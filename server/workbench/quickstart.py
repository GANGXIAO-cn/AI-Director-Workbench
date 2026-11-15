"""Quick kickoff and story directions, using the existing continuous run and executors."""
import json
import re
from .db import connect, transaction, dumps, new_id
from .errors import Rejected, Conflict, row
from .projectops import bigrams
from .sourcefiles import production, read_named
from .specs import validate_spec
from .methodpack import load_pack, method_entries
from .executors.base import ExecRequest
from . import workflow

DIRECTION_CONTRACT = '''先构思事件，再用机制复核。判断用户是否已经给出了完整的起因、变化和结局，不按字数判断。
reply 必须为 JSON 对象：completeStory（布尔）、directions（数组）。完整故事只整理成一个 recommended=true 的方向，保留原话事件和走向；否则提出三个主要事件或结尾明显不同的方向。
每项：key（A/B/C）、title（10字内）、premise（一句前提）、story（150–300字完整起因变化兑现）、hook、ending、difference（与最近几集不同）、mechanisms（编号数组，可空）、sources（仅输入中的选题编号或知识标题，可空）、recommended（布尔，恰一项true）、reason。各文本非空。不编造来源。未提供机制库时明确注明“未提供机制库”。'''


def validate_directions(value, sources, idea=''):
    if not isinstance(value, dict) or type(value.get('completeStory')) is not bool:
        raise Rejected('方向回复缺少 completeStory 布尔值')
    items=value.get('directions')
    if not isinstance(items,list) or len(items)!=(1 if value['completeStory'] else 3):
        raise Rejected('完整故事须有一个方向，否则须有三个方向')
    keys=[]
    for item in items:
        if not isinstance(item,dict): raise Rejected('方向须为对象')
        if any(not isinstance(item.get(k),str) or not item[k].strip() for k in ('key','title','premise','story','hook','ending','difference','reason')):
            raise Rejected('方向文字字段不能为空')
        keys.append(item['key'])
        if item['key'] not in ('A','B','C') or len(item['title'])>10 or not 150<=len(item['story'])<=300:
            raise Rejected('方向编号、标题或故事长度不合格')
        if type(item.get('recommended')) is not bool: raise Rejected('recommended 须为布尔值')
        for field in ('sources','mechanisms'):
            if not isinstance(item.get(field),list) or any(not isinstance(v,str) for v in item[field]):
                raise Rejected(field+' 须为字符串数组')
        # 实测 112：想法原文里写明的编号（如粘贴选题卡中的“选题编号”）也是输入中的来源。
        if any(v not in sources and not (v.strip() and v.strip() in idea) for v in item['sources']): raise Rejected('方向引用了输入中不存在的来源')
    if len(set(keys))!=len(keys) or sum(v['recommended'] for v in items)!=1:
        raise Rejected('方向编号不可重复，且恰有一个推荐')
    return items


