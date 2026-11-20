"""Persistent recovery conditions; probes run outside database write transactions."""
import json
import re
from datetime import datetime
from .db import connect, transaction, dumps, new_id
from .errors import Conflict, row
from .stressrepairs import adopted
from .continuous_images import cursor_lock

LIMIT = dict(auth=3, quota=6, transient=3, image_wait=3)
GAP = dict(auth=60, quota=1800, transient=30, image_wait=30)
INTERRUPTED = ('服务重启时中断', '已收到回包，入库完成前服务中断', '已收到回包，恢复时无法核验')
ENDED_BATCHES = ('completed', 'failed', 'partially_succeeded', 'cancelled')


def parse_resets(run):
    value = run.get('quota_resets_at')
    if not value:
        return None
    match = re.fullmatch(r'in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?', str(value))
    if match:
        base = run.get('ended_at') or run.get('started_at')
        return base + sum(int(n or 0)*unit for n,unit in zip(match.groups(), (3600,60,1))) if base else None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


class ContinuousRecovery:
    def _recovery_block(self, scope, kind, provider=None, role=None, batchIds=(), resetsAt=None, **ignored):
        if not kind:
            return None
        batches = sorted(set(batchIds or ()))
        return dict(id=f'{scope}|{kind}|'+('b:'+','.join(batches) if batches else str(provider or '')),
                    scope=scope, kind=kind, provider=provider, role=role, batchIds=batches,
                    resetsAt=resetsAt, at=self.clock(), lastTry=None, **{k:ignored[k] for k in ('model','effort') if k in ignored})

    def _continuous_run_role(self, run):
        purpose = run.get('purpose') or run.get('role')
        role = {'revise':'create','parallel':'create','cold_read':'review','comparison':'review','proposal':'expert'}.get(purpose,purpose)
        if not run.get('task_id'):
            return run.get('stage','kickoff'), role
        with connect(self.db_path) as db:
            task = row(db,'SELECT stage,executor_plan FROM task WHERE id=?',run['task_id'])
        if role=='image_assess' and run.get('manifest_relpath'):
            manifest,_=self.store.load_manifest(run['manifest_relpath'])
            if manifest.get('imageAssessment',{}).get('coarse'):
                role='review'
        if role == 'create':
            role = json.loads(task['executor_plan']).get('creationRole',role)
            if task['stage']=='prompts' and run.get('manifest_relpath'):
                manifest, _ = self.store.load_manifest(run['manifest_relpath'])
                role = manifest.get('segmentRepair',{}).get('role',role)
        return task['stage'], role

    def _run_blocks(self, run, scope=None):
        reason = run.get('reason') or run.get('errorClass')
        kind = 'quota' if run.get('status')=='waiting_quota' or reason=='quota' else {
            'authentication':'auth', 'network':'transient', 'timeout':'transient', 'interrupted':'transient'}.get(reason)
        stage, role = self._continuous_run_role(run)
        block = self._recovery_block(scope or f'run:{stage}:{role}', kind,
                                     provider=run.get('provider'), role=role, resetsAt=parse_resets(run))
        if block:
            block.update(model=run.get('requested_model',run.get('model')), effort=run.get('requested_effort',run.get('effort')))
        return [block] if block else []

    def _claim_resume(self, db, cid, auto=None, now=None):
        value = row(db, 'SELECT * FROM continuous_run WHERE id=?', cid)
        allowed = (auto is None and value['state'] in ('paused','failed')) or (auto is not None and value['state'] in ('running','waiting_external'))
        if not allowed or (auto is not None and value['pause_requested']):
            if auto is not None:
                return None
            raise Conflict('当前状态不能继续')
        cursor = json.loads(value['cursor'])
        if value['state']=='failed':
            newer = db.execute("SELECT 1 FROM continuous_run WHERE episode_id=? AND id<>? AND (created_at>? OR state IN ('running','paused','waiting_external')) LIMIT 1",
                               (value['episode_id'],cid,value['created_at'])).fetchone()
            if newer:
                raise Conflict('本集已有更新的连续制作，请从最新进度继续')
            if cursor.get('stage')=='prompts':
                from .promptrepairs import retained_writer_notes
                corrected=retained_writer_notes(db,cursor)
                if corrected:
                    cursor.setdefault('ignoredVersions',[]).append(cursor['promptCandidate'])
                    cursor.update(promptCandidate=corrected,queue=[],nextRepair=None)
            # Earlier builds discarded the base on the third repair even when only review dispatch failed.
            # Recover a complete owned draft only from that failed chain. Later director edits take priority below.
            history = cursor.get('repairHistory',{}).get('story:story',[])
            if cursor.get('stage')=='story' and any(attempt.get('method')=='rebuild' and '会话隔离未验证' in attempt.get('reason','') for attempt in history):
                from .storycheck import check_story
                drafts = db.execute("SELECT v.id,v.content,v.source_run_id FROM version v JOIN artifact a ON a.id=v.artifact_id WHERE a.episode_id=? AND a.stage='story' ORDER BY v.display_no DESC",(value['episode_id'],))
                ignored = []
                for draft in drafts:
                    if draft['source_run_id'] not in cursor.get('ownedRuns',[]):break
                    content = json.loads(draft['content'])
                    if not check_story(content) and content.get('title'):
                        cursor['ignoredVersions'] = list(dict.fromkeys(cursor.get('ignoredVersions',[])+ignored))
                        cursor.update(queue=[],nextRepair=None,runId=draft['source_run_id'])
                        break
                    ignored.append(draft['id'])
            cursor.get('repairHistory',{}).pop(str(cursor.get('stage'))+':'+str(cursor.get('stage')),None)
        previous=db.execute('SELECT status,reason FROM run WHERE id=?',(cursor.get('runId'),)).fetchone()
        interrupted=previous and previous['status']!='succeeded'
        if not interrupted and cursor.get('judgmentKind') in ('素材用途接续','引用用途核对') and cursor.get('phase')=='judgment':
            first='prompt-material-v1' not in cursor.get('retiredGates',{})
            cursor.setdefault('retiredGates',{}).setdefault('prompt-material-v1',dict(
                kind=cursor['judgmentKind'],runId=cursor.get('runId'),reason=value['reason'],status='no_longer_required'))
            if first:
                db.execute('INSERT INTO ai_decision(id,episode_id,continuous_id,stage,kind,ref,reason,detail,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (new_id(),value['episode_id'],cid,'prompts','decide_skip',cursor.get('promptCandidate'),
                     '旧素材审核门槛不再适用；继承候选和上游采用结论，继续提示词组装与独审',
                     dumps(cursor['retiredGates']['prompt-material-v1']),self.clock()))
            # Keep old receipts and candidate intact; no invented pass result.
            cursor['phase']='draft'
            cursor['judgmentKind']=None
        self._claim_retry_descendants(db,cid,cursor)
        actual = self._continuous_user_stamp(db,value['episode_id'],cursor['ownedRuns'])
        if actual!=cursor.get('userStamp',actual) or adopted(db,value['episode_id'])!=cursor['base']:
            self._continuous_reconcile(db,value['episode_id'],cursor)
        cursor.update(userStamp=actual, base=adopted(db,value['episode_id']), retried={},
                      resumedAt=self.clock() if now is None else now,
                      # Claude 复查：重启恢复（auto=[]）不是“只认领部分受阻项”，不能把旧受阻条目全部挂起。
                      autoScope=auto if auto else None)
        cursor.pop('resumeAfterRestart',None)
        cursor.pop('pendingBlocks',None)
        if auto is None:
            cursor['formatRound']=cursor.get('formatRound',0)+1
            cursor['wakeCounts']={}
            for image in cursor.get('images',{}).values():image['interruptionRetries']=0
            cursor.pop('stoppedNeeds',None)
        else:
            counts=cursor.setdefault('wakeCounts',{})
            for b in cursor.get('blocks',[]):
                if b['id'] in auto:
                    counts[b['id']]=counts.get(b['id'],0)+1
                    b['lastTry']=self.clock() if now is None else now
        db.execute("UPDATE continuous_run SET cursor=?,state='running',choice=NULL,pause_requested=0,reason=NULL,updated_at=? WHERE id=?",
                   (dumps(cursor),self.clock(),cid))
        return cid

    def restart_continuous_runs(self):
        ready=[]
        with transaction(self.db_path) as db:
            for r in db.execute("SELECT id,cursor FROM continuous_run WHERE state='running' AND pause_requested=0").fetchall():
                if json.loads(r['cursor']).get('resumeAfterRestart') and self._claim_resume(db,r['id'],auto=[]):
                    ready.append(r['id'])
            # A retired 09 merge could fail an ongoing expert run after its writer had already fixed it.
            from .promptrepairs import retained_writer_notes
            failed=db.execute("""SELECT c.id,c.cursor FROM continuous_run c WHERE c.state='failed'
                AND c.pause_requested=0 AND c.mode='full' AND json_extract(c.cursor,'$.stage')='prompts'
                AND NOT EXISTS (SELECT 1 FROM continuous_run n WHERE n.episode_id=c.episode_id AND n.rowid>c.rowid)
                AND NOT EXISTS (SELECT 1 FROM continuous_run n WHERE n.episode_id=c.episode_id
                    AND n.state IN ('running','paused','waiting_external'))""").fetchall()
            for r in failed:
                cursor=json.loads(r['cursor'])
                if retained_writer_notes(db,cursor) and self._claim_resume(db,r['id']):
                    ready.append(r['id'])
        return ready

    def _block_ready(self, b, now, probes, counts):
        kind=b['kind']
        # Exhausted probes slow down; changed external conditions remain recoverable.
        if counts.get(b['id'],0)>=LIMIT[kind] and b.get('lastTry') is not None and now-b['lastTry']<max(60,GAP[kind]):return False
        if b.get('lastTry') is not None and now-b['lastTry']<GAP[kind]:return False
        since=b.get('lastTry') if b.get('lastTry') is not None else b['at']
        if kind=='auth':return probes.get('switched',False) or probes.get('identity',{}).get(b.get('provider')) is True
        if kind=='quota':return now>=b['resetsAt'] if b.get('resetsAt') is not None else now-since>=1800
        if kind=='transient':return now-since>=GAP[kind]
        return bool(b.get('batchIds')) and all(probes.get('batches',{}).get(bid,False) for bid in b['batchIds'])

    def _recovery_probes(self, snapshots, now):
        batches=set()
        providers=set()
        for r in snapshots:
            c=r['cursor']
            for b in c.get('blocks',[]):
                batches.update(b.get('batchIds',[]))
                if b['kind']=='auth':providers.add(b.get('provider'))
            if r['state']=='running':
                batches.update(i['pendingBatch'] for i in c.get('images',{}).values() if i.get('pendingBatch'))
        if batches:self.settle_unknown_requests(sorted(batches))
        identities={}
        # The probe lock also coalesces timer / POST checks; never held with a DB write lock.
        with self._recovery_probe_lock:
            for provider in providers:
                cached=self._recovery_identity_cache.get(provider)
                if not cached or now-cached[0]>=60:
                    try:authenticated=self.executor_identity(provider).get('authenticated')
                    except Exception:authenticated=None
                    cached=(now,authenticated)
                    self._recovery_identity_cache[provider]=cached
                identities[provider]=cached[1]
        ended={}
        with connect(self.db_path) as db:
            for bid in batches:
                batch=db.execute('SELECT state FROM image_batch WHERE id=?',(bid,)).fetchone()
                live=db.execute("SELECT 1 FROM image_request WHERE batch_id=? AND state IN ('submitted','unknown')",(bid,)).fetchone()
                ended[bid]=bool(batch and batch[0] in ENDED_BATCHES and not live)
        return dict(identity=identities,batches=ended)

    def _provider_switched(self, db, r, b):
        role=b.get('role')
        if role == 'image' or not role or not b.get('provider'):return False
        c=r['cursor']
        if role=='kickoff':
            plan=c.get('startArgs',{}).get('executor_plan',{})
            slot=plan.get('expert') or plan.get('create',{})
        elif role in ('image_assess','expert'):
            slot=self.episode_expert(r['episode_id'])
        else:
            plan={**self._kickoff(db,r['episode_id'])['executor_plan'],
                  **self._stage_executors(db,r['episode_id'],b['scope'].split(':')[1] if b['scope'].startswith('run:') else c.get('stage','story'))}
            slot=plan.get(role,{})
        if slot.get('provider'):
            cfg=self.run_connection(slot['provider'],slot.get('model'),slot.get('effort'))
            slot=dict(slot,model=cfg['model'],effort=cfg['effort'])
        if b['kind']=='quota' and slot.get('provider')!=b.get('provider') and self.get_setting('quota_retry_policy')=='same_cli':
            return False
        return bool(slot.get('provider') and (slot['provider']!=b.get('provider') or
                    'model' in b and any(slot.get(key) != b.get(key) for key in ('model','effort'))))

    def changed_continuous_runs(self, now=None):
        now=self.clock() if now is None else now
        with connect(self.db_path) as db:
            snapshots=[dict(r) for r in db.execute("SELECT * FROM continuous_run WHERE state IN ('running','waiting_external')")]
        for r in snapshots:r['cursor']=json.loads(r['cursor'])
        probes=self._recovery_probes(snapshots,now)
        ready=[]
        with transaction(self.db_path) as db:
            for snapshot in snapshots:
                r=dict(row(db,'SELECT * FROM continuous_run WHERE id=?',snapshot['id']))
                cursor=json.loads(r['cursor']);r['cursor']=cursor
                if r['pause_requested']:continue
                if r['state']=='waiting_external':
                    if cursor.get('blocks')!=snapshot['cursor'].get('blocks'):continue
                    blocks=cursor.get('blocks',[])
                    deadlines={'auth':3600,'transient':3600,'image_wait':7200,'quota':86400}
                    expired=next((b for b in blocks if now>=b['at']+deadlines[b['kind']]
                                  or cursor.get('wakeCounts',{}).get(b['id'],0)>=LIMIT[b['kind']]),None)
                    # Conditions are evaluated per item: run any still-eligible siblings first.
                    ids=[]
                    for block in blocks:
                        kind=block['kind']
                        if kind in ('auth','quota','transient') and self._provider_switched(db,r,block):
                            ids.append(block['id'])
                        elif (now<block['at']+deadlines[kind] and cursor.get('wakeCounts',{}).get(block['id'],0)<LIMIT[kind]
                              and self._block_ready(block,now,probes,cursor.get('wakeCounts',{}))):
                            ids.append(block['id'])
                    if ids:
                        if self._claim_resume(db,r['id'],auto=ids,now=now):ready.append(r['id'])
                        continue
                    if expired or not blocks:
                        db.execute("UPDATE continuous_run SET state='failed',reason=?,updated_at=? WHERE id=? AND state='waiting_external'",('外部服务未能完成请求：'+(r['reason'] or ''),now,r['id']))
                elif r['state']=='running' and snapshot['state']=='running':
                    # Polling and restarts do not extend a live request's first deadline.
                    waiting_images=[cursor.get('images',{}).get(k,{}) for k,w in cursor.get('waits',{}).items() if w.get('running')]
                    text_ids={rid for item in waiting_images for rid in item.get('parkedQueue',[])}
                    if cursor.get('waitingRun'):text_ids.add(cursor['waitingRun'])
                    live=sorted(text_ids | {item['pendingBatch'] for item in waiting_images if item.get('pendingBatch')})
                    if live:
                        previous=cursor.get('liveWaitSince',{})
                        since={key:previous.get(key,now) for key in live}
                        if since!=previous:
                            cursor['liveWaitSince']=since
                            db.execute('UPDATE continuous_run SET cursor=? WHERE id=?',(dumps(cursor),r['id']))
                        overdue=[]
                        for key in live:
                            if now-since[key]<7200:continue
                            if key in text_ids:
                                task=db.execute('SELECT status FROM run WHERE id=?',(key,)).fetchone()
                                if task and task[0] not in ('running','preparing','queued'):continue
                            elif probes['batches'].get(key):continue
                            overdue.append(key)
                        if overdue:
                            db.execute("UPDATE continuous_run SET state='failed',reason=?,updated_at=? WHERE id=? AND state='running'",('外部请求超过时限，保留原请求以核对结果：'+','.join(overdue),now,r['id']))
                            continue
                    if cursor.get('pendingUserChange'):ready.append(r['id']);continue
                    if cursor.get('waitingRun'):
                        waiting=db.execute('SELECT status FROM run WHERE id=?',(cursor['waitingRun'],)).fetchone()
                        if waiting and waiting['status'] not in ('queued','running','preparing'):
                            ready.append(r['id'])
                            continue
                    for key,wait in cursor.get('waits',{}).items():
                        if not wait.get('running'):continue
                        image=cursor.get('images',{}).get(key,{})
                        runs=[db.execute('SELECT status FROM run WHERE id=?',(rid,)).fetchone() for rid in image.get('parkedQueue',[])]
                        if probes['batches'].get(image.get('pendingBatch')) or runs and all(v and v[0] not in ('queued','running') for v in runs):
                            ready.append(r['id']);break
        return ready

    def _continuous_retry_slot(self, cid, run):
        reason=(run.get('reason') or '').split(' → 已重试为 ')[0]
        if reason not in ('authentication','quota','network','timeout','unavailable','model_not_found') or run.get('purpose')=='image':
            return {}
        value=self._continuous_record(cid)
        stage, role = self._continuous_run_role(run)
        with connect(self.db_path) as db:
            plan={**self._kickoff(db,value['episode_id'])['executor_plan'],
                  **self._stage_executors(db,value['episode_id'],stage)}
        slot=self.episode_expert(value['episode_id']) if role in ('expert','image_assess') else plan.get(role,{})
        if slot.get('provider'):
            cfg=self.run_connection(slot['provider'],slot.get('model'),slot.get('effort'))
            slot=dict(slot,model=cfg['model'],effort=cfg['effort'])
        if reason=='quota' and slot.get('provider')!=run['provider'] and self.get_setting('quota_retry_policy')=='same_cli':
            return {}
        return dict(provider=slot['provider'],model=slot.get('model'),effort=slot.get('effort'),use_selected_defaults=True) if slot.get('provider') and (slot['provider'] != run['provider'] or slot.get('model') != run.get('requested_model') or slot.get('effort') != run.get('requested_effort')) else {}

    def _restart_retry(self, cid, run):
        if run['status'] not in ('failed','stopped') or run.get('reason') not in INTERRUPTED:return None
        with cursor_lock(cid):
            c=self._continuous_record(cid)['cursor'];counts=c.setdefault('restartRetries',{})
            n=counts.get(run['id'],0)
            if n>=3:return None
            rid=self.retry_run(run['id'],command_id=f"{cid}:restart-retry:{run['id']}")['runId']
            counts[rid]=n+1
            self._continuous_update(cid,cursor=c)
            return rid
