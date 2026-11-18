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

DIRECTION_CONTRACT = """你是本集写稿人。先读懂创意、选题卡和导演要求。分析本集最值得看的是什么，
主角主动做什么、哪一处因果最薄弱。提出两到三种实质不同的讲法，说明人物选择、事件怎样升级、
开场的看点如何在结尾兑现。保留导演明确指定的走向，仍可从叙事重心、信息揭示或表现方式提出不同讲法。
选题卡写得完整也要提案。推荐一种并解释取舍。此轮只交创作提案，先不写正文。
discussion 的 reply 填一个 JSON 对象：{"analysis":"本集具体创作判断","directions":[
{"key":"A","title":"讲法名","story":"具体起因、关键选择及结尾"}],"recommendation":"A","reason":"为什么更适合本集"}。
每个选项使用不同的 A/B/C 编号。"""

EXPERT_DIRECTION = """你是本集定案专家。阅读原始创意、项目约束和写稿人的完整提案。
判断何种讲法最能兑现看点，检查关键因果和角色动机。你可以采纳推荐、另选或组合。
直接向写稿人给出明确的定案：选哪种、理由、保留哪些事件、具体怎么解决薄弱处。
若提案有缺漏，你负责补足并定案。不要向用户提问。discussion 的 reply 用自然语言写最终写作指令。"""


def read_proposal(reply):
    value = json.loads(reply)
    if not isinstance(value, dict):
        raise ValueError("创作提案须为对象")
    for key in ("analysis", "reason", "recommendation"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError("提案须包含本集分析、推荐方向和理由")
    items = value.get("directions")
    if not isinstance(items, list) or len(items) not in (2, 3):
        raise ValueError("请提出两到三种不同讲法")
    keys = []
    for item in items:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                             for k in ("key", "title", "story")):
            raise ValueError("每个讲法须有编号、标题、具体事件和结尾")
        keys.append(item["key"])
    if len(set(keys)) != len(keys) or value["recommendation"] not in keys:
        raise ValueError("讲法编号须唯一且推荐其中一种")
    options = [i["key"] + " · " + i["title"] for i in items]
    text = "\n\n".join([value["analysis"], *[f"**{o}**\n{i['story']}" for o,i in zip(options,items)],
                        f"**写稿人推荐 {value['recommendation']}**　{value['reason']}"])
    return text, options


class QuickStartMixin:
    def similar_topics(self, project_id, text, limit=5):
        with connect(self.db_path) as db:
            candidates = [dict(code=t['code'],title=t['code'],premise=t['premise'],mechanisms=[t['mechanism']] if t['mechanism'] else []) for t in db.execute('SELECT * FROM topic_comparisons WHERE project_id=?',(project_id,))]
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
            recent=[dict(r) for r in db.execute("""
                SELECT t.code,t.premise story,min(v.published_at) date
                FROM research_topics t JOIN topic_publication v ON v.topic_id=t.id
                WHERE t.project_id=? GROUP BY t.id ORDER BY date DESC,t.code LIMIT 5
            """,(pid,))]
            allowed_knowledge={r[0] for r in db.execute("SELECT id FROM knowledge WHERE project_id=? AND episode_id IS NULL AND status='readable'",(pid,))}
            mechanism=read_named(production(db,pid),'mechanismLibrary')
        return dict(idea=idea,recent=recent or '无',series='无',seriesOrdering='按已核实的首次发布时间',
                    topics=self.similar_topics(pid,idea),knowledge=[r for r in self.similar(pid,idea,10) if r['kind']=='knowledge' and r['id'] in allowed_knowledge][:5],mechanisms=mechanism[1].decode() if mechanism else '未提供机制库')

    def _direction_run(self,cid,context,instruction,expert=False):
        value=self._continuous_checkpoint(cid);eid=value['episode_id'];cursor=value['cursor']
        run_key = 'directionDecisionRunId' if expert else 'directionRunId'
        if cursor.get(run_key):
            with connect(self.db_path) as db:
                latest = db.execute("SELECT id,status FROM run WHERE task_id=(SELECT task_id FROM run WHERE id=?) "
                                    "ORDER BY created_at DESC,rowid DESC LIMIT 1",(cursor[run_key],)).fetchone()
            if latest['status']=='succeeded':
                return json.loads((self.store.run_dir(latest['id'])/'result.json').read_text())['reply']
            self._continuous_execute(cid,latest['id'])
            return json.loads((self.store.run_dir(self._continuous_record(cid)['cursor']['runId'])/'result.json').read_text())['reply']
        with transaction(self.db_path) as db:
            ep=self._episode_in(db,eid);art=row(db,"SELECT * FROM artifact WHERE episode_id=? AND stage='story'",eid)
            kickoff=self._kickoff(db,eid);plan={**kickoff['executor_plan'],**self._stage_executors(db,eid,'story')}
            if expert:
                plan['create']=self.episode_expert(eid)
            label = '专家定案并交给写稿人' if expert else '写稿人分析创意并提出讲法'
            tid,thread=self._draft_thread(db,ep['project_id'],eid,art['id'],'story',label,plan)
        for hit in ([] if expert else context['knowledge']):
            self.cite(tid,hit['id'],'AI 自动检索：与想法相似',cid+':cite:'+tid+':'+hit['id'])
        with transaction(self.db_path) as db:
            pack,profile=self._episode_method(db,eid)
            entries=method_entries(pack,'topic',profile)+[dict(role='direction_context',name='想法、选题、最近故事与机制',text=dumps(context))]
            from .attachments import prepare
            attachments,_=prepare([(f['name'],self.store.read_blob(f['sha256'])) for f in cursor.get('feedbackFiles',[])])
            entries.extend(attachments)
            rid=self._queue_run(db,tid,'create',None,instruction,message_entries=entries,workspace_revision=art['revision'],discussion_only=True,extra_header=dict(direction=True, writerProposal=not expert))
            db.execute('UPDATE continuous_run SET cursor=json_set(cursor,?,?) WHERE id=?',('$.'+run_key,rid,cid))
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
        proposal = cursor.get("writerProposal")
        if not proposal:
            proposal = self._direction_run(cid,context,DIRECTION_CONTRACT)
            try:
                proposal, _ = read_proposal(proposal)
            except ValueError:
                # A prose proposal is still usable by the expert.
                pass
            self._continuous_cursor(cid,writerProposal=proposal)
        decision = self._direction_run(cid,dict(context,writerProposal=proposal),EXPERT_DIRECTION,expert=True)
        goal = workflow.STANDARD_GOALS['story'] + '\n用户原话：\n' + context['idea'] + "\n依照以下专家定案写正文：\n" + decision + "\n写稿人原提案：\n" + proposal
        self._continuous_log(cid,'story','choose_direction',eid,decision,dict(proposal=proposal,decision=decision))
        self._continuous_cursor(cid,directionDone=True,storyGoal=goal)
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
