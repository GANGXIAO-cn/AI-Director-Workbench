"""Continuous 07 orchestration; generated versions and batch receipts are retained."""
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from .db import connect,transaction,dumps,new_id
from .errors import Rejected,Conflict,row
from .worksheet import CATEGORIES, asset_product_code
from .progress_expert import ProgressExpert
from .storage import canonical, sha256_bytes
from .images import INTERRUPTED_IMAGE


EDIT_LIMIT=2
local=threading.local()
_LOCKS={}
_LOCKS_GUARD=threading.Lock()


def cursor_lock(cid):
    """同一连续制作的游标读改写串行进行，并行条目不会互相覆盖。"""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cid,threading.RLock())


def parallel_worker():
    return getattr(local,'parallel',False)


class MaterialWait(Exception):
    def __init__(self, reason, running=False, kind=None, provider=None, role=None, batchIds=(), resetsAt=None):
        self.reason=reason; self.running=running
        self.kind=kind; self.provider=provider; self.role=role
        self.batchIds=list(batchIds); self.resetsAt=resetsAt

    def waiting(self):
        return dict(reason=self.reason,running=self.running,kind=self.kind,provider=self.provider,
                    role=self.role,batchIds=self.batchIds,resetsAt=self.resetsAt)


def generated_count(attempts):
    return sum(1 for x in attempts if not x.get('edit') and not x.get('interrupted') and x.get('blockedBy')!='auth')



def usable(assessment,accepted=False):
    return accepted or assessment and (assessment['verdict']=='usable' or assessment['verdict']=='limited' and not assessment['critical'])


