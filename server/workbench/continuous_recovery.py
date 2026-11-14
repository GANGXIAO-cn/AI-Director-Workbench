"""Persistent recovery conditions; probes run outside database write transactions."""
import json
import re
from datetime import datetime
from .db import connect, transaction, dumps, new_id
from .errors import Conflict, row
from .stressrepairs import adopted
from .continuous_images import cursor_lock

LIMIT = dict(auth=3, quota=6, transient=3, image_wait=3)
GAP = dict(auth=60, quota=1800, transient=300, image_wait=30)
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
                    resetsAt=resetsAt, at=self.clock(), lastTry=None)

    def _run_blocks(self, run, scope=None):
        reason = run.get('reason') or run.get('errorClass')
        kind = 'quota' if run.get('status')=='waiting_quota' or reason=='quota' else {
            'authentication':'auth', 'network':'transient', 'timeout':'transient', 'interrupted':'transient'}.get(reason)
        purpose = run.get('purpose') or run.get('role')
        role = {'revise':'create','parallel':'create','cold_read':'review','comparison':'review','proposal':'expert'}.get(purpose,purpose)
        stage=run.get('stage','kickoff')
        if run.get('task_id'):
            with connect(self.db_path) as db:
                stage=row(db,'SELECT stage FROM task WHERE id=?',run['task_id'])['stage']
        block = self._recovery_block(scope or f'run:{stage}:{role}', kind,
                                     provider=run.get('provider'), role=role, resetsAt=parse_resets(run))
        return [block] if block else []

    def _commit_blocks(self, cid):
        with cursor_lock(cid):
            value = self._continuous_record(cid)
            cursor = value['cursor']
            pending = cursor.pop('pendingBlocks', None) or []
            if value['state'] in ('blocked','waiting_quota'):
                old = {b['id']:b for b in cursor.get('blocks', [])}
                for b in pending:
                    if b['id'] in old:
                        b.update(at=old[b['id']]['at'], lastTry=old[b['id']].get('lastTry'))
                cursor['blocks'] = pending
                cursor['wakeCounts'] = {b['id']:cursor.get('wakeCounts',{}).get(b['id'],0) for b in pending}
            cursor.pop('autoScope', None)
            self._continuous_update(cid, cursor=cursor)

    def _claim_resume(self, db, cid, auto=None, now=None):
        value = row(db, 'SELECT * FROM continuous_run WHERE id=?', cid)
        legacy = json.loads(value['choice'] or '{}').get('kind') in ('visual','image_failed')
        allowed = value['state'] in ('paused','waiting_quota','blocked') or (auto is None and value['state']=='waiting_choice' and legacy)
        if not allowed or (auto is not None and value['pause_requested']):
            if auto is not None:
                return None
            raise Conflict('当前状态不能继续')
        cursor = json.loads(value['cursor'])
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
            for r in db.execute("SELECT id,cursor FROM continuous_run WHERE state='paused' AND pause_requested=0").fetchall():
                if json.loads(r['cursor']).get('resumeAfterRestart') and self._claim_resume(db,r['id'],auto=[]):
                    ready.append(r['id'])
        return ready

    def _block_ready(self, b, now, probes, counts):
        kind=b['kind']
        if counts.get(b['id'],0)>=LIMIT[kind]:return False
        if b.get('lastTry') is not None and now-b['lastTry']<GAP[kind]:return False
        since=b.get('lastTry') if b.get('lastTry') is not None else b['at']
        if kind=='auth':return probes.get('switched',False) or probes.get('identity',{}).get(b.get('provider')) is True
        if kind=='quota':return now>=b['resetsAt'] if b.get('resetsAt') is not None else now-since>=1800
        if kind=='transient':return now-since>=300
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
        if role in ('image','image_assess') or not role:return False
        c=r['cursor']
        if role=='kickoff':slot=c.get('startArgs',{}).get('executor_plan',{}).get('create',{})
        else:
            plan={**self._kickoff(db,r['episode_id'])['executor_plan'],
                  **self._stage_executors(db,r['episode_id'],b['scope'].split(':')[1] if b['scope'].startswith('run:') else c.get('stage','story'))}
            slot=plan.get(role,{})
        return bool(slot.get('provider') and slot['provider']!=b.get('provider'))

    def changed_continuous_runs(self, now=None):
        now=self.clock() if now is None else now
        with connect(self.db_path) as db:
            snapshots=[dict(r) for r in db.execute("SELECT * FROM continuous_run WHERE state IN ('running','waiting_choice','blocked','waiting_quota')")]
        for r in snapshots:r['cursor']=json.loads(r['cursor'])
        probes=self._recovery_probes(snapshots,now)
        ready=[]
        with transaction(self.db_path) as db:
            for snapshot in snapshots:
                r=dict(row(db,'SELECT * FROM continuous_run WHERE id=?',snapshot['id']))
                cursor=json.loads(r['cursor']);r['cursor']=cursor
                if r['pause_requested']:continue
                if r['state'] in ('blocked','waiting_quota'):
                    if cursor.get('pendingBlocks') is not None or cursor.get('blocks')!=snapshot['cursor'].get('blocks'):continue
                    ids=[b['id'] for b in cursor.get('blocks',[]) if self._block_ready(
                        b,now,dict(probes,switched=self._provider_switched(db,r,b) if b['kind']=='auth' else False),cursor.get('wakeCounts',{}))]
                    if ids and self._claim_resume(db,r['id'],auto=ids,now=now):ready.append(r['id'])
                elif r['state']=='running' and snapshot['state']=='running':
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
                elif r['state']=='waiting_choice' and cursor.get('waitingSettings'):
                    current=self.kickoff_check_view(self._kickoff_source(cursor['startArgs']))
                    if current['basis']!=cursor['settingsBasis']:
                        cursor.update(waitingSettings=False,phase='kickoff')
                        db.execute("UPDATE continuous_run SET state='running',cursor=?,updated_at=? WHERE id=?",(dumps(cursor),self.clock(),r['id']))
                        ready.append(r['id'])
        return ready

    def _continuous_retry_slot(self, cid, run):
        if run.get('reason')!='authentication' or run.get('purpose') in ('image','image_assess'):
            return {}
        value=self._continuous_record(cid)
        with connect(self.db_path) as db:
            stage=row(db,'SELECT stage FROM task WHERE id=?',run['task_id'])['stage']
            plan={**self._kickoff(db,value['episode_id'])['executor_plan'],
                  **self._stage_executors(db,value['episode_id'],stage)}
        role={'revise':'create','parallel':'create','cold_read':'review','comparison':'review','proposal':'expert'}.get(run.get('purpose'),run.get('purpose'))
        slot=plan.get(role,{})
        return {k:slot.get(k) for k in ('provider','model','effort')} if slot.get('provider') and slot['provider']!=run['provider'] else {}

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