class QuickStartMixin:
    def similar_topics(self, project_id, text, limit=5):
        candidates=[dict(code=t['code'],title=t['title'],premise=t['premise'],mechanisms=t.get('mechanisms',[])) for t in self.source_topics(project_id)]
        with connect(self.db_path) as db:
            candidates += [dict(code=t['code'],title=t['code'],premise=t['premise'],mechanisms=[t['mechanism']] if t['mechanism'] else []) for t in db.execute('SELECT * FROM topic WHERE project_id=?',(project_id,))]
        query=bigrams(text); result={}
        for item in candidates:
            terms=bigrams(item['premise']);score=len(query & terms)/max(1,len(query | terms))
            if score:
                result[(item['code'],item['premise'])]=dict(item,score=round(score,3))
        return sorted(result.values(),key=lambda v:(-v['score'],v['code']))[:limit]

    def quick_start_prepare(self, project_id, idea, command_id, selections=None, files=()):
        from .attachments import prepare
        selected=dict(selections or {})
        if not isinstance(idea,str): raise Rejected('想法须为文字')
        _,pictures=prepare(files,images_only=True)
        frozen=[dict(name=name,sha256=self.store.put_blob(raw)) for name,raw in files]
        plan=selected.get('executor_plan',selected.get('executorPlan',self.executor_defaults()))
        plan={**self.executor_defaults(),**plan}
        for role in ('create','review','expert'):
            slot=plan.get(role,{})
            if not slot.get('provider'): raise Rejected('缺少开工默认项：executor_plan.'+role)
            self.validate_selection(slot['provider'],slot.get('model'),slot.get('effort'))
        topic=selected.get('topicId',selected.get('topic_id'))
        imported=selected.get('source_import_id',selected.get('importId'))
        if topic and imported: raise Rejected('选题卡与导入原文只能选择一种来源')
        if not (idea.strip() or topic or imported): raise Rejected('请输入想法或选择选题、原文')
        with transaction(self.db_path) as db:
            project=row(db,'SELECT * FROM project WHERE id=?',project_id)
            preset_name=selected.get('preset_name','default')
            preset=self._project_presets(db,project_id).get(preset_name)
            if not preset: raise Rejected('缺少开工默认项：规格预设 '+preset_name)
            overrides=selected.get('spec_overrides',selected.get('specOverrides',{}))
            spec=dict(preset['values'],**overrides)
            if not spec.get('aspectRatio'): raise Rejected('缺少开工默认项：aspectRatio')
            validate_spec(spec)
            def create():
                numbers=[int(m[1]) for r in db.execute('SELECT display_code FROM episode WHERE project_id=?',(project_id,)) if (m:=re.fullmatch(r'E(\d+)',r[0]))]
                code=f'E{max(numbers,default=0)+1:02d}';eid=new_id()
                db.execute('INSERT INTO episode(id,project_id,display_code,title,leftover_facts,created_at) VALUES(?,?,?,\'\',\'[]\',?)',(eid,project_id,code,self.clock()))
                args=dict(episode_id=eid,source_type='import' if imported else 'topic' if topic else 'idea',
                          source_import_id=imported,topic_id=topic,idea_text=None if imported or topic else idea,
                          goal=selected.get('goal',idea if imported or topic else ''),executor_plan=plan,
                          cooperation_mode=selected.get('cooperation_mode','advanced'),spec_overrides=overrides,
                          preset_name=preset_name,preset_revision=selected.get('preset_revision'),
                          palette_ids=selected.get('palette_ids',selected.get('paletteIds',[])),
                          source_intent=selected.get('source_intent','adapt'),relation=selected.get('relation','independent'),
                          predecessor_ids=selected.get('predecessor_ids',[]),leftover_facts=selected.get('leftover_facts',[]),
                          start_story=False)
                cid=self._start_continuous_prekickoff(eid,args,db=db,frozen_files=frozen,image_options=selected.get('image_options',selected.get('imageOptions')))
                return dict(episodeId=eid,continuousId=cid)
            return self._command(db,command_id,'quick_start',dict(project=project_id,idea=idea,selections=selected,pictures=pictures),create)

    def _start_continuous_prekickoff(self, episode_id, start_args, db=None, frozen_files=(), image_options=None):
        if db is None:
            with transaction(self.db_path) as connection:
                return self._start_continuous_prekickoff(episode_id,start_args,db=connection,frozen_files=frozen_files,image_options=image_options)
        cid=new_id()
        cursor=dict(imageOptions=image_options or {},phase='kickoff',startArgs=start_args,frozenFiles=list(frozen_files),base={},attempts={},rounds={},stressReports=0,queue=[],ownedRuns=[],userStamp=[],enableDirections=True)
        db.execute('INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,created_at,updated_at) VALUES(?,?,\'delivery\',\'running\',?,?,?)',(cid,episode_id,dumps(cursor),self.clock(),self.clock()))
        return cid

    def quick_start(self, project_id, idea, command_id, selections=None, files=()):
        result=self.quick_start_prepare(project_id,idea,command_id,selections,files)
        if self._continuous_record(result['continuousId'])['state']=='running': self._advance_continuous(result['continuousId'])
        return result

    def _kickoff_source(self, args):
        with connect(self.db_path) as db:ep=self._episode_in(db,args['episode_id'])
        return dict(project_id=ep['project_id'],episode_id=ep['id'],**{k:args.get(k) for k in ('source_type','idea_text','goal','source_import_id','topic_id')})

    def _continuous_kickoff(self,cid):
        value=self._continuous_record(cid);cursor=value['cursor'];args=cursor['startArgs'];source=self._kickoff_source(args)
        check=self.kickoff_check_view(source)
        if check['settings']:
            slot=args['executor_plan']['create']
            # 实测 111：上次核对失败时换新的核对令牌，导演重新登录后点“继续”会真正重跑核对。
            retry=cursor.setdefault('attempts',{}).get('kickoff',0)
            if check['state']=='failed':
                retry+=1;cursor['attempts']['kickoff']=retry;self._continuous_update(cid,cursor=cursor)
            if retry:slot=args['executor_plan'].get('expert') or slot
            check=self.kickoff_check_run(source,check['basis'],slot['provider'],slot.get('model'),slot.get('effort'),cid+':kickoff:'+check['basis']+(f':{retry}' if retry else ''))
            self._continuous_checkpoint(cid)  # The director may have cancelled during the external call.
            if check['state']!='done':
                external=self._run_blocks(dict(check,role='kickoff'))
                if external:self._continuous_problem(cid,check['error'] or '开工核对等待通道',blocks=external)
                if check['state']=='running':
                    self._continuous_problem(cid,'开工核对正在执行',blocks=[self._recovery_block('run:kickoff:check','transient',role='kickoff')])
                if retry<2:
                    from .continuous import UserChange
                    raise UserChange()
                # A malformed optional comparison cannot veto a story: retain all
                # project rules in the actual kickoff snapshot and story instructions.
                self._continuous_log(cid,'story','decide_skip',check['basis'],'核对回包不可用，起草时沿用全部项目必守设定',dict(error=check['error']))

            conflicts=[]
            for item in check['checks']:
                conflict=item['verdict']=='conflict'
                if item['requestQuote'].strip():self.kickoff_check_decide(source,check['basis'],item['key'],item['requestQuote'],conflict)
                self._continuous_log(cid,'story','decide_skip',check['basis'],'开工检查：'+item['key']+' 判断为'+('冲突' if conflict else '不冲突'),dict(check=item,conflict=conflict))
                if conflict:conflicts.append(item)
            if conflicts:
                self._adjust_kickoff(cid,args,cursor,check,conflicts)
                return self._continuous_kickoff(cid)
        with connect(self.db_path) as db:started=db.execute('SELECT 1 FROM kickoff WHERE episode_id=?',(value['episode_id'],)).fetchone()
        if not started:
            files=[(f['name'],self.store.read_blob(f['sha256'])) for f in cursor.get('frozenFiles',[])]
            self.start_episode(**args,files=files,command_id=cid+':start')
        cursor.update(phase='direction',userStamp=[],base={})
        self._continuous_update(cid,cursor=cursor,choice=None)
        if cursor.get('originalRequest'):
            self.add_decision(value['episode_id'],'按设定调整后的想法：'+args['idea_text']+'。出处原话（不再作为约束）：'+cursor['originalRequest'],'story','episode',[],cid+':original-request')
        return True

    def _direction_inputs(self,eid):
        with connect(self.db_path) as db:
            kickoff=self._kickoff(db,eid);ep=self._episode_in(db,eid);pid=ep['project_id']
            idea=self._source_text(db,kickoff)
            recent=[dict(code=r['display_code'],date=r['created_at'],story=json.loads(r['content']).get('synopsis','')) for r in db.execute("SELECT e.display_code,e.created_at,v.content FROM episode e JOIN artifact a ON a.episode_id=e.id AND a.stage='story' JOIN version v ON v.id=a.adopted_version_id WHERE e.project_id=? AND e.id<>? ORDER BY e.created_at DESC,e.rowid DESC LIMIT 5",(pid,eid))]
            prod=production(db,pid);ledger=read_named(prod,'topicLedger');mechanism=read_named(prod,'mechanismLibrary')
        series=[]
        if ledger:
            document=json.loads(ledger[1]);series=[{k:v for k,v in r.items() if k in ('id','title','summary','synopsis','core_desire','comedy_mechanism','ending','recency_basis','evidence_scope')} for r in document.get('episodes',[])[-5:]]
        topics=self.similar_topics(pid,idea)
        knowledge=[r for r in self.similar(pid,idea,10) if r['kind']=='knowledge'][:5]
        return dict(idea=idea,recent=recent or '无',series=series or '无',seriesOrdering=json.loads(ledger[1]).get('ordering_note','原资料顺序') if ledger else '无',topics=topics,knowledge=knowledge,mechanisms=mechanism[1].decode() if mechanism else '未提供机制库')

    def _direction_run(self,cid,context,instruction):
        value=self._continuous_checkpoint(cid);eid=value['episode_id'];cursor=value['cursor']
        if cursor.get('directionRunId'):
            rid=cursor['directionRunId'];self._continuous_execute(cid,rid)
            return json.loads((self.store.run_dir(self._continuous_record(cid)['cursor']['runId'])/'result.json').read_text())['reply']
        with transaction(self.db_path) as db:
            ep=self._episode_in(db,eid);art=row(db,"SELECT * FROM artifact WHERE episode_id=? AND stage='story'",eid)
            kickoff=self._kickoff(db,eid);plan={**kickoff['executor_plan'],**self._stage_executors(db,eid,'story')}
            plan['create']=self.episode_expert(eid)
            tid,thread=self._draft_thread(db,ep['project_id'],eid,art['id'],'story',instruction,plan)
        for hit in context['knowledge']:
            self.cite(tid,hit['id'],'AI 自动检索：与想法相似',cid+':cite:'+tid+':'+hit['id'])
        with transaction(self.db_path) as db:
            pack,profile=self._episode_method(db,eid)
            entries=method_entries(pack,'topic',profile)+[dict(role='direction_context',name='想法、选题、最近故事与机制',text=dumps(context))]
            from .attachments import prepare
            attachments,_=prepare([(f['name'],self.store.read_blob(f['sha256'])) for f in cursor.get('feedbackFiles',[])])
            entries.extend(attachments)
            rid=self._queue_run(db,tid,'create',None,instruction,message_entries=entries,workspace_revision=art['revision'],discussion_only=True,extra_header=dict(direction=True))
        self._continuous_cursor(cid,directionTaskId=tid,directionRunId=rid)
        self._continuous_execute(cid,rid)
        return json.loads((self.store.run_dir(self._continuous_record(cid)['cursor']['runId'])/'result.json').read_text())['reply']

    def _continuous_direction(self,cid):
        value=self._continuous_record(cid);cursor=value['cursor'];eid=value['episode_id']
        with connect(self.db_path) as db:kickoff=self._kickoff(db,eid)
        art=self._continuous_art(eid,'story')
        if kickoff['source_type']=='import' or art and art['adopted_version_id']:
            self._continuous_cursor(cid,directionDone=True)
            self._continuous_log(cid,'story','decide_skip',eid,'导入稿或已采用故事直接进入制作',{})
            return True
        context=self._direction_inputs(eid)
        if cursor.get('directionFeedback'):
            context.update(feedback=cursor['directionFeedback'],previousDirections=cursor.get('previousDirections',[]))
        for attempt in range(2):
            try:
                reply=self._direction_run(cid,context,DIRECTION_CONTRACT+('\n上次格式问题：'+error if attempt else ''))
                answer=json.loads(reply);items=validate_directions(answer,{t['code'] for t in context['topics']}|{k['title'] for k in context['knowledge']},context['idea'] if isinstance(context['idea'],str) else '')
                break
            except (ValueError,Rejected) as exc:
                error=str(exc)
                self._continuous_cursor(cid,directionRunId=None)
                if attempt:
                    self._continuous_cursor(cid,directionDone=True,storyGoal=workflow.STANDARD_GOALS['story']+'\n直接按已给选题及开工要求起草：'+context['idea'])
                    self._continuous_log(cid,'story','decide_skip',eid,'方向候选格式无效，沿用原选题起草',dict(reason=error))
                    return True
        if answer['completeStory']:
            self._continuous_cursor(cid,directionDone=True,storyGoal=workflow.STANDARD_GOALS['story']+'\n按用户完整故事走向起草，不改事件：\n'+items[0]['story']+'\n用户原话：\n'+context['idea'])
            self._continuous_log(cid,'story','decide_skip',eid,'想法已有完整故事走向，直接起草',dict(direction=items[0]))
            return True
        item=next(i for i in items if i['recommended'])
        self._continuous_log(cid,'story','choose_direction',item['key'],item['reason'],item)
        self._continuous_cursor(cid,directionDone=True,storyGoal=workflow.STANDARD_GOALS['story']+'\n按专家选定的方向写：\n'+item['story'])
        return True

    def _adjust_kickoff(self,cid,args,cursor,check,conflicts):
        old=check['request'];slot=args['executor_plan'].get('expert') or self.executor_defaults()['expert']
        new=None
        if not cursor.get('kickoffAdjustment'):
            connection=self.run_connection(slot['provider'],slot.get('model'),slot.get('effort'))
            rid=new_id();work=self.store.run_dir(rid)/'work';work.mkdir(parents=True)
            prompt='只改写与必守设定冲突的部分，保留原事件、时长和交付要求。返回 JSON 的 idea 字段。\n'+dumps(dict(idea=old,conflicts=conflicts))
            schema=dict(type='object',additionalProperties=False,required=['idea'],properties=dict(idea=dict(type='string')))
            outcome=self.executors[slot['provider']].start(ExecRequest(rid,'kickoff_adjust','kickoff_adjust',prompt,schema,'ephemeral',work,connection['model'],connection['effort'],connection=connection))
            self._continuous_checkpoint(cid)
            if outcome.ok and isinstance(outcome.structured,dict):new=outcome.structured.get('idea')
        if not isinstance(new,str) or not new.strip() or new==old:
            new=old
            for item in conflicts:
                new=new.replace(item['requestQuote'],'',1)
            new=new.strip()+'\n遵守项目设定：'+'；'.join(check['settings'][item['key']] for item in conflicts)
        cursor.setdefault('originalRequest',old)
        cursor['kickoffAdjustment']=dict(before=old,after=new)
        count=cursor.setdefault('attempts',{}).get('kickoffAdjustment',0)+1
        cursor['attempts']['kickoffAdjustment']=count
        if count>2:raise RuntimeError('必守设定核对在定向改写后仍返回明确冲突：'+dumps(conflicts))
        args.update(source_type='idea',idea_text=new,topic_id=None,source_import_id=None)
        self._continuous_update(cid,cursor=cursor,choice=None,reason=None)