class ContinuousImages(ProgressExpert):
    def _image_ratio(self,eid):
        with connect(self.db_path) as db:return self._kickoff(db,eid)['spec']['aspectRatio']

    def _image_options(self,eid,selected):
        channels=self.image_providers();selected=selected or {}
        provider=selected.get('provider') if selected.get('provider') in channels else next(iter(channels),None)
        if provider not in channels:raise Rejected('未配置的图像通道：'+str(provider))
        caps=channels[provider]['capabilities'];models=caps.get('models') or []
        model=selected.get('model') if selected.get('model') in models else (models[0] if models else None)
        if models and model not in models:raise Rejected('所选图像型号不再可用')
        with connect(self.db_path) as db:ratio=self._kickoff(db,eid)['spec']['aspectRatio']
        a,b=(float(v) for v in ratio.split(':'));target=a/b
        sizes=caps.get('sizes') or []
        def distance(size):
            try:w,h=(float(v) for v in size.split('x'));return abs(math.log(w/h/target))
            except (ValueError,ZeroDivisionError):return float('inf')
        size=min(sizes,key=distance) if sizes else None
        if not size:raise Rejected('图像通道没有可用尺寸')
        qualities=caps.get('quality') or ['auto']
        quality=selected.get('quality') if selected.get('quality') in qualities else next((q for q in ('auto','high') if q in qualities),qualities[0])
        return dict(provider=provider,model=model,size=size,quality=quality)

    def _image_state(self,cid,key,**changes):
        with cursor_lock(cid):
            cursor=self._continuous_record(cid)['cursor'];states=cursor.setdefault('images',{})
            state=states.setdefault(key,dict(attempts=[],batchIds=[],requestIds=[]))
            state.update(changes);self._continuous_update(cid,cursor=cursor)
            return state

    def _need(self,eid,key):
        return next(r for r in self.asset_worksheet(eid)['rows'] if r['need_key']==key)

    def _image_assess_need(self,cid,need):
        eid=need['episode_id'];key=need['need_key']
        assessed={r['version_id']:r['body'] for r in self.image_assessments(eid,key)}
        state=self._image_state(cid,key)
        ids=[v for v in dict.fromkeys(need['candidates']) if v not in state.get('retiredCandidates',[])]
        if not ids:return []
        # 实测 135：评估只看条目用途。本流程自己出图引起的版本号变化不改变用途，
        # 已评过的候选沿用原结论，只评新图；导演改条目或返修入口会把 assessedRevision 清空，届时全部重评。
        basis=dumps({k:need.get(k) for k in ('title','description','kind','role_id','state_id','scene_ids')})
        reuse=state.get('assessedRevision') is not None and (state['assessedRevision']==need['revision'] or state.get('assessedBasis')==basis)
        ranked=[v for v in state.get('rankedCandidates',[]) if v in ids and v in assessed] if reuse else []
        fresh=[v for v in ids if v not in ranked]
        # 每次最多四张实图：新图两两一组，只带上目前最好的两张“可用/有限”图做比较，不再反复带已判不可用的图。
        for offset in range(0,len(fresh),2):
            keep=[v for v in ranked if assessed[v]['verdict']!='blocked'][:2]
            group=[*keep,*fresh[offset:offset+2]]
            tag=sha256_bytes(canonical(group))[:12]
            values=self.assess_images(eid,group,f'{cid}:assess:{key}:{need["revision"]}:{tag}',need_key=key,continuous_id=cid)
            assessed.update({v['versionId']:v for v in values})
            ordered=sorted(values,key=lambda v:('usable','limited','blocked').index(v['verdict']))
            compared=[v['versionId'] for v in ordered]
            ranked=[*compared,*[v for v in ranked if v not in compared]]
        ranked=sorted(ranked,key=lambda v:('usable','limited','blocked').index(assessed[v]['verdict']))
        self._image_state(cid,key,assessedRevision=need['revision'],assessedBasis=basis,assessedCandidates=ids,rankedCandidates=ranked)
        return [assessed[v] for v in ranked]

    def _image_fixed_role(self,cid,need):
        """实测 122：项目已登记固定身份图的角色，直接采用登记原图，不再出图或等导演选图。"""
        if CATEGORIES.get(need['kind'],need['kind'])!='identity' or not need.get('role_id'):return False
        with connect(self.db_path) as db:pid=self._episode_in(db,need['episode_id'])['project_id']
        try:roles={r['id'] for r in self.project_role_assets(pid)['roles']}
        except Rejected:return False
        if need['role_id'] not in roles:return False
        eid=need['episode_id'];key=need['need_key']
        vid=self.use_fixed_role(eid,need['role_id'],f'{cid}:fixed-role:{key}')['versionId']
        with transaction(self.db_path) as db:
            db.execute('INSERT OR IGNORE INTO asset_need_candidate VALUES(?,?)',(need['id'],vid))
        need=self._need(eid,key)
        self.set_asset_need(eid,key,need['revision'],'reuse',f'{cid}:fixed-role-reuse:{key}:{vid}',version_id=vid,reason='复用项目固定身份图')
        self._continuous_log(cid,'assets','choose_image',vid,'项目已登记固定身份图，直接采用',dict(needKey=key,role=need['role_id']))
        return True

    def _image_library_candidates(self,cid,need):
        eid=need['episode_id'];key=need['need_key'];state=self._image_state(cid,key)
        if state.get('libraryChecked'):return
        with connect(self.db_path) as db:pid=self._episode_in(db,eid)['project_id']
        category=CATEGORIES.get(need['kind'],need['kind'])
        candidates=[r for r in self.library(pid) if r['status'] in ('approved','limited') and r['category']==category and (category!='identity' or need['role_id'] in r.get('roles',[]))]
        # 实测 135：同类库图常有几十张且多与本条目无关（洗衣机条目拉来爆米花、洗车工具），逐张看图评估很费时。
        # 先按库图名称与用途说明挑出确实可能适用的，没有就直接出图。
        candidates=self._library_pick(cid,need,candidates)
        mapping={}
        with transaction(self.db_path) as db:
            for item in candidates:
                copied=self._copy_library_image(db,item['id'],eid)['versionId']
                db.execute('INSERT OR IGNORE INTO asset_need_candidate VALUES(?,?)',(need['id'],copied));mapping[copied]=item['id']
        self._image_state(cid,key,libraryChecked=True,libraryEntries=mapping)

    def _image_generate(self,cid,need,edit=None):
        eid=need['episode_id'];key=need['need_key'];state=self._image_state(cid,key)
        # A saved batch receipt takes precedence over preparing another request.
        unfinished=state.get('pendingBatch')
        if unfinished:
            batch=self.batch_view(unfinished)
            if batch['state'] in ('failed','partially_succeeded','cancelled'):
                requests=[r for r in batch['requests'] if not r.get('replaced')]
                if not any(r['state']=='succeeded' for r in requests):
                    attempts=state['attempts'];attempt=attempts[-1]
                    errors=[r.get('error') or r['state'] for r in requests]
                    interrupted=bool(errors) and all(e==INTERRUPTED_IMAGE for e in errors)
                    auth=batch['provider']=='codex-native' and any('本机Codex尚未登录' in e for e in errors)
                    if interrupted and state.get('interruptionRetries',0)>=3:
                        raise MaterialWait('连续 3 次中断恢复后仍未取得图片，请检查出图连接后点继续')
                    attempt['result']='被打断，未出图' if interrupted else '出图失败：'+'；'.join(dict.fromkeys(errors))
                    if interrupted:attempt['interrupted']=True
                    if auth:attempt['blockedBy']='auth'
                    changes=dict(pendingBatch=None,attempts=attempts)
                    if interrupted:changes['interruptionRetries']=state.get('interruptionRetries',0)+1
                    if interrupted and attempt.get('edit'):changes['edits']=max(0,state.get('edits',0)-1)
                    self._image_state(cid,key,**changes)
                    if auth:raise MaterialWait('本机Codex尚未登录',kind='auth',provider='codex',role='image')
                    if interrupted and not attempt.get('edit'):
                        self._continuous_log(cid,'assets','regenerate_image',batch['id'],'上一张被打断未出图，按当前通道重出',dict(needKey=key))
                    return self._need(eid,key)
        else:
            n=len(state['attempts'])+1   # 命令号含局部修图次数，保证不与旧批次重复
            generated=generated_count(state['attempts'])+1
            prior=self.image_assessments(eid,key)
            evidence=[r['body'] for r in prior]
            prompt=need['image_prompt'] if not state.get('basePrompt') else state['basePrompt']
            if not str(prompt or '').strip():
                # 实测 122：说清是哪一项、为什么不能出图、导演怎么处理，而不是只给“请填写出图提示词”。
                raise MaterialWait(f'素材「{need["title"]}」缺少出图提示词')
            if edit:
                method='局部修图'
                prompt=edit['prompt']
                self.set_asset_need(eid,key,need['revision'],'generate',f'{cid}:edit:{key}:{state.get("epoch",0)}:{n}',prompt=prompt,version_id=edit['versionId'])
            else:
                method='首次生成' if generated==1 else '常规重出' if generated==2 else '简化同时出现的角色与动作'
                if generated>=2:prompt+='\n上一轮实际评估，修正指出的问题：'+dumps(evidence)
                if generated>=3:prompt+='\n本轮换方法：保留剧本用途和必要关系，简化同时出现的角色与动作，以清晰单图呈现；不得重复上一轮复杂构图。'
                self.set_asset_need(eid,key,need['revision'],'generate',f'{cid}:generate:{key}:{state.get("epoch",0)}:{n}',prompt=prompt)
            need=self._need(eid,key)
            with connect(self.db_path) as db:
                design=json.loads(row(db,'SELECT content FROM version WHERE id=?',need['source_version_id'])['content'])
            product=next((asset_product_code(item) for item in design['assets'] if item['id']==key),None)
            if product:self.use_project_product(eid,product,f'{cid}:product:{product}')
            options=self._image_options(eid,self._image_channel(cid,eid))
            created=self.prepare_asset_batch(eid,{key:need['revision']},command_id=f'{cid}:batch:{key}:{state.get("epoch",0)}:{n}',**options)
            batch=self.batch_view(created['batchId'])
            attempts=[*state['attempts'],dict(n=n,method=method,result='待回执',**({'edit':True,'base':edit['versionId']} if edit else {}))]
            self._image_state(cid,key,pendingBatch=batch['id'],needRevision=need['revision'],attempts=attempts,batchIds=[*state['batchIds'],batch['id']],requestIds=[*state['requestIds'],*[r['id'] for r in batch['requests']]])
            if n>1:self._continuous_log(cid,'assets','regenerate_image',batch['id'],method,dict(needKey=key,attempt=n,**({'edit':edit['instruction'],'base':edit['versionId']} if edit else {})))
        self._continuous_checkpoint(cid)
        if not batch['authorization']:
            self.authorize_batches(eid,{batch['id']:batch['summary_hash']},f'{cid}:authorize:{batch["id"]}')
            batch=self.batch_view(batch['id'])
        if batch['state'] in ('authorized','partial'):self.execute_batch(batch['id'])
        batch=self.batch_view(batch['id'])
        if batch['state']!='completed' and not (batch['state']=='partially_succeeded' and any(r['state']=='succeeded' and not r.get('replaced') for r in batch['requests'])):
            with connect(self.db_path) as db:
                running=bool(db.execute("SELECT 1 FROM image_request WHERE batch_id=? AND state='submitted'",(batch['id'],)).fetchone())
            if batch['state'] in ('failed','partially_succeeded','cancelled'):
                return self._image_generate(cid,self._need(eid,key))
            raise MaterialWait('出图请求尚未完成，请核对批次：'+batch['id'],running=running,
                               kind='image_wait',role='image',batchIds=[batch['id']])
        current=self._need(eid,key);state=self._image_state(cid,key)
        attempts=state['attempts'];attempts[-1]['result']='候选已保存'
        self._image_state(cid,key,pendingBatch=None,attempts=attempts,interruptionRetries=0)
        self._continuous_checkpoint(cid)
        if current['revision']!=state['needRevision']+1:
            self._image_state(cid,key,assessedRevision=None)
            self._continuous_log(cid,'assets','apply_user_change',key,'条目提交后已修改，结果仅保存为候选，按当前用途重新评估',dict(submittedRevision=state['needRevision'],currentRevision=current['revision']))
        return current

    def _image_choose(self,cid,need,assessment,user=False,accept=False):
        eid=need['episode_id'];key=need['need_key'];vid=assessment['versionId']
        if accept and not user:raise Rejected('只有用户可以接受图片严重问题')
        state=self._image_state(cid,key);entry=state.get('libraryEntries',{}).get(vid)
        if entry:
            self.set_asset_need(eid,key,need['revision'],'reuse',f'{cid}:reuse:{key}:{vid}',entry_id=entry,reason=assessment['reason'])
        else:self.adopt_asset_needs(eid,{key:dict(versionId=vid,revision=need['revision'])},f'{cid}:image-adopt:{key}:{vid}')
        detail=dict(assessment=assessment,needKey=key)
        if accept:detail['userDirection']=dict(stage='closing_manual_revision',instruction='用这张，保留这个问题',critical=assessment['critical'])
        decision=self._continuous_log(cid,'assets','choose_image',vid,assessment['reason'],detail)
        if accept:
            with transaction(self.db_path) as db:
                for issue in assessment['critical']:
                    db.execute('INSERT OR IGNORE INTO accepted_image_issue VALUES(?,?,?,?,?)',(vid,issue['type']+':'+issue['where'],issue['text'],decision,self.clock()))
        if assessment['recommendRequired']:
            self.mark_image(eid,vid,'required',command_id=f'{cid}:required:{vid}',requirement=dict(reason=assessment['reason'],fitness='limited' if assessment['minor'] or assessment['critical'] else 'pass',limitations='；'.join(i['text'] for i in [*assessment['critical'],*assessment['minor']])))
            with transaction(self.db_path) as db:
                record=row(db,"SELECT d.* FROM required_image_details d JOIN image_choice c ON c.id=d.choice_id WHERE c.episode_id=? AND c.asset_version_id=? AND c.mark='required'",eid,vid)
                body=json.loads(record['details']);body['selectedBy']='user' if user else 'agent'
                db.execute('UPDATE required_image_details SET details=? WHERE choice_id=?',(dumps(body),record['choice_id']))
        with connect(self.db_path) as db:design=json.loads(row(db,'SELECT content FROM version WHERE id=?',need['source_version_id'])['content'])
        product=next((asset_product_code(item) for item in design['assets'] if item['id']==key),None)
        if product:self.use_project_product(eid,product,f'{cid}:product:{product}')
        self._image_state(cid,key,selected=vid)

    def system_image_channel(self):
        channels=self.image_providers()
        saved=self.get_setting('default-image-channel')
        if saved in channels:return dict(provider=saved)
        api='openai-image-api'
        return dict(provider=api if channels.get(api,{}).get('settings',{}).get('keyConfigured') else next(iter(channels),None))

    def save_system_image_channel(self,provider):
        if provider not in self.image_providers_map:raise Rejected('未配置的图像通道：'+str(provider))
        self.put_setting('default-image-channel',provider)
        return dict(provider=provider)

    def episode_image_channel(self,episode_id,options=None):
        """实测 139：本集出图通道与型号。优先导演在备图工作单选定并保存的；没选过时沿用本集最近一次出图的；
        再没有就用显式开工设置，否则使用系统默认通道。尺寸始终按本集画幅自动选。"""
        options=dict(options or {})
        channels=self.image_providers()
        saved=self.get_setting('image-channel:'+episode_id)
        if saved and saved.get('provider') in channels:
            return {**options,'provider':saved['provider'],'model':saved.get('model'),'source':'director'}
        with connect(self.db_path) as db:
            used=db.execute('SELECT provider,model,spec FROM image_batch WHERE episode_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(episode_id,)).fetchone()
        if used and used['provider'] in channels:
            spec=json.loads(used['spec'] or '{}')
            return {**options,'provider':used['provider'],'model':used['model'],'quality':spec.get('quality'),'source':'last_batch'}
        if options.get('provider') in channels:return {**options,'source':'kickoff'}
        return {**options,**self.system_image_channel(),'source':'default'}

    def save_episode_image_channel(self,episode_id,provider,model=None,command_id=None):
        """导演一改就保存；连续制作下一次出图起按新的通道与型号。"""
        channels=self.image_providers()
        if provider not in channels:raise Rejected('未配置的图像通道：'+str(provider))
        models=channels[provider]['capabilities'].get('models') or []
        if models and model not in models:raise Rejected('所选图像型号不可用')
        with connect(self.db_path) as db:self._episode_in(db,episode_id)
        self.put_setting('image-channel:'+episode_id,dict(provider=provider,model=model if models else None))
        return self.episode_image_channel(episode_id)

    def _image_channel(self,cid,eid):
        return self.episode_image_channel(eid,self._continuous_record(cid)['cursor'].get('imageOptions'))

    def _image_parallelism(self,cid):
        eid=self._continuous_record(cid)['episode_id']
        try:provider=self._image_options(eid,self._image_channel(cid,eid))['provider']
        except Rejected:return 1
        channel=getattr(self,'image_providers_map',{}).get(provider)
        return max(1,int(getattr(channel,'max_concurrency',1) or 1))

    def _image_need_round(self,cid,need,parallel=False):
        """一个条目的一轮：复用固定角色或库图，评估现有候选，不合格就出图再评，直到可用或需要停下。返回等待原因或 None。"""
        local.parallel=parallel
        eid=need['episode_id'];key=need['need_key']
        try:
            self._continuous_checkpoint(cid)
            parked=self._image_state(cid,key).get('parkedQueue',[])
            if parked:
                if parallel:
                    for rid in parked:self._continuous_run_direct(cid,rid)
                else:
                    self._continuous_cursor(cid,queue=parked)
                    self._continuous_execute(cid)
                self._image_state(cid,key,parkedQueue=[],executionFailure=None)
            if self._image_fixed_role(cid,need):return None
            self._image_library_candidates(cid,need)
            while True:
                need=self._need(eid,key);assessments=self._image_assess_need(cid,need)
                good=[a for a in assessments if usable(a)]
                if good:
                    self._image_choose(cid,need,good[0]);return None
                state=self._image_state(cid,key)
                if generated_count(state['attempts'])>=2:   # 局部修图另计（EDIT_LIMIT）
                    return dict(reason='连续两次生成仍未解决，需改用局部编辑或其他制作方法',running=False)
                self._image_generate(cid,need)
        except MaterialWait as exc:
            with cursor_lock(cid):
                queued=self._continuous_record(cid)['cursor'].get('queue',[]) if not parallel else []
                if queued:
                    self._image_state(cid,key,parkedQueue=queued)
                    self._continuous_cursor(cid,queue=[])
            return exc.waiting()
        except Rejected as exc:
            # Keep the real error. Independent assets can still complete.
            return dict(reason=str(exc),running=False)
        except Exception as exc:
            from .continuous import Halt
            value=self._continuous_record(cid)
            if parallel or not isinstance(exc,Halt) or value['state'] not in ('blocked','waiting_quota'):raise
            self._image_state(cid,key,parkedQueue=value['cursor'].get('queue',[]),executionFailure=value['reason'])
            self._continuous_cursor(cid,queue=[])
            self._continuous_update(cid,state='running',reason=None)
            blocks=value['cursor'].get('pendingBlocks',[])
            fields={k:blocks[0].get(k) for k in ('kind','provider','role','batchIds','resetsAt')} if blocks else {}
            with cursor_lock(cid):
                cursor=self._continuous_record(cid)['cursor'];cursor.pop('pendingBlocks',None)
                self._continuous_update(cid,cursor=cursor)
            return dict(reason=value['reason'],running=False,**fields)
        finally:
            local.parallel=False

    def _continuous_run_direct(self,cid,rid):
        """并行时每个条目自己执行评估运行，不经过共享运行队列；失败只影响本条目。"""
        retried=False;original_rid=rid
        rid=self._continuous_record(cid)['cursor'].get('directRuns',{}).get(rid,rid)
        while True:
            with cursor_lock(cid):
                cursor=self._continuous_record(cid)['cursor']
                cursor.setdefault('directRuns',{})[original_rid]=rid
                if rid not in cursor['ownedRuns']:
                    cursor['ownedRuns'].append(rid)
                self._continuous_update(cid,cursor=cursor)
            value=self._continuous_checkpoint(cid)
            rid=value['cursor'].get('directRuns',{}).get(original_rid,rid)
            with connect(self.db_path) as db:run=dict(row(db,'SELECT * FROM run WHERE id=?',rid))
            was_queued=run['status']=='queued'
            if was_queued:
                for follow in self.execute_run(rid) or []:self.execute_run(follow)
                with connect(self.db_path) as db:run=dict(row(db,'SELECT * FROM run WHERE id=?',rid))
            if run['status']=='succeeded':return rid
            if run['status']=='waiting_quota' and (was_queued or retried):
                from .continuous_recovery import parse_resets
                raise MaterialWait('执行器额度用完，恢复后点继续',kind='quota',provider=run['provider'],role='image_assess',resetsAt=parse_resets(run))
            if run['status'] in ('running','preparing'):raise MaterialWait('正在评估图片',running=True)
            restart=self._restart_retry(cid,run)
            if restart:
                rid=restart
                with cursor_lock(cid):
                    cursor=self._continuous_record(cid)['cursor']
                    cursor.setdefault('directRuns',{})[original_rid]=rid
                    self._continuous_update(cid,cursor=cursor)
                continue
            if run['status'] in ('failed','stopped','waiting_quota') and not retried:
                rid=self.retry_run(rid,command_id=f'{cid}:retry:{rid}')['runId'];retried=True
                with cursor_lock(cid):
                    cursor=self._continuous_record(cid)['cursor']
                    cursor.setdefault('directRuns',{})[original_rid]=rid
                    self._continuous_update(cid,cursor=cursor)
                continue
            reason=run.get('diagnostic') or run.get('reason') or '图片评估运行失败'
            kind={'authentication':'auth','network':'transient','timeout':'transient'}.get(run.get('reason'))
            if kind:raise MaterialWait(reason,kind=kind,provider=run['provider'],role='image_assess')
            raise Rejected(reason)

    def _continuous_images(self,cid):
        value=self._continuous_checkpoint(cid);eid=value['episode_id']
        self._continuous_cursor(cid,stage='assets',phase='images')
        design=self._continuous_art(eid,'asset_design')
        if not design or not design['adopted_version_id']:self._continuous_stage(cid,'asset_design')
        design=self._continuous_art(eid,'asset_design');vid=design['adopted_version_id']
        content=next(v['content'] for v in design['versions'] if v['id']==vid)
        note_errors=self._continuous_note_errors(eid,'asset_design',content)
        if note_errors:
            self._continuous_create(cid,'asset_design','按当前06报告补齐制作提醒回应：'+'；'.join(note_errors),base=vid)
            self._continuous_adopt(cid,'asset_design',self._continuous_art(eid,'asset_design')['versions'][-1]['id'])
            return self._continuous_images(cid)
        if self.product_placements(eid)['minimum']>0 and not any(asset_product_code(item) and CATEGORIES.get(item.get('kind'))=='scene' for item in content['assets']):
            if value['cursor'].get('productDesignRepaired'):self._continuous_stop(cid,'blocked','素材清单修订后仍缺商品入景条目')
            self._continuous_create(cid,'asset_design','保留其他条目，按项目商品目录和本地商品入景方法补充至少一个自然入景场景图条目，写明 productCode。',base=vid)
            candidate=self._continuous_art(eid,'asset_design')['versions'][-1]['id']
            self._continuous_adopt(cid,'asset_design',candidate)
            self._continuous_cursor(cid,productDesignRepaired=True)
            return self._continuous_images(cid)
        self.initialize_asset_worksheet(eid,vid,f'{cid}:worksheet:{vid}')
        waiting={}; tried=set(); held=set()
        cursor=self._continuous_record(cid)['cursor']
        if cursor.get('autoScope') is not None:
            previous={n['key']:n for n in cursor.get('stoppedNeeds',[])}
            for block in cursor.get('blocks',[]):
                if block['scope'].startswith('need:') and block['id'] not in cursor['autoScope']:
                    key=block['scope'][5:];held.add(key);tried.add(key)
                    waiting[key]=dict(previous.get(key,{**block,'reason':'等待恢复'}),running=False)
        while True:
            self._continuous_checkpoint(cid)
            rows=self.asset_worksheet(eid)['rows']
            self.material_issues(eid)
            counted=[n for n in rows if n['decision']!='text_only']
            self._continuous_cursor(cid,stage='assets',phase='images',progress=dict(current=sum(bool(n['adopted_version_id']) for n in counted),total=len(counted)))
            pending=[n for n in rows if not n['adopted_version_id'] and n['decision']!='text_only']
            if not pending:return self._image_fill_records(cid)
            ready=sorted([n for n in pending if not n['missing'] and n['need_key'] not in tried],key=lambda n:({'identity':0,'scene':1}.get(CATEGORIES.get(n['kind']),2),n['sort']))
            if ready:
                # 实测 139：互不依赖的条目按图片通道的并发上限同时出图、同时评估；通道只允许 1 个时照旧逐项处理。
                workers=self._image_parallelism(cid)
                if workers == 1:
                    ready=ready[:1]
                for need in ready:tried.add(need['need_key'])
                if workers>1 and len(ready)>1:
                    with ThreadPoolExecutor(max_workers=min(workers,len(ready)),thread_name_prefix='continuous-image') as pool:
                        results=list(pool.map(lambda n:self._image_need_round(cid,n,parallel=True),ready))
                else:
                    results=[self._image_need_round(cid,ready[0])]
                for need,wait in zip(ready,results):
                    if wait:waiting[need['need_key']]=wait
                continue
            # All independent work has been attempted. Never turn a live request into a failure.
            for need in pending:
                waiting.setdefault(need['need_key'],dict(reason='素材依赖尚未满足：'+'、'.join(need['missing']),running=False))
            self._continuous_cursor(cid,waits=waiting)
            if any(w['running'] for w in waiting.values()):return False
            progressed=False
            for need in pending:
                key=need['need_key'];state=self._image_state(cid,key)
                if key in held or waiting[key].get('kind') or state.get('pendingBatch') or state.get('parkedQueue') or state.get('executionFailure'):continue  # preserve external failures
                try:
                    if self._material_bottleneck(cid,need,waiting[key]):
                        waiting.pop(key,None);tried.discard(key);progressed=True;break
                except MaterialWait as exc:
                    waiting[key]=exc.waiting()
            if progressed:continue
            self._continuous_cursor(cid,waits=waiting)
            # 实测 143：停下原因逐项写明条目名，并记下候选，卡片可直接跳到这一项。
            stopped=[]
            for n in pending:
                text=self._image_state(cid,n['need_key']).get('directorText') or waiting[n['need_key']]['reason']
                stopped.append(dict(**{k:waiting[n['need_key']].get(k) for k in ('kind','provider','role','batchIds','resetsAt')},key=n['need_key'],title=n['title'],reason=text,candidates=self._image_state(cid,n['need_key']).get('rankedCandidates') or list(n.get('candidates') or [])))
            self._continuous_cursor(cid,stoppedNeeds=stopped)
            reasons=[f"{x['title']}：{x['reason']}" for x in stopped]
            blocks=[self._recovery_block('need:'+n['key'],**{k:n.get(k) for k in ('kind','provider','role','batchIds','resetsAt')}) for n in stopped if n.get('kind')]
            self._continuous_stop(cid,'blocked','；'.join(dict.fromkeys(reasons)),blocks=blocks)

    def _material_bottleneck(self,cid,need,wait):
        eid=need['episode_id'];key=need['need_key'];state=self._image_state(cid,key)
        reason=wait.get('reason','') if isinstance(wait,dict) else str(wait)
        assessments=self._image_assess_need(cid,need)
        # 实测 143：只有真的在等（依赖图未定、请求仍在进行）才允许“等待”；质量问题只能暂用、局部修图或降级。
        wait_for=[*need['missing'],*([state['pendingBatch']] if state.get('pendingBatch') else [])]
        actions=['wait'] if wait_for or (isinstance(wait,dict) and wait.get('running')) else []
        if assessments and not need['missing']:actions.append('provisional')
        if assessments and not need['missing'] and self._can_edit(cid,need,state,assessments):actions.append('edit')
        if not need['image_prompt'] and not state.get('inputFilled'):actions.append('fill_input')
        # Identity references remain mandatory; only scene/prop descriptions can degrade.
        if not assessments and CATEGORIES.get(need['kind']) in ('scene','prop') and not state.get('textOnlyTried'):actions.append('text_only')
        if not actions:
            self._material_issue(cid,need,text=reason,state='missing')
            return False
        answer=state.get('textOnlyAction') if state.get('textOnlyPhase') not in (None,'failed','done') else None
        if not answer:answer=self._material_judgment(cid,need,assessments,reason,actions,wait_for)
        if not answer and assessments and 'provisional' in actions:
            # 专家不可用或回包两次不合格：暂用排序第一的候选，问题列为未解决（v4 V2-06）。
            top=assessments[0]
            answer=dict(action='provisional',target=key,versionId=top['versionId'],instruction='',
                        issueKeys=[i['type']+':'+i['where'] for i in top['critical']] or ['verdict:'+top['verdict']],
                        reason='专家未给出可执行判断，按评估排序暂用最好的一张',summary=need['title']+'：返修后仍有问题，暂用评估最好的一张，问题列为未解决。')
        if not answer:
            if not assessments:self._material_issue(cid,need,text=reason,state='missing')
            return False
        self._image_state(cid,key,directorText=answer['summary'])
        if answer['action']=='provisional':
            candidate=next(a for a in assessments if a['versionId']==answer['versionId'])
            self._image_choose(cid,need,candidate)
            self._material_issue(cid,self._need(eid,key),candidate,answer['summary'])
            self._continuous_log(cid,'assets','provisional_use',candidate['versionId'],'AI 暂用（未解决）',answer)
            return True
        if answer['action']=='edit':
            return self._material_edit(cid,need,answer)
        if answer['action']=='fill_input':
            self.set_asset_need(eid,key,need['revision'],'edit_prompt',f'{cid}:fill:{key}:{state.get("epoch",0)}',prompt=answer['instruction'])
            self._image_state(cid,key,inputFilled=True)
            return True
        if answer['action']=='text_only':
            return self._material_text_only(cid,need,answer)
        if not assessments:self._material_issue(cid,need,text=answer['summary'],state='missing')
        return False

    def _can_edit(self,cid,need,state,assessments):
        """实测 143：局部修图——当前通道支持编辑、本条目修图未满 2 次、最好的候选只有局部（非身份）问题。"""
        if state.get('edits',0)>=EDIT_LIMIT:return False
        try:channel=self._image_options(need['episode_id'],self._image_channel(cid,need['episode_id']))['provider']
        except Rejected:return False
        if 'edit' not in (self.image_providers().get(channel,{}).get('capabilities',{}).get('operations') or []):return False
        top=assessments[0]
        return top['verdict']=='blocked' and bool(top['critical']) and not any(i['type']=='identity' for i in top['critical'])

    def _material_edit(self,cid,need,answer):
        eid=need['episode_id'];key=need['need_key'];state=self._image_state(cid,key)
        n=state.get('edits',0)+1
        base=state.get('basePrompt') or need['image_prompt']
        prompt='以第一张参考图为底图，只做局部修改：'+answer['instruction'].strip()+'\n其余人物、服装、构图、背景和光线保持不变。\n原条目要求：'+base
        self._image_state(cid,key,edits=n,basePrompt=base)
        self._image_generate(cid,need,edit=dict(versionId=answer['versionId'],prompt=prompt,n=n,instruction=answer['instruction']))
        return True

    def _material_text_only(self,cid,need,answer):
        eid=need['episode_id'];key=need['need_key'];state=self._image_state(cid,key)
        epoch=state.get('epoch',0)
        if not state.get('textOnlyPhase'):
            art=self._continuous_art(eid,'asset_design')
            dependents=[r['need_key'] for r in self.asset_worksheet(eid)['rows'] if key in r['depends_on'] and r['decision']!='text_only']
            state=self._image_state(cid,key,textOnlyTried=True,textOnlyPhase='deps',textOnlyAction=answer,textOnlyBase=art['adopted_version_id'],textOnlyDependents=dependents)
        try:
            if state['textOnlyPhase']=='deps' and state['textOnlyDependents']:
                instruction='只调整以下依赖条目的 dependsOn 和出图提示词，去掉对缺失素材 '+key+' 的图片引用，改为准确文字描述。其余条目和稳定 id 保留。条目：'+dumps(state['textOnlyDependents'])
                job=self.create_task(eid,'asset_design',instruction,base_version_id=state['textOnlyBase'],command_id=f'{cid}:textonly:{key}:{epoch}:deps')
                self._continuous_execute(cid,job['runId'])
                art=self._continuous_art(eid,'asset_design')
                versions=[v for v in art['versions'] if v.get('source_run_id')==self._continuous_record(cid)['cursor'].get('runId')]
                if not versions:raise Rejected('依赖修订没有生成有效素材清单')
                vid=versions[-1]['id']
                original=self._continuous_version(state['textOnlyBase'])['assets']
                revised=versions[-1]['content']['assets']
                before={i['id']:i for i in original};after={i['id']:i for i in revised}
                if before.keys()!=after.keys():raise Rejected('依赖修订改变了无关素材条目')
                for ident,item in before.items():
                    allowed={'dependsOn','imagePrompt'} if ident in state['textOnlyDependents'] else set()
                    if {k:v for k,v in item.items() if k not in allowed}!={k:v for k,v in after[ident].items() if k not in allowed}:
                        raise Rejected('依赖修订改变了无关内容：'+ident)
                if art['adopted_version_id']!=vid:self._continuous_adopt(cid,'asset_design',vid)
            self._image_state(cid,key,textOnlyPhase='set')
            need=self._need(eid,key)
            if need['decision']!='text_only':
                self.set_asset_need(eid,key,need['revision'],'text_only',f'{cid}:textonly:{key}:{epoch}:set',reason=answer['summary'])
            self._image_state(cid,key,textOnlyPhase='done')
            return True
        except Rejected as exc:
            self._image_state(cid,key,textOnlyPhase='failed')
            self._material_issue(cid,need,text='文字降级未完成：'+str(exc),state='missing')
            return False

    def _director_adopted(self,eid,vid):
        """实测 144：这张已采用图不是 AI 选的（没有 AI 的选图或暂用记录），就是导演在备图工作单亲手采用的。"""
        with connect(self.db_path) as db:
            rows=db.execute("SELECT detail FROM ai_decision WHERE episode_id=? AND ref=? AND kind IN ('choose_image','provisional_use')",(eid,vid)).fetchall()
        return not any('userDirection' not in json.loads(r[0] or '{}') for r in rows)

    def _accept_director_adoption(self,cid,need,assessment):
        """导演亲手采用即视为接受这张图的严重问题：记为导演已接受，交付页照常列出，不再停下要求确认。"""
        vid=assessment['versionId']
        detail=dict(assessment=assessment,needKey=need['need_key'],userDirection=dict(stage='closing_manual_revision',instruction='导演在备图工作单采用此图，保留这些问题',critical=assessment['critical']))
        decision=self._continuous_log(cid,'assets','accept_issue',vid,'导演亲手采用，保留未解决问题：'+need['title'],detail)
        issues=[(i['type']+':'+i['where'],i['text']) for i in assessment['critical']] or [('verdict:'+assessment['verdict'],assessment['reason'])]
        self._material_issue(cid,need,assessment,'导演亲手采用，保留：'+assessment['reason'],state='accepted')
        with transaction(self.db_path) as db:
            for issue_key,text in issues:
                db.execute('INSERT OR IGNORE INTO accepted_image_issue VALUES(?,?,?,?,?)',(vid,issue_key,text,decision,self.clock()))

    def _image_fill_records(self,cid):
        eid=self._continuous_record(cid)['episode_id'];rows=self.asset_worksheet(eid)['rows']
        matrix=self.cast_matrix(eid)
        for need in rows:
            vid=need['adopted_version_id']
            if not vid:continue
            values=[r['body'] for r in self.image_assessments(eid,need['need_key']) if r['version_id']==vid]
            # Adopted assets are production decisions, including legacy ones.
            # Keep any actual observations; absence is not a new rejection.
            observation=values[-1]['observation'] if values else '沿用已采用素材；历史看图观察未登记。'
            if CATEGORIES.get(need['kind'])=='identity' and need['role_id'] and need['state_id'] in (None,'','default','base'):
                with connect(self.db_path) as db:fixed=bool(db.execute('SELECT 1 FROM fixed_role_asset WHERE episode_id=? AND role=? AND asset_version_id=?',(eid,need['role_id'],vid)).fetchone())
                appearance='' if fixed else need['description'].strip() or observation.split('。')[0]
                with connect(self.db_path) as db:
                    existing=db.execute('SELECT appearance FROM episode_role_baseline WHERE episode_id=? AND role=? AND asset_version_id=?',(eid,need['role_id'],vid)).fetchone()
                baseline=dict(appearance=existing['appearance']) if existing else self.set_role_baseline(eid,need['role_id'],vid,appearance,f'{cid}:baseline:{vid}')
                appearance=baseline['appearance']
                self._continuous_log(cid,'assets','fill_observation',vid,'采用身份图并登记外观',dict(observation=appearance))
            for state in matrix['roleStates']:
                if state['confirmed'] or (state['role'],state['stateId'])!=(need['role_id'],need['state_id']):continue
                self.confirm_role_state(eid,state['role'],state['stateId'],state['designHash'],vid,observation,f'{cid}:role-state:{state["designHash"]}:{vid}')
                self._continuous_log(cid,'assets','fill_observation',vid,'继承已采用状态图与现有观察',dict(observation=observation))
        for lock in self.space_locks(eid):
            if lock['status']!='pending':continue
            candidates=[]
            for need in rows:
                vid=need['adopted_version_id']
                if not vid or CATEGORIES.get(need['kind'])!='scene':continue
                with connect(self.db_path) as db:accepted=bool(db.execute('SELECT 1 FROM accepted_image_issue WHERE asset_version_id=?',(vid,)).fetchone())
                values=[r['body'] for r in self.image_assessments(eid,need['need_key']) if r['version_id']==vid]
                candidates.append(dict(versionId=vid,sceneIds=need['scene_ids'],description=need['description'],assessment=values[-1] if values else None,accepted=accepted,adopted=True))
            instruction='按场次、空间关系及实际看图观察，为空间锁定选择适配的已采用图片。继承已采用用途，不重新评图片质量；缺历史观察不代表错误。只选择能作为本项空间依据的图，不要求图片证明声音或后续动作。无适配图返回 null。reply JSON {"versionId":"候选编号或null","reason":"理由"}。\n'+dumps(dict(lock=lock,candidates=candidates))
            selection=None
            if candidates:
                for _ in range(2):
                    try:
                        answer=json.loads(self._continuous_discuss(cid,'asset_design',instruction))
                        if answer.get('versionId') is not None and answer['versionId'] not in [c['versionId'] for c in candidates]:raise ValueError()
                        if not isinstance(answer.get('reason'),str) or not answer['reason'].strip():raise ValueError()
                        selection=answer;break
                    except (ValueError,TypeError,AttributeError):instruction+='\n请修正回包格式。'
                if selection is None:self._continuous_stop(cid,'blocked','空间锁定选图回复格式不合格')
            if selection and selection['versionId']:
                self.confirm_space_item(lock['id'],selection['versionId'],command_id=f'{cid}:lock:{lock["id"]}:{selection["versionId"]}')
                self._continuous_log(cid,'assets','confirm_lock',lock['id'],selection['reason'],dict(versionId=selection['versionId']))
            else:
                cursor=self._continuous_record(cid)['cursor'];supplemented=cursor.get('lockSupplements',[])
                if lock['id'] in supplemented:self._continuous_stop(cid,'blocked','补图后仍无适配空间锁定的场景图：'+lock['label'])
                art=self._continuous_art(eid,'asset_design')
                self._continuous_create(cid,'asset_design','保留现有条目，为以下空间锁定补充一张场景图条目，标明剧本场号和需要呈现的空间关系：'+dumps(lock),base=art['adopted_version_id'])
                version=self._continuous_art(eid,'asset_design')['versions'][-1]['id']
                self._continuous_adopt(cid,'asset_design',version)
                self._continuous_cursor(cid,lockSupplements=[*supplemented,lock['id']])
                return self._continuous_images(cid)
        with connect(self.db_path) as db:
            unfinished=db.execute("SELECT r.id,r.batch_id FROM image_request r JOIN image_batch b ON b.id=r.batch_id WHERE b.episode_id=? AND b.state!='cancelled' AND r.state IN ('pending','submitted','unknown') LIMIT 1",(eid,)).fetchone()
        if unfinished:self._continuous_stop(cid,'blocked','仍有进行中或待核对的图片请求：'+unfinished[0],blocks=[self._recovery_block('run:assets:image','image_wait',role='image',batchIds=[unfinished[1]])])
        if self.product_placements(eid)['minimum']>0:
            design=self._continuous_art(eid,'asset_design')
            content=next(v['content'] for v in design['versions'] if v['id']==design['adopted_version_id'])
            product_keys={item['id'] for item in content['assets'] if asset_product_code(item) and CATEGORIES.get(item.get('kind'))=='scene'}
            if not any(need['need_key'] in product_keys and need['adopted_version_id'] for need in rows):self._continuous_stop(cid,'blocked','项目要求商品入景，但尚无采用的商品场景图')
        unconfirmed=[r for r in self.cast_matrix(eid)['roleStates'] if not r['confirmed']]
        if unconfirmed:self._continuous_stop(cid,'blocked','角色状态尚未完成对图：'+'、'.join(r['role']+'/'+r['stateId'] for r in unconfirmed))
        return True

    def _accept_known_image_review_issues(self,cid,version_id,segment,pass_name):
        from .progress_expert import provisional_image_issues
        with connect(self.db_path) as db:
            _,art,entry,_,_=self._segment_material(db,version_id,segment)
            accepted=[dict(r) for ref in entry.get('refs',[]) for r in db.execute('SELECT * FROM accepted_image_issue WHERE asset_version_id=?',(ref['assetVersionId'],))]
            provisional=[r for ref in entry.get('refs',[]) for r in provisional_image_issues(db,art['episode_id'],ref['assetVersionId'])]
        from .materialcontext import usage_clearances
        with connect(self.db_path) as db:usage=usage_clearances(db,art['episode_id'],segment,entry)
        if not accepted and not provisional and not usage:return
        status=self.prompt_review_status(version_id)[segment]
        review=status['coldRead' if pass_name=='cold_read' else 'comparison']
        for issue in review.get('issues',[]):
            if issue.get('waiver_reason'):continue
            instruction='核对独审意见是否与清单中的某一图片问题为同一个问题。仅同一问题可以放行。用户已接受返回 acceptedIssue，AI 暂用返回 provisionalIssue，不得混淆。reply JSON {"acceptedIssue":null或{"assetVersionId":"图片版本","issueKey":"问题键"},"provisionalIssue":null或同样对象,"reason":"依据"}。\n'+dumps(dict(issue=issue,accepted=accepted,provisional=provisional,usage=usage,promptText=entry.get('txt','')))
            instruction+=' 如仅重复 usage 中已核对通过的同一限定用途问题，且正文已落实限制，可返回 usageIssue={assetVersionId,assessmentId,observationId,sourceIssue,quote}，前四项原样引用清单，quote 必须是正文落实限制的准确原句。新矛盾、超出用途或未落实限制不得返回 usageIssue；无对应则全部 null。'
            try:answer=json.loads(self._continuous_discuss(cid,'prompts',instruction,base=version_id))
            except (ValueError,TypeError):continue
            if not isinstance(answer,dict):continue
            use=answer.get('usageIssue')
            if isinstance(use,dict):
                try:
                    self.waive_segment_issue(issue['id'],'继承已核对的限定用途：'+str(answer.get('reason','')),f'{cid}:known-use:{issue["id"]}',usage_issue=use)
                except Rejected:pass
                else:
                    self._continuous_log(cid,'prompts','waive_segment',issue['id'],'同一限定用途问题不重复返修',dict(disposition='usage',match=use,judgment=answer))
                    continue
            for field,records in (('acceptedIssue',accepted),('provisionalIssue',provisional)):
                match=answer.get(field)
                if not isinstance(match,dict) or not any((r['asset_version_id'],r['issue_key'])==(match.get('assetVersionId'),match.get('issueKey')) for r in records):continue
                reason='用户已接受的已知问题' if field=='acceptedIssue' else 'AI 暂用的未解决图片问题'
                self.waive_segment_issue(issue['id'],reason,f'{cid}:known-image:{issue["id"]}',**{'accepted_issue' if field=='acceptedIssue' else 'provisional_issue':match})
                self._continuous_log(cid,'prompts','waive_segment',issue['id'],reason,dict(disposition=field,match=match,judgment=answer))
                break

    def _image_feedback(self,cid):
        value=self._continuous_record(cid);feedback=value['cursor'].get('imageFeedback')
        if not feedback:return True
        eid=value['episode_id'];art=self._continuous_art(eid,'asset_design')
        job=self.create_task(eid,'asset_design',feedback['text'],auto_dialogue=True,base_version_id=art['adopted_version_id'],workspace_revision=art['revision'],command_id=feedback['commandId'],files=[(f['name'],self.store.read_blob(f['sha256'])) for f in feedback.get('files',[])])
        self._continuous_execute(cid,job['runId'])
        latest=self._continuous_art(eid,'asset_design')['versions'][-1]
        self._continuous_cursor(cid,imageFeedback=None)
        if latest['id']==art['versions'][-1]['id']:
            rid=self._continuous_record(cid)['cursor']['runId']
            result=json.loads((self.store.run_dir(rid)/'result.json').read_text())
            choice=dict(feedback['choice'],reply=result.get('reply',''))
            self._continuous_update(cid,state='waiting_choice',choice=choice)
            return False
        self._continuous_adopt(cid,'asset_design',latest['id'])
        key=feedback['choice']['needKey'];need=self._need(eid,key)
        self.set_asset_need(eid,key,need['revision'],'generate',feedback['commandId']+':generate',prompt=need['image_prompt'])
        state=self._image_state(cid,key)
        self._image_state(cid,key,priorAttempts=[*state.get('priorAttempts',[]),*state['attempts']],attempts=[],epoch=new_id(),assessedRevision=None,keyVisualSupplemented=False,extraRequested=False)
        self._continuous_log(cid,'assets','apply_user_change',key,'按用户原话修订素材并重新评估',dict(instruction=feedback['text'],versionId=latest['id']))
        return True

    def _choose_image_card(self,value,choice_key,text=None,command_id=None,files=()):
        choice=value['choice'];cid=value['id'];eid=value['episode_id'];key=choice['needKey'];need=self._need(eid,key)
        if choice_key=='free':
            if not isinstance(text,str) or not text.strip():raise Rejected('请写明想法')
            self._continuous_cursor(cid,imageFeedback=dict(text=text,choice=choice,commandId=command_id or new_id(),files=[dict(name=name,sha256=self.store.put_blob(raw)) for name,raw in files]))
            self._continuous_update(cid,state='running',choice=None,reason=None)
            return self._advance_continuous(cid)
        if need['revision']!=choice['needRevision']:raise Conflict('素材条目已更新，请按当前用途重新评估')
        if choice_key=='regenerate' and choice.get('regenerate'):
            self._image_state(cid,key,extraRequested=True)
            self._continuous_update(cid,state='running',choice=None,reason=None)
            self._image_generate(cid,need)
        else:
            item=next((c for c in choice['candidates'] if c['key']==choice_key),None)
            if not item:raise Rejected('请选择卡片中的图片')
            self._image_choose(cid,need,item,user=True,accept=choice['kind']=='image_failed')
            self._continuous_update(cid,state='running',choice=None,reason=None)
        return self._advance_continuous(cid)
