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
from .images import INTERRUPTED_IMAGE, TRANSPORT_FAILURE


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
    def __init__(self, reason, running=False, kind=None, provider=None, role=None, batchIds=(), resetsAt=None, selection=None):
        self.reason=reason; self.running=running
        self.kind=kind; self.provider=provider; self.role=role
        self.batchIds=list(batchIds); self.resetsAt=resetsAt; self.selection=selection or {}

    def waiting(self):
        return dict(reason=self.reason,running=self.running,kind=self.kind,provider=self.provider,
                    role=self.role,batchIds=self.batchIds,resetsAt=self.resetsAt,**self.selection)


def generated_count(attempts):
    return sum(1 for x in attempts if not x.get('edit') and not x.get('interrupted') and x.get('blockedBy')!='auth')



def usable(assessment,accepted=False):
    return accepted or assessment and (assessment['verdict']=='usable' or assessment['verdict']=='limited' and not assessment['critical'])


class ContinuousImages(ProgressExpert):
    def _image_ratio(self,eid):
        with connect(self.db_path) as db:return self._kickoff(db,eid)['spec']['aspectRatio']

    def _image_options(self,eid,selected):
        channels=self.image_providers();selected=selected or {}
        provider=selected.get('provider') or self.system_image_channel()['provider']
        if provider not in channels:
            from .errors import ExecutorUnavailable
            raise ExecutorUnavailable('所选图像通道未配置：'+str(provider))
        caps=channels[provider]['capabilities'];models=caps.get('models') or []
        model=selected.get('model') or (models[0] if models else None)
        if model and model not in models:
            from .errors import ExecutorUnavailable
            raise ExecutorUnavailable('所选图像型号未由当前通道提供：'+str(model))
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

    def _reviewed_image_prompt(self,cid,eid,key,source,feedback,method):
        """Use the episode expert to turn review evidence into the target picture."""
        # Recover the artwork brief from earlier worksheet versions which saved
        # the review envelope alongside the actual prompt.
        source=source.split('\n上一轮实际评估，修正指出的问题：',1)[0]
        if '。原画面要求：' in source and '按原用途修正明显缺陷：' in source:
            source=source.split('。原画面要求：',1)[1]
        if not feedback:return source
        materials=dict(asset=key,source=source,review=feedback,method=method)
        instruction=('把评估中需要改变的内容落实成这张图片最终可见的状态，保留原稿的角色、构图和其他既定要求。'
                     'reply 为 JSON 对象，仅含 prompt 字符串，内写完整生图正文，沿用原稿语言，不加标题、修改说明或评估理由。')
        art=self._continuous_art(eid,'asset_design')
        digest=sha256_bytes(canonical(materials))
        job=self.create_task(eid,'asset_design',instruction,discussion_only=True,
            workspace_revision=art['revision'],creation_role='create',
            bounded_judgment=dict(kind='image_prompt_revision',materials=materials),
            command_id=f'{cid}:image-prompt:{digest}')
        actual=self._continuous_run_direct(cid,job['runId'])
        reply=json.loads((self.store.run_dir(actual)/'result.json').read_text())['reply']
        try:
            prompt=json.loads(reply)['prompt'].strip()
        except (ValueError,KeyError,TypeError,AttributeError) as exc:
            raise Rejected('图像返修没有交回画面描述') from exc
        if (not prompt or prompt.startswith(('{','[','```')) or
                any(label in prompt for label in ('上一轮实际评估，修正指出的问题：','按原用途修正明显缺陷：'))):
            raise Rejected('图像返修没有交回可直接生成的画面描述')
        return prompt

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
                    interrupted=bool(errors) and all(e in (INTERRUPTED_IMAGE, TRANSPORT_FAILURE) for e in errors)
                    auth=batch['provider']=='codex-native' and any('本机Codex尚未登录' in e for e in errors)
                    attempt['result']='被打断，未出图' if interrupted else '出图失败：'+'；'.join(dict.fromkeys(errors))
                    if interrupted:attempt['interrupted']=True
                    if auth:attempt['blockedBy']='auth'
                    changes=dict(pendingBatch=None,attempts=attempts)
                    if interrupted:changes['interruptionRetries']=state.get('interruptionRetries',0)+1
                    if interrupted and attempt.get('edit'):changes['edits']=max(0,state.get('edits',0)-1)
                    self._image_state(cid,key,**changes)
                    if any('insufficient_quota' in e for e in errors):raise MaterialWait('图片通道额度不足，等待额度恢复',kind='quota',provider=batch['provider'],role='image')
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
                if generated>=2:
                    prompt=self._reviewed_image_prompt(cid,eid,key,prompt,evidence,method)
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
            raise MaterialWait('等待既有出图请求回执：'+batch['id'],running=running,
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
        elif need['adopted_version_id']!=vid:self.adopt_asset_needs(eid,{key:dict(versionId=vid,revision=need['revision'])},f'{cid}:image-adopt:{key}:{vid}')
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
        if saved:return dict(provider=saved)
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
        if saved and saved.get('provider'):
            return {**options,'provider':saved['provider'],'model':saved.get('model'),'source':'director'}
        with connect(self.db_path) as db:
            used=db.execute('SELECT provider,model,spec FROM image_batch WHERE episode_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(episode_id,)).fetchone()
        if used:
            spec=json.loads(used['spec'] or '{}')
            return {**options,'provider':used['provider'],'model':used['model'],'quality':spec.get('quality'),'source':'last_batch'}
        if options.get('provider'):return {**options,'source':'kickoff'}
        return {**options,**self.system_image_channel(),'source':'default'}

    def save_episode_image_channel(self,episode_id,provider,model=None,command_id=None):
        """导演一改就保存；连续制作下一次出图起按新的通道与型号。"""
        channels=self.image_providers()
        if provider not in channels:
            from .errors import ExecutorUnavailable
            raise ExecutorUnavailable('所选图像通道未配置：'+str(provider))
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
        previous=self._continuous_record(cid)['cursor']
        prior_blocks=previous.get('blocks',[])
        prior_counts=previous.get('wakeCounts',{})
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
            if not need['candidates']:
                self._image_library_candidates(cid,need)
            need=self._need(eid,key)
            state=self._image_state(cid,key)
            retired=set(state.get('retiredCandidates',[]))
            if state.get('pendingBatch') or not any(v not in retired for v in need['candidates']):
                need=self._image_generate(cid,need)
            available=[v for v in need['candidates'] if v not in retired]
            if not available:
                raise MaterialWait('出图未取得文件，可重试',kind='transient',role='image')
            vid=available[-1]
            entry=state.get('libraryEntries',{}).get(vid)
            if entry:self.set_asset_need(eid,key,need['revision'],'reuse',f'{cid}:ready-library:{key}:{vid}',entry_id=entry,reason='按条目用途复用已选库图')
            else:self.adopt_asset_needs(eid,{key:dict(versionId=vid,revision=need['revision'])},f'{cid}:ready:{key}:{vid}')
            self._continuous_log(cid,'assets','choose_image',vid,'为分镜选用已有图片版本',dict(needKey=key,reused=bool(entry)))
            self._image_state(cid,key,selected=vid)
            return None
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
            if parallel or not isinstance(exc,Halt) or value['state']!='waiting_external':raise
            self._image_state(cid,key,parkedQueue=value['cursor'].get('queue',[]),executionFailure=value['reason'])
            self._continuous_cursor(cid,queue=[])
            self._continuous_update(cid,state='running',reason=None)
            blocks=value['cursor'].get('blocks',[])
            fields={k:blocks[0].get(k) for k in ('kind','provider','role','batchIds','resetsAt')} if blocks else {}
            with cursor_lock(cid):
                cursor=self._continuous_record(cid)['cursor'];cursor.update(blocks=prior_blocks,wakeCounts=prior_counts);cursor.pop('pendingBlocks',None)
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
                raise MaterialWait('执行器额度用完，等待自动恢复',kind='quota',provider=run['provider'],role='image_assess',resetsAt=parse_resets(run),
                                   selection=dict(model=run['requested_model'],effort=run['requested_effort']))
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
                rid=self.retry_run(rid,command_id=f'{cid}:retry:{rid}',**self._continuous_retry_slot(cid,run))['runId'];retried=True
                with cursor_lock(cid):
                    cursor=self._continuous_record(cid)['cursor']
                    cursor.setdefault('directRuns',{})[original_rid]=rid
                    self._continuous_update(cid,cursor=cursor)
                continue
            reason=run.get('diagnostic') or run.get('reason') or '图片评估运行失败'
            kind={'authentication':'auth','network':'transient','timeout':'transient'}.get(run.get('reason'))
            if kind:raise MaterialWait(reason,kind=kind,provider=run['provider'],role='image_assess',
                                       selection=dict(model=run['requested_model'],effort=run['requested_effort']))
            raise Rejected(reason)

    @staticmethod
    def _grid_frames(seg,shots):
        frames=seg.get('cells')
        ids={s['id'] for s in shots}
        if isinstance(frames,list) and len(frames)==9 and all(isinstance(f,dict) and f.get('shotId') in ids and isinstance(f.get('visual'),str) and f['visual'].strip() for f in frames):
            return frames
        if not shots:raise Rejected('九宫格段缺少镜头：'+seg['id'])
        ordered=sorted(shots,key=lambda s:s.get('order',0))
        return [dict(shotId=s['id'],visual=s.get('action','')) for s in (ordered[round(i*(len(ordered)-1)/8)] for i in range(9))]

    def _grid_artwork(self,cid,eid,plan_id):
        from . import gridcontinuity
        plan=self._continuous_version(plan_id)
        matrix=self.cast_matrix(eid)
        owned=gridcontinuity.restore(self,cid,eid)
        current_grids={s['id'] for s in plan['segments'] if s['strategy']=='grid'}
        for sid,g in list(owned.items()):
            if sid in current_grids:continue
            board=g['versionId']
            for r in matrix['rows']:
                if r['segment']!=sid:continue
                for role,c in r['cells'].items():
                    if c.get('assetVersionId')!=board:continue
                    source=g.get('roleInputs',{}).get(role) or matrix['roster'].get(role,{}).get('assetVersionId')
                    if source:self.set_cast(eid,sid,role,'ok',source,note='分镜改用独立参考图',command_id=f'{cid}:restore-cast:{plan_id}:{sid}:{role}:{source}')
            owned.pop(sid)
            self._continuous_cursor(cid,gridArtwork=owned)
        from . import worksheetrefs
        selected_art = worksheetrefs.select(self,cid,eid,plan_id,plan,self.cast_matrix(eid))
        items=[];keys={}
        for seg in plan['segments']:
            if seg['strategy']!='grid':continue
            sid=seg['id'];shots=[s for s in plan['shots'] if s['segment']==sid]
            roles={r for s in shots for r in s.get('visibleRoles',[])}
            refs=[d['master'] for d in self._continuous_record(cid)['cursor'].get('productDrawings',{}).get(sid,[])]
            for role in sorted(roles):
                cell=next((r['cells'].get(role,{}) for r in matrix['rows'] if r['segment']==sid),{})
                vid=cell.get('assetVersionId')
                if vid and self.asset_version(vid)['grid']:
                    vid=owned.get(sid,{}).get('roleInputs',{}).get(role) or matrix['roster'].get(role,{}).get('assetVersionId')
                if vid:refs.append(vid)
            refs.extend(selected_art.get(sid, []))
            frames=self._grid_frames(seg,shots)
            prompt='本段故事板，三行三列等大九格，按阅读顺序展示关键动作，无文字。'+str(seg.get('gridPrompt') or '')+' '+dumps(frames)
            drawings=self._continuous_record(cid)['cursor'].get('productDrawings',{}).get(sid,[])
            if drawings:prompt+=' 商品使用位置：'+dumps([dict(shot=d['shotId'],intent=d['intent']) for d in drawings])
            inputs=list(dict.fromkeys(refs))[:16]
            prior=owned.get(sid,{})
            replacements=self._continuous_record(cid)['cursor'].get('reviewReplacements',{})
            # Keep accepted artwork when only a visually reviewed source was repaired.
            original_inputs=prior.get('inputs',[])
            if prior.get('prompt')==prompt and {replacements.get(v,v) for v in original_inputs}==set(inputs):continue
            basis=sha256_bytes(canonical([sid,prompt,inputs]))
            if owned.get(sid,{}).get('basis')==basis:continue
            keys[sid]=basis
            items.append(dict(prompt=prompt,inputs=inputs,segments=[sid],basis=basis,target=dict(name=sid+' 九宫格',category='grid',grid=True,roles=sorted(roles))))
        if items:
            receipts=self._continuous_record(cid)['cursor'].get('gridBatches',{})
            fresh=[item for item in items if item['basis'] not in receipts]
            if fresh:
                opts=self._image_options(eid,self._image_channel(cid,eid))
                job=self.create_batch(eid,operation='generate',items=fresh,command_id=cid+':grids:'+sha256_bytes(canonical(fresh)),**opts)
                receipts.update({item['basis']:job['batchId'] for item in fresh})
                self._continuous_cursor(cid,gridBatches=receipts)
            waiting=[];failed=[];interrupted=[]
            for bid in dict.fromkeys(receipts[item['basis']] for item in items):
                batch=self.batch_view(bid)
                if not batch['authorization']:self.authorize_batches(eid,{bid:batch['summary_hash']},cid+':grid-auth:'+bid)
                for turn in range(2):
                    if self.batch_view(bid)['state'] in ('authorized','partial'):self.execute_batch(bid)
                    batch=self.batch_view(bid)
                    retry=[]
                    for r in batch['requests']:
                        if r['replaced']:continue
                        item=batch['spec']['items'][r['sourceOrdinal']-1];sid=item['segments'][0]
                        if keys.get(sid)!=item['basis']:continue
                        if r['state']=='succeeded':
                            owned[sid]=dict(roleInputs={role:source for role,source in owned.get(sid,{}).get('roleInputs',{}).items() if source in item['inputs']},versionId=r['asset_version_id'],basis=item['basis'],inputs=item['inputs'],prompt=item['prompt'],title='本段九宫格：'+'；'.join(str(s.get('action','')) for s in plan['shots'] if s['segment']==sid)[:120])
                        elif r['state'] in ('failed','confirmed_not_executed'):
                            if r.get('error') in (INTERRUPTED_IMAGE, TRANSPORT_FAILURE):
                                if turn==0:retry.append(r['id'])
                                else:interrupted.append(sid)
                            elif not r['replaces_id'] and turn==0:retry.append(r['id'])
                            else:failed.append(sid)
                        elif bid not in waiting:waiting.append(bid)
                    if not retry:break
                    for rid in retry:self.resubmit_request(rid,f'{cid}:grid-resubmit:{rid}')
                self._continuous_cursor(cid,gridArtwork=owned)
            if interrupted:
                self._continuous_problem(cid,'九宫格连接中断，等待补图：'+'、'.join(interrupted),blocks=[self._recovery_block('grid:'+sid,'transient',role='image') for sid in interrupted])
            if failed:
                # A storyboard is a drawing strategy. Preserve the shots and use
                # the segment's actual base references when the drawing service fails.
                segments=[dict(seg,strategy='flex') if seg['id'] in failed else seg for seg in plan['segments']]
                art=self._continuous_art(eid,'plan')
                update=self.save_manual(art['id'],plan_id,dict(segments=segments),f'{cid}:grid-flex:{plan_id}:'+','.join(sorted(set(failed))),note='九宫格出图失败，本段改用独立参考图')
                vid=self._continuous_internal_version(cid,update['versionId'])
                self._continuous_adopt(cid,'plan',vid)
                from .continuous import UserChange
                raise UserChange()
            if waiting:
                self._continuous_problem(cid,'九宫格原请求尚未返回',blocks=[self._recovery_block('run:plan:grid','image_wait',role='image',batchIds=[bid]) for bid in waiting])
        for seg in plan['segments']:
            if seg['strategy']!='grid':continue
            sid=seg['id'];g=owned[sid];vid=g['versionId'];shots=[s['id'] for s in plan['shots'] if s['segment']==sid]
            self._continuous_bind(cid,vid,shots,plan_id,'grid:'+sid,g['basis'],'本段九宫格',self._continuous_image_rule(eid,vid))
            for d in self._continuous_record(cid)['cursor'].get('productDrawings',{}).get(sid,[]):
                self.save_product_placement(eid,d['code'],d['shotId'],d['intent'],vid,'',f'{cid}:drawn-product:{plan_id}:{vid}:{d["code"]}:{d["shotId"]}')
            group=[s for s in plan['shots'] if s['segment']==sid]
            frames=self._grid_frames(seg,group)
            existing={r['cell']:r for r in self.grid_bindings(eid,vid)}
            for cell,frame in enumerate(frames,1):
                old=existing.get(cell,{})
                if (old.get('plan_version_id'),old.get('shot_id'))!=(plan_id,frame['shotId']):
                    self.set_grid_cell(eid,vid,cell,old.get('revision',0),plan_id,frame['shotId'],0,f'{cid}:grid-cell:{vid}:{plan_id}:{cell}')
            replacements=self._continuous_record(cid)['cursor'].get('reviewReplacements',{})
            effective_inputs={replacements.get(v,v) for v in g.get('inputs',[])}
            sources=g.setdefault('roleInputs',{})
            for role,c in next(r['cells'] for r in matrix['rows'] if r['segment']==sid).items():
                bound=c.get('assetVersionId')
                if c.get('status')!='ok' or not bound or bound==vid:continue
                if not self.asset_version(bound)['grid']:
                    sources[role]=bound
                if sources.get(role) not in effective_inputs:continue
                # The creation receipt proves the drawing source, not visual fidelity.
                self._continuous_cursor(cid,gridArtwork=owned)
                self.set_cast(eid,sid,role,'ok',vid,note='本段九宫格以已绑定的角色参考版本绘制：'+sources[role],opening_confirmed=True,command_id=f'{cid}:board-cast:{sid}:{role}:{vid}')
        gridcontinuity.reconcile(self,cid,eid,plan_id,owned)
        return {s['id']:owned[s['id']] for s in plan['segments'] if s['strategy']=='grid'}

    def _episode_image_review(self,cid,eid,rows,grids=None):
        cursor=self._continuous_record(cid)['cursor']
        review=cursor.get('episodeImageReview',{})
        signature={n['need_key']:n['adopted_version_id'] for n in rows if n['adopted_version_id']}
        signature.update({'grid:'+sid:g['versionId'] for sid,g in (grids or {}).items()})
        if review.get('done') and review.get('protocol')==3:
            if review.get('current')==signature:return
            review={}
        if review.get('done'):
            if review.get('protocol')==2:
                review={}
        if review.get('done'):
            # A legacy 07 receipt may predate the artwork produced by 08.
            seen=set(review.get('selected',{}).values()) | set(review.get('finalVersions',[]))
            if all(g['versionId'] in seen for g in (grids or {}).values()):return
            review={}
        if not review:
            # Only new episode artwork needs a visual pass. Preserve earlier independent assessments on resume,
            # while never treating expert state-observation backfill (including legacy records) as independent review.
            def _is_observation_only(rec):
                kind = rec['body'].get('recordKind')
                if kind == 'observation': return True
                if kind == 'independent_review': return False
                try:
                    with connect(self.db_path) as db:
                        r = db.execute('SELECT manifest_relpath FROM run WHERE id=?', (rec['run_id'],)).fetchone()
                    if not r or not r['manifest_relpath']: return False
                    manifest, _ = self.store.load_manifest(r['manifest_relpath'])
                    spec = manifest.get('imageAssessment') or {}
                    if spec.get('coarse'): return False
                    if spec.get('observationOnly'): return True
                    for entry in self.store.manifest_texts(manifest):
                        if entry.get('role') == 'image_assess_context' and '复看这张已采用的状态图' in (entry.get('text') or ''):
                            return True
                except Exception:
                    return False
                return False
            known={r['version_id'] for r in self.image_assessments(eid) if not _is_observation_only(r)}
            selected={}
            for n in rows:
                vid=n['adopted_version_id']
                state=cursor.get('images',{}).get(n['need_key'],{})
                if vid and vid not in known and n['decision']!='reuse' and vid not in state.get('libraryEntries',{}):
                    selected[n['need_key']]=vid
            grid_uses=[]
            for sid,g in (grids or {}).items():
                if g['versionId'] not in known:
                    selected['grid:'+sid]=g['versionId']
                    grid_uses.append(dict(versionId=g['versionId'],title=g['title']))
            if not selected:
                self._continuous_cursor(cid,episodeImageReview=dict(done=True,protocol=3,current=signature))
                return
            job=self.prepare_image_assess(eid,list(dict.fromkeys(selected.values())),cid+':whole-image-review:'+sha256_bytes(canonical(selected)),context=dict(gridPurposes=grid_uses),episode_review=True)
            review=dict(protocol=3,runId=job['runId'],selected=selected)
            self._continuous_cursor(cid,episodeImageReview=review)
        self._continuous_cursor(cid,stage='assets',phase='image_review')
        if 'fixes' not in review:
            for attempt in range(2):
                self._continuous_execute(cid,review['runId'])
                actual=self._continuous_record(cid)['cursor']['runId']
                try:
                    assessments=self.finish_image_assess(actual)
                    break
                except Rejected as exc:
                    if review.get('invalidRun'):
                        from .continuous import Halt
                        self._continuous_update(cid,state='failed',reason='独立评图补交后仍无法读取：'+str(exc))
                        raise Halt()
                    uses=[dict(versionId=vid,title=(grids or {})[key[5:]]['title'])
                          for key,vid in review['selected'].items() if key.startswith('grid:') and key[5:] in (grids or {})]
                    retry=self.prepare_image_assess(eid,list(dict.fromkeys(review['selected'].values())),
                        cid+':image-review-correction:'+actual,context=dict(gridPurposes=uses,correction=str(exc)),episode_review=True)
                    review.update(invalidRun=actual,runId=retry['runId'])
                    self._continuous_cursor(cid,episodeImageReview=review)
            by_id={a['versionId']:a for a in assessments}
            def validate_choices(answer):
                if not isinstance(answer,dict) or set(answer)!=set(by_id):
                    return ['每个本轮图版须各有一个决定']
                for vid,decision in answer.items():
                    if not isinstance(decision,dict) or decision.get('action') not in ('use','repair') or not str(decision.get('reason') or '').strip():
                        return ['请写明采用或修图及理由']
                    if by_id[vid]['critical'] and decision['action']=='use':
                        return ['严重缺陷的图版须先修图']
                return []
            choices,_=self._continuous_bounded_json(cid,'选图决定',review['runId'],
                '阅读本轮实际图版的独立评审。reply 按每个 versionId 返回 {"action":"use 或 repair","reason":"依据或具体修法"}。严重缺陷必须修图。',
                assessments, validate_choices, stage='asset_design')
            review['decisions']=choices
            for vid,decision in choices.items():
                if decision['action']=='repair' and not by_id[vid]['critical']:
                    by_id[vid]['critical']=[dict(type='other',where='画面',text=decision['reason'])]
            for key,vid in review['selected'].items():
                if key.startswith('grid:'):continue
                need=self._need(eid,key)
                if need['adopted_version_id']==vid and not by_id[vid]['critical']:
                    self._image_choose(cid,need,dict(by_id[vid],reason=choices[vid]['reason']))
            review['fixes']={key:by_id[vid]['critical'] for key,vid in review['selected'].items() if by_id[vid]['critical']}
            self._continuous_cursor(cid,episodeImageReview=review)
        fixes=list(review['fixes'].items())
        if fixes:
            # A corrected board must use the actual repaired sources, including on resume.
            for grid_pass in (False,True):
                group=[entry for entry in fixes if entry[0].startswith('grid:')==grid_pass]
                if not group:continue
                waits=[];failures=[]
                with ThreadPoolExecutor(max_workers=min(len(group),self._image_parallelism(cid))) as pool:
                    for (key,_),job in zip(group,[pool.submit(self._repair_review_image,cid,eid,review,entry) for entry in group]):
                        try:
                            result=job.result()
                            if result:failures.append(result)
                        except MaterialWait as exc:
                            waits.append((key,exc))
                        except (Conflict,Rejected) as exc:
                            self._continuous_checkpoint(cid)
                            failures.append((key,str(exc)))
                if waits:
                    self._continuous_problem(cid,'；'.join(exc.reason for _,exc in waits),
                        blocks=[self._recovery_block('need:'+key,**exc.waiting()) for key,exc in waits])
                if failures and grid_pass:
                    plan_id=self._continuous_art(eid,'plan')['adopted_version_id']
                    plan=self._continuous_version(plan_id)
                    failed={k[5:] for k,_ in failures}
                    segments=[dict(seg,strategy='flex') if seg['id'] in failed else seg for seg in plan['segments']]
                    if segments!=plan['segments']:
                        art=self._continuous_art(eid,'plan')
                        update=self.save_manual(art['id'],plan_id,dict(segments=segments),f'{cid}:repair-flex:{plan_id}',note='修图未取得九宫格，沿用本段独立参考')
                        new_plan=self._continuous_internal_version(cid,update['versionId'])
                        from .workflow import binding_candidate
                        if candidate:=binding_candidate.get():
                            candidate['version']=new_plan
                        else:
                            self._continuous_adopt(cid,'plan',new_plan)
                        self._continuous_products(cid)
                elif failures:
                    remaining=[]
                    for key,reason in failures:
                        need=self._need(eid,key)
                        vid=review['selected'][key]
                        if need['adopted_version_id'] not in (None,vid):
                            with cursor_lock(cid):
                                replacements=self._continuous_record(cid)['cursor'].get('reviewReplacements',{})
                                self._continuous_cursor(cid,reviewReplacements={**replacements,vid:need['adopted_version_id']})
                            continue
                        remaining.append((key,reason))
                    if not remaining:continue
                    from .continuous import Halt
                    self._continuous_update(cid,state='failed',reason='局部修图及重出均未取得素材：'+dumps(remaining))
                    raise Halt()
            try:
                self._apply_review_replacements(cid,eid)
            except (Conflict,Rejected) as exc:
                self._continuous_checkpoint(cid)
                from .continuous import Halt
                self._continuous_update(cid,state='failed',reason='修正版绑定无法接续：'+str(exc))
                raise Halt()
            self._image_fill_records(cid)
            self._grid_artwork(cid,eid,self._continuous_art(eid,'plan')['adopted_version_id'])
        finals=self._continuous_record(cid)['cursor'].get('gridArtwork',{})
        current={n['need_key']:n['adopted_version_id'] for n in self.asset_worksheet(eid)['rows'] if n['adopted_version_id']}
        current.update({'grid:'+sid:g['versionId'] for sid,g in finals.items()})
        self._continuous_cursor(cid,episodeImageReview=dict(review,protocol=3,done=True,current=current,finalVersions=[g['versionId'] for g in finals.values()]))

    def _repair_review_image(self,cid,eid,review,entry):
        key,issues=entry
        original=review['selected'][key]
        cursor=self._continuous_record(cid)['cursor']
        if original in cursor.get('reviewReplacements',{}):return
        grid=key.startswith('grid:')
        if grid:
            sid=key[5:]
            artwork=cursor['gridArtwork'].get(sid)
            if not artwork or artwork['versionId']!=original:return
            inputs=artwork['inputs']
            prompt=artwork['prompt']
        else:
            need=self._need(eid,key)
            if need['adopted_version_id'] not in (None,original):
                with cursor_lock(cid):
                    changes=self._continuous_record(cid)['cursor'].get('reviewReplacements',{})
                    self._continuous_cursor(cid,reviewReplacements={**changes,original:need['adopted_version_id']})
                return
            inputs=[r['adopted_version_id'] for r in self.asset_worksheet(eid)['rows']
                    if r['need_key'] in need['depends_on'] and r['adopted_version_id']]
            with connect(self.db_path) as db:
                receipt=db.execute('SELECT r.*,b.spec FROM image_request r JOIN image_batch b ON b.id=r.batch_id WHERE r.asset_version_id=?',(original,)).fetchone()
            if receipt:
                inputs=json.loads(receipt['spec'])['items'][self._original_ordinal(receipt)-1].get('inputs',[])
            prompt=need['image_prompt'] or need['description']
        changes=cursor.get('reviewReplacements',{})
        inputs=list(dict.fromkeys(changes.get(v,v) for v in inputs if v!=original))
        asset=self.asset_version(original)
        target=dict(assetId=asset['asset_id'],name=asset['name'],category=asset['category'],
                    roles=asset['roles'],grid=bool(asset['grid']),cellsMeta=asset['cells'])
        opts=self._image_options(eid,self._image_channel(cid,eid))
        operations=self.image_providers()[opts['provider']]['capabilities']['operations']
        revised=None
        errors=[]
        for operation in ('edit','generate'):
            if operation not in operations or (operation=='edit' and len(inputs)>=16):continue
            try:
                command=f'{cid}:repair-image:{original}:{operation}'
                with connect(self.db_path) as db:
                    saved=db.execute('SELECT result FROM command WHERE command_id=?',(command,)).fetchone()
                if saved:
                    job=json.loads(saved['result'])
                else:
                    if revised is None:
                        revised=self._reviewed_image_prompt(cid,eid,key,prompt,issues,'局部修图')
                    item=dict(target=target,prompt=revised,inputs=inputs,segments=[sid] if grid else [])
                    if operation=='edit':
                        item.update(inputs=[original,*inputs],prompt='以第一张为底图。'+revised)
                    job=self.create_batch(eid,operation=operation,items=[item],command_id=command,**opts)
                batch=self.batch_view(job['batchId'])
                if not batch['authorization']:
                    self.authorize_batches(eid,{batch['id']:batch['summary_hash']},cid+':repair-auth:'+batch['id'])
                if self.batch_view(batch['id'])['state'] in ('authorized','partial'):
                    self.execute_batch(batch['id'])
                batch=self.batch_view(batch['id'])
            except Rejected as exc:
                errors.append(str(exc))
                continue
            req=next(r for r in batch['requests'] if not r.get('replaced'))
            if req['state']=='succeeded':
                fixed=req['asset_version_id']
                break
            if req['state'] not in ('failed','confirmed_not_executed','cancelled'):
                raise MaterialWait('修图等待原请求回执',kind='image_wait',role='image',batchIds=[batch['id']])
            error=req.get('error') or req['state']
            external='quota' if 'insufficient_quota' in error else 'auth' if '本机Codex尚未登录' in error else None
            if error in (INTERRUPTED_IMAGE, TRANSPORT_FAILURE):external='transient'
            if external and (external=='transient' or not req.get('replaces_id')):
                provider='codex' if external=='auth' else batch['provider']
                block=self._recovery_block('need:'+key,external,provider=provider,role='image',batchIds=[batch['id']])
                if block['id'] in (cursor.get('autoScope') or []) or (external=='transient' and cursor.get('autoScope') is None and cursor.get('resumedAt')):
                    self.resubmit_request(req['id'],f'{cid}:repair-resume:{req["id"]}')
                    self.execute_batch(batch['id'])
                    req=next(r for r in self.batch_view(batch['id'])['requests'] if not r.get('replaced'))
                    if req['state']=='succeeded':
                        fixed=req['asset_version_id']
                        break
                    if req['state'] not in ('failed','confirmed_not_executed','cancelled'):
                        raise MaterialWait('修图等待补发请求回执',kind='image_wait',role='image',batchIds=[batch['id']])
                    error=req.get('error') or req['state']
                else:
                    raise MaterialWait(error,kind=external,provider=provider,role='image',batchIds=[batch['id']])
            if error in (INTERRUPTED_IMAGE, TRANSPORT_FAILURE):
                raise MaterialWait(error,kind='transient',provider=batch['provider'],role='image',batchIds=[batch['id']])
            errors.append(error)
            if 'insufficient_quota' in error or '本机Codex尚未登录' in error:
                return key,error
        else:
            return key,'；'.join(errors) or '通道没有可用的修图方式'
        if not grid:
            with transaction(self.db_path) as db:
                db.execute('INSERT OR IGNORE INTO asset_need_candidate VALUES(?,?)',(need['id'],fixed))
            if need['adopted_version_id']==original:
                self.adopt_asset_needs(eid,{key:dict(versionId=fixed,revision=need['revision'])},f'{cid}:repair-adopt:{key}:{fixed}')
            else:
                fixed=need['adopted_version_id']
                if not fixed:raise Conflict('修图期间工作单采用状态已变化')
            self._image_state(cid,key,selected=fixed)
        with cursor_lock(cid):
            cursor=self._continuous_record(cid)['cursor']
            changes={**cursor.get('reviewReplacements',{}),original:fixed}
            if grid:cursor['gridArtwork'][sid]['versionId']=fixed
            self._continuous_cursor(cid,gridArtwork=cursor.get('gridArtwork',{}),reviewReplacements=changes)


    def _apply_review_replacements(self,cid,eid):
        changes=self._continuous_record(cid)['cursor'].get('reviewReplacements',{})
        if not changes:return
        art=self._continuous_art(eid,'plan');plan_id=art['adopted_version_id']
        plan=self._continuous_version(plan_id)
        shots=[{k:(changes.get(v,v) if k in ('startAssetId','endAssetId') else v) for k,v in shot.items()} for shot in plan['shots']]
        if shots!=plan['shots']:
            result=self.save_manual(art['id'],plan_id,dict(shots=shots),f'{cid}:repair-opening:{plan_id}:'+sha256_bytes(canonical(changes)),note='沿用分镜，用修正版替换原参考图')
            plan_id=self._continuous_internal_version(cid,result['versionId'])
            from .workflow import binding_candidate
            if candidate:=binding_candidate.get():
                candidate['version']=plan_id
            else:
                self._continuous_adopt(cid,'plan',plan_id)
        for r in self.cast_matrix(eid)['rows']:
            for role,cell in r['cells'].items():
                old=cell.get('assetVersionId')
                if old in changes and cell.get('status')=='ok':
                    self.set_cast(eid,r['segment'],role,'ok',changes[old],note=cell.get('note') or '沿用本段身份绑定，替换修正版',
                                  cells=cell.get('cells',[]),opening_confirmed=cell.get('depictsMoment',{}).get('segment')==r['segment'],command_id=f'{cid}:repair-cast:{r["segment"]}:{role}:{changes[old]}')
        for lock in self.space_locks(eid):
            old=lock['versionId']
            if lock['status']=='confirmed' and old in changes:
                self.confirm_space_item(lock['id'],changes[old],command_id=f'{cid}:repair-lock:{lock["id"]}:{changes[old]}')
        artwork=self._continuous_record(cid)['cursor'].get('gridArtwork',{})
        for board in artwork.values():
            board['roleInputs']={role:changes.get(vid,vid) for role,vid in board.get('roleInputs',{}).items()}
        self._continuous_cursor(cid,gridArtwork=artwork)
        with transaction(self.db_path) as db:
            for old,new in changes.items():
                # A board can temporarily cover an explicitly confirmed state in the cast matrix.
                # Preserve its opening for a later grid-to-flex transition as well.
                image=self.asset_version(new,db)
                if image['category']=='state':
                    db.execute("""INSERT OR IGNORE INTO cast_opening(episode_id,sha256,segment_id)
                                  SELECT c.episode_id,?,c.segment_id FROM cast_opening c JOIN asset_version v ON v.sha256=c.sha256
                                  WHERE c.episode_id=? AND v.id=?""",(image['sha256'],eid,old))
                source=db.execute("SELECT id FROM image_choice WHERE episode_id=? AND asset_version_id=? AND mark='required'",(eid,old)).fetchone()
                if not source:continue
                target=db.execute("SELECT id FROM image_choice WHERE episode_id=? AND asset_version_id=? AND mark='required'",(eid,new)).fetchone()
                if target:raise Conflict('修正版已有独立的必用范围，需重新匹配')
                db.execute('UPDATE image_choice SET asset_version_id=? WHERE id=?',(new,source['id']))
                db.execute("UPDATE required_image_details SET details=json_set(details,'$.sha256',?,'$.fitness','not_tested') WHERE choice_id=?",(self.asset_version(new,db)['sha256'],source['id']))

    def _continuous_images(self,cid):
        value=self._continuous_checkpoint(cid);eid=value['episode_id']
        self._continuous_cursor(cid,stage='assets',phase='images')
        design=self._continuous_art(eid,'asset_design')
        if not design or not design['adopted_version_id']:self._continuous_stage(cid,'asset_design')
        design=self._continuous_art(eid,'asset_design');vid=design['adopted_version_id']
        content=next(v['content'] for v in design['versions'] if v['id']==vid)
        note_errors=self._continuous_note_errors(eid,'asset_design',content)
        if note_errors:
            self._continuous_cursor(cid,stage='asset_design')
            self._continuous_problem(cid,'按当前06报告补齐制作提醒回应：'+'；'.join(note_errors))
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
            if not pending:
                return self._image_fill_records(cid)
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
            for key,w in waiting.items():
                if not w['running']:continue
                state=self._image_state(cid,key)
                if state.get('pendingBatch') or state.get('parkedQueue'):continue
                # No request can wake this worker; use a bounded transient retry.
                w.update(running=False,kind='transient',role='image',reason='生图通道未登记可追踪的请求：'+w['reason'])
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
            self._continuous_problem(cid,'；'.join(dict.fromkeys(reasons)),blocks=blocks)

    def _material_bottleneck(self,cid,need,wait):
        eid=need['episode_id'];key=need['need_key'];state=self._image_state(cid,key)
        reason=wait.get('reason','') if isinstance(wait,dict) else str(wait)
        # The scheduler handles live requests before considering a local fallback.
        if need.get('missing') or state.get('pendingBatch') or (isinstance(wait,dict) and wait.get('running')):
            return False
        if not need['image_prompt'] and not state.get('inputFilled'):
            prompt='生成'+need['title']+'的制作参考图。'+(need.get('description') or '')
            self.set_asset_need(eid,key,need['revision'],'edit_prompt',f'{cid}:fill:{key}:{state.get("epoch",0)}',prompt=prompt)
            self._image_state(cid,key,inputFilled=True)
            return True
        self._material_issue(cid,need,text=reason,state='missing')
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
            observation=(values[-1].get('observation') or '').strip() if values else ''
            if CATEGORIES.get(need['kind'])=='identity' and need['role_id'] and need['state_id'] in (None,'','default','base'):
                with connect(self.db_path) as db:fixed=bool(db.execute('SELECT 1 FROM fixed_role_asset WHERE episode_id=? AND role=? AND asset_version_id=?',(eid,need['role_id'],vid)).fetchone())
                appearance='' if fixed else need['description'].strip() or observation.split('。')[0]
                with connect(self.db_path) as db:
                    existing=db.execute('SELECT appearance FROM episode_role_baseline WHERE episode_id=? AND role=? AND asset_version_id=?',(eid,need['role_id'],vid)).fetchone()
                baseline=dict(appearance=existing['appearance']) if existing else self.set_role_baseline(eid,need['role_id'],vid,appearance,f'{cid}:baseline:{vid}')
                appearance=baseline['appearance']
                self._continuous_log(cid,'assets','fill_observation',vid,'采用身份图并登记外观',dict(observation=appearance))
            for state in matrix['roleStates']:
                if (state['role'],state['stateId'])!=(need['role_id'],need['state_id']):continue
                if state['confirmed'] and state.get('imageId')==vid:continue
                if not observation:
                    checked=self.assess_images(eid,[vid],f'{cid}:observe-state:{state["designHash"]}:{vid}',need_key=need['need_key'],
                        context=dict(observationOnly=True,instruction='复看这张已采用的状态图，在 observation 写明图中实际可见的角色外观、状态及限制。不重选、不重新出图。'),continuous_id=cid)
                    observation=checked[0]['observation'].strip()
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
                if selection is None:self._continuous_problem(cid,'空间锁定选图回复格式不合格')
            if selection and selection['versionId']:
                self.confirm_space_item(lock['id'],selection['versionId'],command_id=f'{cid}:lock:{lock["id"]}:{selection["versionId"]}')
                self._continuous_log(cid,'assets','confirm_lock',lock['id'],selection['reason'],dict(versionId=selection['versionId']))
            else:
                cursor=self._continuous_record(cid)['cursor'];supplemented=cursor.get('lockSupplements',[])
                if lock['id'] in supplemented:self._continuous_problem(cid,'补图后仍无适配空间锁定的场景图：'+lock['label'])
                art=self._continuous_art(eid,'asset_design')
                self._continuous_create(cid,'asset_design','保留现有条目，为以下空间锁定补充一张场景图条目，标明剧本场号和需要呈现的空间关系：'+dumps(lock),base=art['adopted_version_id'])
                version=self._continuous_art(eid,'asset_design')['versions'][-1]['id']
                self._continuous_adopt(cid,'asset_design',version)
                self._continuous_cursor(cid,lockSupplements=[*supplemented,lock['id']])
                return self._continuous_images(cid)
        with connect(self.db_path) as db:
            unfinished=db.execute("SELECT r.id,r.batch_id FROM image_request r JOIN image_batch b ON b.id=r.batch_id WHERE b.episode_id=? AND b.state!='cancelled' AND r.state IN ('pending','submitted','unknown') LIMIT 1",(eid,)).fetchone()
        if unfinished:self._continuous_problem(cid,'仍有进行中或待核对的图片请求：'+unfinished[0],blocks=[self._recovery_block('run:assets:image','image_wait',role='image',batchIds=[unfinished[1]])])
        unconfirmed=[r for r in self.cast_matrix(eid)['roleStates'] if not r['confirmed'] and not r.get('textOnly')]
        if unconfirmed:self._continuous_problem(cid,'角色状态尚未完成对图：'+'、'.join(r['role']+'/'+r['stateId'] for r in unconfirmed))
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
            self._continuous_cursor(cid,imageFeedbackReply=result.get('reply',''))
            return True
        self._continuous_adopt(cid,'asset_design',latest['id'])
        key=feedback['choice']['needKey'];need=self._need(eid,key)
        self.set_asset_need(eid,key,need['revision'],'generate',feedback['commandId']+':generate',prompt=need['image_prompt'])
        state=self._image_state(cid,key)
        self._image_state(cid,key,priorAttempts=[*state.get('priorAttempts',[]),*state['attempts']],attempts=[],epoch=new_id(),assessedRevision=None,keyVisualSupplemented=False,extraRequested=False)
        self._continuous_log(cid,'assets','apply_user_change',key,'按用户原话修订素材并重新评估',dict(instruction=feedback['text'],versionId=latest['id']))
        return True
