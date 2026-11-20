"""Episode-scoped, persistent orchestration; all writing uses ordinary run inputs."""
import json
import re
import logging
import threading
from contextvars import ContextVar

from .db import connect, transaction, dumps, new_id
from .errors import Conflict, Rejected, ExecutorUnavailable, row
from . import workflow
from .quickstart import QuickStartMixin
from .continuous_supplements import ContinuousSupplements
from .continuous_images import ContinuousImages
from .continuous_finishing import ContinuousFinishing
from .continuous_delivery import ContinuousDelivery
from .continuous_fixes import ContinuousFixes
from .storycheck import check_story
from .continuous_recovery import ContinuousRecovery
from .stressrepairs import adopted, pending_changes, issue_stage, upstream_basis

STAGES = workflow.ORDER[:6]
ACTIVE = ('running','paused','waiting_external')
FORMAT_REASONS = {'schema', '回包两次不符合契约', '已收到回包，未通过入库校验'}


candidate_scope=ContextVar('continuous_candidate',default=None)


class CandidateRejected(Exception):
    """Only the candidate currently being drafted exhausted its format repair."""


class RunWait(Exception):
    """An existing text run is still executing outside this worker."""
    pass


class Halt(Exception):
    pass


class UserChange(Exception):
    """Restart orchestration from reconciled user state, never the old call stack."""
    pass


class ContinuousMixin(ContinuousRecovery, QuickStartMixin, ContinuousSupplements, ContinuousImages, ContinuousFinishing, ContinuousDelivery, ContinuousFixes):
    def standard_goals(self):
        return {stage:workflow.STANDARD_GOALS[stage] for stage in STAGES}

    def _continuous_record(self, cid):
        with connect(self.db_path) as db:
            value = dict(row(db, 'SELECT * FROM continuous_run WHERE id=?', cid))
        for key in ('cursor', 'choice', 'scope'):
            value[key] = json.loads(value[key]) if value[key] else None
        return value

    def _continuous_update(self, cid, **changes):
        with transaction(self.db_path) as db:
            if row(db, 'SELECT state FROM continuous_run WHERE id=?', cid)['state'] in ('cancelled','failed','done'):
                return
            if changes.get("state")=="paused":
                old = row(db, "SELECT state,episode_id,cursor,pause_requested FROM continuous_run WHERE id=?", cid)
                if old["state"] != changes["state"]:
                    cursor = json.loads(old["cursor"])
                    event = dict(state=changes["state"], stage=cursor.get("stage"),
                                 actor="user" if old["pause_requested"] else "system",
                                 reason=changes.get("reason"), at=self.clock())
                    history = cursor.get("pauseHistory", []) + [event]
                    changes["cursor"] = dict(changes.get("cursor", cursor), pauseHistory=history)
            for key, value in changes.items():
                if key not in ('state','cursor','choice','reason','pause_requested'):
                    raise ValueError(key)
                if key in ('cursor','choice') and value is not None:
                    value = dumps(value)
                db.execute(f'UPDATE continuous_run SET {key}=?,updated_at=? WHERE id=?', (value,self.clock(),cid))
            if changes.get('state')=='running':
                # 实测 124：记录本次恢复运行的时刻，状态条显示“本步已用”而不是从创建起的总时长。
                db.execute("UPDATE continuous_run SET cursor=json_set(cursor,'$.resumedAt',?) WHERE id=?",(self.clock(),cid))

    def attention_episodes(self):
        """实测 117：最新一次连续制作停下或等待选择的集，供全局提示使用。"""
        with connect(self.db_path) as db:
            rows=db.execute("""SELECT c.episode_id,c.state,c.reason,c.cursor,c.updated_at,e.display_code,e.title,e.project_id,p.name project_name
                FROM continuous_run c JOIN active_episodes e ON e.id=c.episode_id JOIN project p ON p.id=e.project_id
                WHERE c.rowid IN (SELECT max(rowid) FROM continuous_run GROUP BY episode_id) AND c.state='waiting_external'
                ORDER BY c.updated_at DESC""").fetchall()
        result=[]
        for r in rows:
            cursor=json.loads(r['cursor'] or '{}')
            result.append(dict(episodeId=r['episode_id'],displayCode=r['display_code'],title=r['title'],projectId=r['project_id'],projectName=r['project_name'],
                               state=r['state'],reason=r['reason'] or '',stage=cursor.get('stage'),phase=cursor.get('phase'),updatedAt=r['updated_at']))
        return result

    def continuous_view(self, episode_id):
        from .custommethods import selection
        from .methodpack import method_entries
        with connect(self.db_path) as db:
            ep=self._episode_in(db,episode_id)
            saved=db.execute('SELECT id FROM continuous_run WHERE episode_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(episode_id,)).fetchone()
            if not db.execute('SELECT 1 FROM kickoff WHERE episode_id=?',(episode_id,)).fetchone():
                return dict(run=self._continuous_record(saved[0]) if saved else None,methods={},decisions=[dict(r) for r in db.execute('SELECT * FROM ai_decision WHERE continuous_id=?',(saved[0],))] if saved else [],methodNotice='')
            pack,profile=self._episode_method(db,episode_id)
            methods={}
            missing=set()
            for stage in STAGES:
                disabled=self.get_setting(f'method-options:{episode_id}:{stage}',[])
                entries=[e for e in method_entries(pack,stage,profile) if e['name'] not in disabled]
                entries+=[e for e in self._group_inputs(db,episode_id,stage,pack) if e['role']=='method']
                entries+=self._custom_method_entries(db,dict(episode_id=episode_id,project_id=ep['project_id'],stage=stage))
                methods[stage]=[dict(name=e['name'],role=e['role']) for e in entries]
                missing.update(set(selection(db,ep['project_id'],stage))-set(selection(db,episode_id,stage)))
            decisions=[dict(r) for r in db.execute('SELECT * FROM ai_decision WHERE continuous_id=? ORDER BY created_at,rowid',(saved[0],))] if saved else []
        value=self._continuous_record(saved[0]) if saved else None
        execution=None
        if value and value['cursor'].get('runId'):
            with connect(self.db_path) as db:
                current=db.execute('SELECT id,purpose,status,provider,requested_model,reported_model,base_version_id FROM run WHERE id=?',(value['cursor']['runId'],)).fetchone()
                if current:execution=dict(current)
        image_progress=None
        if value and value['cursor'].get('phase')=='images':
            with connect(self.db_path) as db:
                counts=db.execute("""SELECT count(*) total,count(adopted_version_id) adopted FROM asset_need
                    WHERE episode_id=? AND removed=0 AND decision IS NOT 'text_only'""",(episode_id,)).fetchone()
                requests=db.execute("""SELECT b.provider,b.model,r.state,count(*) n FROM image_batch b
                    JOIN image_request r ON r.batch_id=b.id WHERE b.episode_id=?
                    GROUP BY b.provider,b.model,r.state ORDER BY max(b.created_at) DESC""",(episode_id,))
                failure=db.execute("""SELECT r.ordinal,r.replaces_id,r.error,b.spec FROM image_request r
                    JOIN image_batch b ON b.id=r.batch_id
                    WHERE b.episode_id=? AND r.state IN ('failed','unknown') AND r.error IS NOT NULL
                    ORDER BY coalesce(r.ended_at,r.submitted_at,b.created_at) DESC LIMIT 1""",(episode_id,)).fetchone()
                latest_error=None
                if failure:
                    ordinal=self._original_ordinal(failure)
                    item=json.loads(failure['spec'])['items'][ordinal-1]
                    latest_error=dict(title=(item.get('target') or {}).get('name'),error=failure['error'])
                image_progress=dict(counts,requests=[dict(r) for r in requests],latestError=latest_error)
        return dict(run=value,execution=execution,imageProgress=image_progress,methods=methods,decisions=decisions,expert=self.episode_expert(episode_id),issues=self.material_issues(episode_id),methodNotice=f'本项目有 {len(missing)} 份方法本集尚未采用，将按本集现有方法运行' if missing else '')

    def start_continuous_precheck(self, episode_id, target_stage='stress', command_id=None, image_options=None):
        if target_stage not in ('stress','delivery'): raise Rejected('请选择故事审查或图文交付')
        with connect(self.db_path) as db:
            self._kickoff(db,episode_id)
            if db.execute("SELECT 1 FROM continuous_run WHERE episode_id=? AND state NOT IN ('done','cancelled','failed')",(episode_id,)).fetchone(): raise Conflict('本集已有连续制作，请从当前进度继续')
            if db.execute("SELECT 1 FROM run r JOIN task t ON t.id=r.task_id WHERE t.episode_id=? AND r.status IN ('queued','running')",(episode_id,)).fetchone(): raise Conflict('本集有正在执行的任务，结束后再开始')

    def start_continuous(self, episode_id, target_stage='stress', command_id=None, image_options=None):
        if target_stage not in ('stress','delivery'):
            raise Rejected('请选择故事审查或图文交付')
        with transaction(self.db_path) as db:
            self._kickoff(db,episode_id)
            def create():
                if db.execute("SELECT 1 FROM continuous_run WHERE episode_id=? AND state NOT IN ('done','cancelled','failed')",(episode_id,)).fetchone():
                    raise Conflict('本集已有连续制作，请从当前进度继续')
                if db.execute("SELECT 1 FROM run r JOIN task t ON t.id=r.task_id WHERE t.episode_id=? AND r.status IN ('queued','running')",(episode_id,)).fetchone():
                    raise Conflict('本集有正在执行的任务，结束后再开始')
                previous = db.execute("SELECT id,state,mode,target_stage FROM continuous_run WHERE episode_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",(episode_id,)).fetchone()
                if previous and previous['state']=='failed' and previous['mode']=='full' and previous['target_stage']==target_stage and not image_options:
                    return dict(continuousId=self._claim_resume(db,previous['id']))
                cid=new_id()
                cursor=dict(roleProtocol=1,imageOptions=image_options or {},enableDirections=True,base=adopted(db,episode_id),attempts={},rounds={},stressReports=0,queue=[],ownedRuns=[],userStamp=self._continuous_user_stamp(db,episode_id,[]))
                db.execute('INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,reason,choice,pause_requested,created_at,updated_at) VALUES(?,?,?,?,?,NULL,NULL,0,?,?)',(cid,episode_id,target_stage,'running',dumps(cursor),self.clock(),self.clock()))
                return dict(continuousId=cid)
            result=self._command(db,command_id,'start_continuous',[episode_id,target_stage,image_options],create)
        return self._advance_continuous(result['continuousId'])

    def pause_continuous(self, continuous_id):
        with transaction(self.db_path) as db:
            db.execute("UPDATE continuous_run SET pause_requested=1,state=CASE WHEN state='waiting_external' THEN 'paused' ELSE state END,updated_at=? WHERE id=? AND state IN ('waiting_external','running')",(self.clock(),continuous_id))
        return self._continuous_record(continuous_id)

    def cancel_continuous(self, continuous_id):
        self._continuous_update(continuous_id,state='cancelled',pause_requested=1,reason='已结束连续制作，稿件保留')
        return self._continuous_record(continuous_id)

    def resume_continuous(self, continuous_id, command_id=None):
        with transaction(self.db_path) as db:
            result=self._command(db,command_id,'resume_continuous',continuous_id,
                                 lambda:dict(continuousId=self._claim_resume(db,continuous_id)))
        return self._advance_continuous(result['continuousId'])

    def _continuous_changed(self, db, eid):
        for value in db.execute("SELECT * FROM continuous_run WHERE episode_id=? AND state IN ('running','waiting_external')", (eid,)).fetchall():
            cursor=json.loads(value['cursor'])
            cursor['pendingUserChange']=True
            db.execute("UPDATE continuous_run SET state='running',choice=NULL,reason=NULL,cursor=?,updated_at=? WHERE id=?", (dumps(cursor),self.clock(),value['id']))

    def _continuous_reconcile(self, db, eid, cursor):
        for rid in cursor.get('queue',[]):
            status=db.execute('SELECT status FROM run WHERE id=?',(rid,)).fetchone()
            if status and status[0]=='queued': self._finish(rid,'stopped',reason='用户修改后从实际状态接续',db=db)
        cursor.update(queue=[],planId=None,stressReports=0,rounds={},externalDrafts=True,userDrafts={},promptCandidate=None,finishingReceipt=None,promptRepairHistory={})
        cursor.pop('waitingRun',None)
        cursor['ignoredVersions']=[r['id'] for r in db.execute('SELECT v.* FROM version v JOIN artifact a ON a.id=v.artifact_id WHERE a.episode_id=?',(eid,)) if r['source_run_id'] in cursor['ownedRuns']]
        for r in db.execute('SELECT v.*,a.stage,a.adopted_version_id FROM version v JOIN artifact a ON a.id=v.artifact_id WHERE a.episode_id=? ORDER BY v.display_no',(eid,)):
            adopted_no=db.execute('SELECT display_no FROM version WHERE id=?',(r['adopted_version_id'],)).fetchone()
            if r['source_run_id'] not in cursor['ownedRuns'] and r['id'] not in cursor.get('ownedVersions',[]) and r['display_no']>(adopted_no[0] if adopted_no else 0): cursor['userDrafts'][r['stage']]=r['id']
        cursor.update(pendingUserChange=False,userStamp=self._continuous_user_stamp(db,eid,cursor['ownedRuns']),base=adopted(db,eid),retried={})

    def _expert_repair_step(self,cid):
        """A rejected production decision creates a scoped revision, not a user checkpoint."""
        run=self._continuous_record(cid);c=run['cursor'];r=c.get('nextRepair')
        if not r:return
        stage=r['stage'];eid=run['episode_id']
        art=self._continuous_art(eid,stage)
        mode=r.get('method','patch')
        base=r.get('baseVersionId') or (art['versions'][-1]['id'] if art and art['versions'] else None)
        directions={'patch':'只修复被指出的字段或引用，保留其余原稿。',
                    'simplify':'换用更简单可拍的方案表达同一事件，减少非必要动作及依赖。',
                    'rebuild':'基于当前版本逐项修复仍未解决的问题，保留其他字段和已经通过的内容。'}
        self._continuous_cursor(cid,nextRepair=None)
        if r.get('retryRunId'):
            failed=r['retryRunId']
            retry=self.retry_run(failed,command_id=f'{cid}:segment-repair:{failed}')
            self._continuous_execute(cid,retry['runId'])
            return
        if stage=='prompts':
            plan=self._continuous_art(eid,'plan')
            self._grid_artwork(cid,eid,plan['adopted_version_id'])
        if stage=='prompts' and base:
            scope=set(r.get('segments') or self._prompt_problem_segments(cid,base,r['reason']))
            version=self._continuous_prompt_revision(cid,base,scope,r['reason'],**({'notes_only':True} if r.get('notesOnly') else {}))
            self._continuous_adopt(cid,stage,version)
            self._continuous_cursor(cid,finishingReceipt=None,promptCandidate=version)
            return
        self._continuous_create(cid,stage,
            directions[mode]+'保留已定角色、事件因果和导演要求。具体问题：'+r['reason'],
            base=base,format_repair=True)
        version=self._continuous_art(eid,stage)['versions'][-1]['id']
        self._continuous_adopt(cid,stage,version)
        self._continuous_cursor(cid,finishingReceipt=None,promptCandidate=version if stage=='prompts' else None)

    def _continuous_problem(self,cid,reason,blocks=None,*,repair_base=None,segments=None,retry_run=None,notes_only=False,allow_content_repair=True):
        from .continuous_images import cursor_lock
        with cursor_lock(cid):
            value=self._continuous_record(cid)
            if value['state']!='running':raise Halt()
            cursor=value['cursor']; external=[b for b in blocks or [] if b]
            if external:
                # Persist the wait and its deadline atomically; a restart cannot lose the probe.
                prior={b['id']:b for b in cursor.get('blocks',[])}
                for block in external:
                    old=prior.get(block['id'],{})
                    block.update(at=old.get('at',block['at']),lastTry=old.get('lastTry'))
                cursor.update(blocks=external,autoScope=None,wakeCounts={b['id']:cursor.get('wakeCounts',{}).get(b['id'],0) for b in external})
                self._continuous_update(cid,state='waiting_external',reason=reason,cursor=cursor)
                raise Halt()
            stage=cursor.get('stage')
            stage={'assets':'asset_design','delivery':'prompts','package':'prompts'}.get(stage,stage)
            phase=cursor.get('judgmentKind') if cursor.get('phase')=='judgment' else stage
            key=str(stage)+':'+str(phase)
            if stage=='prompts' and segments:key+=':'+dumps(sorted(set(segments)))
            history=cursor.setdefault('repairHistory',{})
            attempts=history.setdefault(key,[])
            if allow_content_repair and stage in (*workflow.ORDER, 'publish'):
                for method in ('patch','simplify','rebuild'):
                    if any(entry['method']==method for entry in attempts):continue
                    attempts.append(dict(method=method,reason=reason,runId=cursor.get('runId')))
                    cursor.update(nextRepair=dict(stage=stage,reason=reason,method=method),queue=[],formatRound=cursor.get('formatRound',0)+1)
                    if notes_only:cursor['nextRepair']['notesOnly']=True
                    if retry_run:cursor['nextRepair']['retryRunId']=retry_run
                    if repair_base:cursor['nextRepair']['baseVersionId']=repair_base
                    if segments:cursor['nextRepair']['segments']=segments
                    self._continuous_update(cid,cursor=cursor,reason=reason)
                    raise UserChange()
            if not allow_content_repair:
                block=self._recovery_block(f'judgment:{stage}:{phase}','transient',role='expert')
                prior={b['id']:b for b in cursor.get('blocks',[])}
                old=prior.get(block['id'],{})
                block.update(at=old.get('at',block['at']),lastTry=old.get('lastTry'))
                cursor.update(queue=[],formatRound=cursor.get('formatRound',0)+1,blocks=[block],autoScope=None,
                              wakeCounts={block['id']:cursor.get('wakeCounts',{}).get(block['id'],0)})
                cursor.pop('nextRepair',None)
                self._continuous_update(cid,state='waiting_external',reason='核对或判断未拿到有效格式回复，等待自动重试（不派发内容返修）：'+reason,cursor=cursor,choice=None)
            else:
                self._continuous_update(cid,state='failed',reason='无法产出通过校验的阶段稿：'+reason,cursor=cursor,choice=None)
        raise Halt()

    def _continuous_user_stamp(self, db, eid, owned):
        internal={vid for record in db.execute("SELECT cursor FROM continuous_run WHERE episode_id=?",(eid,)) for vid in json.loads(record[0]).get("ownedVersions",[])}
        return [r["id"] for r in db.execute("SELECT v.id,v.source_run_id FROM version v JOIN artifact a ON a.id=v.artifact_id WHERE a.episode_id=? ORDER BY v.created_at,v.rowid",(eid,)) if r["source_run_id"] not in owned and r["id"] not in internal]

    def _retry_origin(self, db, cid, run_id):
        command=db.execute("SELECT command_id FROM command WHERE kind='retry_run' AND json_extract(result,'$.runId')=?",(run_id,)).fetchone()
        run=row(db,'SELECT task_id FROM run WHERE id=?',run_id)
        parent=next((r['id'] for r in db.execute('SELECT id,reason FROM run WHERE task_id=? ORDER BY created_at,rowid',(run['task_id'],))
                     if run_id in (r['reason'] or '').split(' → 已重试为 ')[1:]),None)
        if not parent:return None
        name=command['command_id'] if command else ''
        prefix=f'{cid}:format-switch:'
        if name.startswith(prefix):
            round_text,_,source=name[len(prefix):].partition(':')
            if round_text.isdigit() and source==parent:return ('format-switch',int(round_text),parent)
        if name in (f'{cid}:retry:{parent}',f'{cid}:restart-retry:{parent}',f'{cid}:segment-repair:{parent}'):
            return ('auto',None,parent)
        return ('manual',None,parent)

    def _claim_retry_descendants(self, db, cid, cursor):
        """Claim only linked attempts, before external-version detection; repair lost receipts."""
        before=dumps(cursor)
        seeds=set(cursor.get('ownedRuns',[])+cursor.get('queue',[])+list(cursor.get('directRuns',{}).values()))
        seeds.update(r for r in (cursor.get('runId'),cursor.get('waitingRun')) if r)
        latest={}; tasks={}
        owned=cursor.setdefault('ownedRuns',[])
        episode=row(db,'SELECT episode_id FROM continuous_run WHERE id=?',cid)['episode_id']
        switches={json.loads(r['result'])['runId']:r['command_id'][len(f'{cid}:format-switch:'):].split(':',1)
                  for r in db.execute("SELECT command_id,result FROM command WHERE kind='retry_run' AND command_id GLOB ?",(f'{cid}:format-switch:*',))}
        processed=set()
        for seed in seeds:
            start=db.execute('SELECT task_id FROM run WHERE id=?',(seed,)).fetchone()
            if not start:continue
            task_id=start['task_id']
            if task_id not in tasks:
                tasks[task_id]=[dict(r) for r in db.execute('SELECT * FROM run WHERE task_id=? ORDER BY created_at,rowid',(task_id,))]
            runs=tasks[task_id]; order={r['id']:i for i,r in enumerate(runs)}
            linked={seed}
            for index,run in enumerate(runs):
                if run['id'] not in linked:continue
                linked.update(child for child in (run['reason'] or '').split(' → 已重试为 ')[1:] if order.get(child,-1)>index)
            latest[seed]=max(linked,key=order.__getitem__)
            for run in runs:
                if run['id'] not in linked:continue
                if run['id'] not in owned:owned.append(run['id'])
                if run['id'] in processed:continue
                processed.add(run['id'])
                origin=switches.get(run['id'])
                if not origin or len(origin)!=2 or not origin[0].isdigit():continue
                parent=next((r for r in runs if r['id']==origin[1]),None)
                if not parent:continue
                detail=dict(round=int(origin[0]),**{'from':parent['provider'],'to':run['provider']},
                            reason=(parent['reason'] or '').split(' → 已重试为 ')[0],diagnostic=parent['diagnostic'],parentRun=parent['id'])
                cursor.setdefault('formatSwitches',{})[run['id']]=detail
                if not db.execute("SELECT 1 FROM ai_decision WHERE continuous_id=? AND kind='retry_executor' AND ref=?",(cid,run['id'])).fetchone():
                    stage=row(db,'SELECT stage FROM task WHERE id=?',task_id)['stage']
                    action_text = f"使用当前模型修正回包（{run['provider']}）" if parent['provider'] == run['provider'] else f"回包未通过格式或契约校验，换 {run['provider']} 重试"
                    db.execute('INSERT INTO ai_decision VALUES(?,?,?,?,?,?,?,?,NULL,?)',
                               (new_id(),cid,episode,stage,'retry_executor',run['id'],
                                action_text,dumps(detail),self.clock()))
        cursor['queue']=list(dict.fromkeys(latest.get(r,r) for r in cursor.get('queue',[])))
        for key in ('runId','waitingRun'):
            if cursor.get(key):cursor[key]=latest.get(cursor[key],cursor[key])
        if cursor.get('directRuns'):
            cursor['directRuns']={key:latest.get(r,r) for key,r in cursor['directRuns'].items()}
        return dumps(cursor)!=before

    def _format_retry(self, cid, run, cursor):
        with connect(self.db_path) as db:
            ancestor=run['id']; switched=None; switched_from=None; visited=set()
            while ancestor not in visited:
                visited.add(ancestor)
                origin=self._retry_origin(db,cid,ancestor)
                if not origin or origin[0]=='manual':break
                if origin[0]=='format-switch':
                    if origin[1]==cursor.get('formatRound',0):
                        switched=ancestor
                        switched_from=row(db,'SELECT provider FROM run WHERE id=?',origin[2])['provider']
                    break
                ancestor=origin[2]
            stage=row(db,'SELECT stage FROM task WHERE id=?',run['task_id'])['stage']
        reason=run.get('diagnostic') or (run.get('reason') or '').split(' → 已重试为 ')[0]
        if switched:
            verb='重试过一次' if switched_from==run['provider'] else '换过一次执行器'
            if candidate_scope.get()==cid:raise CandidateRejected(reason)
            readonly_run = cursor.get('phase') in ('judgment', 'readonly')
            if not readonly_run and run.get('manifest_relpath'):
                try:
                    manifest, _ = self.store.load_manifest(run['manifest_relpath'])
                    readonly_run = bool((manifest.get('stageDialogue') or {}).get('readOnly') or manifest.get('contextProfile') == 'bounded_judgment')
                except Exception:
                    readonly_run = False
            self._continuous_problem(cid,f"本轮已{verb}（{run['provider']}），仍未通过：{reason}",
                                     repair_base=run.get('base_version_id'),allow_content_repair=not readonly_run)
        return self.retry_run(run['id'], command_id=f"{cid}:format-switch:{cursor.get('formatRound',0)}:{run['id']}")['runId']

    def _continuous_checkpoint(self, cid):
        from .continuous_images import cursor_lock
        changed=False
        with cursor_lock(cid), transaction(self.db_path) as db:
            value=dict(row(db,'SELECT * FROM continuous_run WHERE id=?',cid))
            cursor=json.loads(value['cursor']); value['cursor']=cursor
            for key in ('choice','scope'):
                value[key]=json.loads(value[key]) if value[key] else None
            if value['state']!='running':raise Halt()
            if not value['pause_requested']:
                claimed=self._claim_retry_descendants(db,cid,cursor)
                current=adopted(db,value['episode_id'])
                stamp=self._continuous_user_stamp(db,value['episode_id'],cursor['ownedRuns'])
                changed=bool(cursor.get('pendingUserChange') or stamp!=cursor.get('userStamp',stamp) or current!=cursor.get('base',current))
                if changed:
                    self._continuous_reconcile(db,value['episode_id'],cursor)
                    db.execute('UPDATE continuous_run SET cursor=?,choice=NULL WHERE id=?',(dumps(cursor),cid))
                elif claimed:
                    db.execute('UPDATE continuous_run SET cursor=? WHERE id=?',(dumps(cursor),cid))
        if value['pause_requested']:
            self._continuous_update(cid,state='paused',reason='已按你的要求暂停')
            raise Halt()
        if changed:raise UserChange()
        return value

    def _continuous_log(self, cid, stage, kind, ref, reason, detail):
        value=self._continuous_record(cid)
        with transaction(self.db_path) as db:
            ident=new_id()
            db.execute('INSERT INTO ai_decision VALUES(?,?,?,?,?,?,?,?,NULL,?)',(ident,cid,value['episode_id'],stage,kind,ref,reason,dumps(detail),self.clock()))
            return ident

    def _continuous_cursor(self,cid,**values):
        from .continuous_images import cursor_lock
        with cursor_lock(cid):
            cursor=self._continuous_record(cid)['cursor']
            if 'stage' in values and values['stage']!=cursor.get('stage'):
                values={**values,'stageStartedAt':self.clock()}   # 实测 124：阶段开始时刻
            cursor.update(values)
            self._continuous_update(cid,cursor=cursor)
            return cursor

    def _continuous_execute(self,cid,rid=None):
        cursor=self._continuous_record(cid)['cursor']
        if rid:
            cursor['queue']=list(dict.fromkeys([*cursor.get('queue',[]),rid]))
            self._continuous_update(cid,cursor=cursor)
        while self._continuous_record(cid)['cursor'].get('queue'):
            value=self._continuous_checkpoint(cid)
            cursor=value['cursor']; rid=cursor['queue'][0]
            cursor['runId']=rid
            if rid not in cursor['ownedRuns']: cursor['ownedRuns'].append(rid)
            self._continuous_update(cid,cursor=cursor)
            with connect(self.db_path) as db:
                run=dict(row(db,'SELECT r.*,t.stage AS task_stage FROM run r JOIN task t ON t.id=r.task_id WHERE r.id=?',rid))
            if run['purpose'] in ('cold_read','comparison') or (run['purpose']=='review' and run['task_stage']=='prompts'):
                if run['status'] in ('running','preparing'):
                    self._continuous_cursor(cid,waitingRun=rid)
                    raise RunWait()
                if run['status']=='queued':
                    with transaction(self.db_path) as db:
                        self._finish(rid,'stopped',reason='旧09总体独审已退出流程，由提示词本步检查接续',db=db)
                self._continuous_cursor(cid,queue=[item for item in cursor['queue'] if item!=rid],runId=None)
                continue
            restart=self._restart_retry(cid,run)
            if restart:
                cursor=self._continuous_record(cid)['cursor']
                cursor['queue'][0]=restart
                self._continuous_update(cid,cursor=cursor)
                continue
            if run['status'] in ('waiting_quota','failed','stopped'):
                if (run.get('reason') or '').split(' → 已重试为 ')[0] == '分段提示词校验失败':
                    segment = (run.get('diagnostic') or '').partition('：')[0]
                    previous = cursor.get('retried',{}).get(rid)
                    diagnostics = previous.get('errors',[]) if isinstance(previous,dict) and previous.get('segment')==segment else []
                    # Only count replies to expert repairs, scoped to the same segment.
                    if isinstance(previous,dict) and previous.get('segment')==segment:
                        if run['diagnostic'] in diagnostics:
                            self._continuous_problem(cid,'写稿 AI 再次收到同一段校验问题：'+run['diagnostic'],repair_base=run.get('base_version_id'),segments=[segment],retry_run=rid)
                        diagnostics = [*diagnostics,run['diagnostic']]
                    retry = self.retry_run(rid, **self._continuous_retry_slot(cid,run), command_id=f'{cid}:segment-repair:{rid}')
                    cursor['queue'][0] = retry['runId']
                    cursor.setdefault('retried',{})[retry['runId']] = dict(segment=segment,errors=diagnostics)
                    self._continuous_update(cid, cursor=cursor)
                    continue
                if run['purpose'] in ('create','revise','parallel') and (run.get('reason') or '').split(' → 已重试为 ')[0] in FORMAT_REASONS:
                    retry=self._format_retry(cid,run,cursor)
                    cursor['queue'][0]=retry
                    self._continuous_update(cid,cursor=cursor)
                    continue
                if run['status']!='waiting_quota' and cursor.get('retried',{}).get(rid):
                    blocks = self._run_blocks(run)
                    if (run['purpose']=='review' or run['reason'] not in FORMAT_REASONS) and not blocks:
                        stage, role = self._continuous_run_role(run)
                        blocks = [self._recovery_block(f'run:{stage}:{role}', 'transient', provider=run['provider'],
                                                       role=role, model=run['requested_model'], effort=run['requested_effort'])]
                    self._continuous_problem(cid,run.get('diagnostic') or run.get('reason') or '运行失败',blocks=blocks)
                retry=self.retry_run(rid,command_id=f'{cid}:retry:{rid}',**self._continuous_retry_slot(cid,run))['runId']
                cursor['queue'][0]=retry
                cursor.setdefault('retried',{})[retry]=True
                self._continuous_update(cid,cursor=cursor)
                continue
            follow=self.execute_run(rid) if run['status']=='queued' else []
            with connect(self.db_path) as db:
                run=dict(row(db,'SELECT * FROM run WHERE id=?',rid))
            if run['status']=='waiting_quota':
                self._continuous_problem(cid,'执行器额度用完，等待自动恢复',blocks=self._run_blocks(run))
            if run['status']=='failed':
                continue
            if run['status']!='succeeded':
                if run['status'] in ('running','preparing'):
                    self._continuous_cursor(cid,waitingRun=rid)
                    self._continuous_update(cid,reason=None)
                    raise RunWait()
                self._continuous_problem(cid,run.get('reason') or '运行尚未完成')
            cursor=self._continuous_record(cid)['cursor']
            cursor.pop('waitingRun',None)
            cursor['queue']=[*cursor['queue'][1:],*follow]
            self._continuous_update(cid,cursor=cursor)
            self._continuous_checkpoint(cid)

    def _continuous_art(self,eid,stage):
        art=next((a for a in self.episode_view(eid)['artifacts'] if a['stage']==stage),None)
        working=workflow.binding_candidate.get()
        if stage=='plan' and working and working['episode']==eid and art:
            return dict(art,adopted_version_id=working['version'])
        return art

    def _continuous_note_errors(self,eid,stage,content):
        from . import productionnotes
        if stage not in productionnotes.STAGES:return []
        with connect(self.db_path) as db:basis=productionnotes.context(db,eid)
        errors=productionnotes.check(stage,content,basis,coverage=False)
        if stage=='plan':
            errors.extend(str(shot.get('id','镜头'))+' 缺少 visibleRoles 或 propStates' for shot in content.get('shots',[]) if not isinstance(shot.get('visibleRoles'),list) or not isinstance(shot.get('propStates'),dict))
        return errors

    def _continuous_submit_review(self, cid, version_id, command_id):
        try:
            job = self.submit_review(version_id, command_id)
        except Rejected as exc:
            self._continuous_problem(cid, '原稿已保留，独审尚未派发：'+str(exc),
                                     blocks=[self._recovery_block('review:'+version_id, 'transient', role='review')])
        self._continuous_execute(cid, job['runId'])

    def _stage_ruling(self,cid,stage,version,gate):
        vid=version['id']
        review=gate.get('review')
        with connect(self.db_path) as db:
            eid=self._continuous_record(cid)['episode_id']
            upstream={'characters':'story','world':'story','treatment':'story','script':'treatment','stress':'script','asset_design':'script','plan':'script','prompts':'plan','publish':'script'}.get(stage)
            source=self._adopted_content(db,eid,upstream) if upstream else None
            settings=self._kickoff(db,eid).get('settings_snapshot')
            if review:
                opinion=db.execute('SELECT observations,reviewer FROM review WHERE id=?',(review['id'],)).fetchone()
                review=dict(review,observations=json.loads(opinion['observations']),reviewer=opinion['reviewer'])
            issues=[dict(r) for r in db.execute('SELECT loc,reason,blocking FROM issue WHERE review_id=?', (review['id'],))] if review else []
            material=dict(version=vid,stage=stage,content=version['content'],upstream=source,settings=settings,review=review,issues=issues)
            if stage=='publish':
                try:material['publishBasis']=self._publish_basis(db,eid)
                except Exception:pass
        answer,_=self._continuous_bounded_json(cid,'采用决定',vid,
            '阅读本步候选、独审与问题，决定采用或退回写稿人。结构和已采用设定必须满足。'
            '有普通意见时解释取舍。reply 内提交 {"action":"adopt 或 revise","reason":"针对本版的一句话或具体改稿要求"}。',
            material,lambda a: [] if isinstance(a,dict) and a.get('action') in ('adopt','revise') and isinstance(a.get('reason'),str) and a['reason'].strip() else ['缺少有效采用决定或理由'],stage=stage)
        self._continuous_log(cid,stage,'expert_judgment',vid,answer['reason'],dict(versionId=vid,review=review,decision='revise' if gate['blocking'] else answer['action']))
        if answer['action']=='revise' or gate['blocking']:
            reason='；'.join([answer['reason'],*(item['reason'] for item in issues if item['blocking'])])
            self._continuous_problem(cid,reason,repair_base=vid)
        return answer['reason']

    def _check_delivery_images(self,cid,stage,segments,version):
        from .images import image_size
        checked=set()
        for segment,entry in segments.items():
            for ref in entry.get('refs',[]):
                vid=ref.get('assetVersionId')
                if not vid or vid in checked or ref.get('kind')!='image':continue
                try:
                    if image_size(self.asset_bytes(vid)) is None:
                        raise ValueError('图片无法解码')
                except (ValueError,OSError,Rejected) as exc:
                    self._continuous_cursor(cid,stage=stage,phase="repair")
                    self._continuous_problem(cid,f"{segment} · {ref.get('label',vid)} · {exc}",repair_base=version,segments=[segment])
                checked.add(vid)

    def _continuous_adopt(self,cid,stage,vid):
        original=vid
        vid=self._continuous_prepare_supplements(cid,stage,vid)
        if stage=='prompts' and vid!=original:
            vid=self._continuous_shot_observations(cid,vid)
            vid=self._continuous_prompt_checks(cid,vid)
        value=self._continuous_checkpoint(cid); eid=value['episode_id']; cursor=value['cursor']
        art=self._continuous_art(eid,stage)
        version=next(v for v in art['versions'] if v['id']==vid)
        if stage=='story':
            errors=check_story(version['content'])
            if value['target_stage']=='delivery' and not str(version['content'].get('title','')).strip():
                errors.append('故事稿须提供 title 标题')
            if errors: self._continuous_problem(cid,'；'.join(errors),repair_base=vid)
        if stage=='plan' and not self.adoption_gate(vid)['formatErrors']:
            working=dict(episode=eid,version=vid,base=art['adopted_version_id'])
            token=workflow.binding_candidate.set(working)
            try:
                self._continuous_cast(cid)
                self._continuous_required_bindings(cid)
                self._continuous_empty_scene_bindings(cid)
                self._continuous_products(cid)
                grids=self._grid_artwork(cid,eid,working['version'])
                self._episode_image_review(cid,eid,self.asset_worksheet(eid)['rows'],grids)
                vid=working['version']
                from .promptdraft import draft
                bound=draft(self,eid,bindings_only=True)['segments']
                self._check_delivery_images(cid,stage,bound,vid)
            finally:
                workflow.binding_candidate.reset(token)
            version=next(v for v in self._continuous_art(eid,stage)['versions'] if v['id']==vid)
        gate=self.adoption_gate(vid)
        gate['formatErrors'].extend(self._continuous_note_errors(eid,stage,version['content']))
        if stage=='prompts':
            self._check_delivery_images(cid,stage,version['content']['segments'],vid)
            placements=self.product_placements(eid,candidate=version['content'])
            if placements['verified']<placements['minimum']:gate['formatErrors'].append('商品入景覆盖不足：须同时有当前镜头记录、必用标记和候选提示词引用')
        skips_diagnosis=False
        if stage=='script':
            with connect(self.db_path) as db:
                skips_diagnosis=bool(db.execute("SELECT 1 FROM optional_stage_choice WHERE episode_id=? AND stage='stress' AND reason<>''",(eid,)).fetchone())
        needs_review=workflow.needs_manuscript_review(stage,skips_diagnosis=skips_diagnosis)
        if gate['formatErrors']:
            self._continuous_cursor(cid,stage=stage,phase='repair')
            self._continuous_problem(cid,'；'.join(gate['formatErrors']),repair_base=vid)
        if needs_review and gate['review'] is None:
            self._continuous_submit_review(cid, vid, f'{cid}:adoption-review:{vid}')
            cursor=self._continuous_checkpoint(cid)['cursor']
            gate.update({key:val for key,val in self.adoption_gate(vid).items() if key!='formatErrors'})
        if stage=='prompts' and (gate['blocking'] or gate['unresolved']):
            # Older builds may already have recorded real findings on this candidate.
            with connect(self.db_path) as db:
                issues=db.execute('SELECT id,loc,reason FROM issue WHERE review_id=? ORDER BY ordinal',(gate['review']['id'],)).fetchall()
            open_ids=set(gate['blocking']+gate['unresolved'])
            pending=[(json.loads(i['loc']),i['reason']) for i in issues if i['id'] in open_ids]
            reason='；'.join(note for _,note in pending)
            affected={loc['objectId'] for loc,_ in pending if loc.get('objectId') in version['content']['segments']}
            self._continuous_cursor(cid,stage=stage,phase='repair')
            self._continuous_problem(cid,reason,repair_base=vid,segments=sorted(affected),notes_only=all(
                re.search(r'(?<![A-Za-z_])notes(?![A-Za-z_])',loc.get('anchor',''),re.I) for loc,_ in pending))
        ruling=None if stage=='prompts' else self._stage_ruling(cid,stage,version,gate)
        cursor=self._continuous_checkpoint(cid)['cursor']
        for issue in gate['unresolved']:
            self.waive(issue,vid,ruling,f'{cid}:waive:{issue}')
            self._continuous_log(cid,stage,'waive',issue,'普通意见按本版放行',dict(versionId=vid))
        user_change=stage in cursor.get('userDrafts',{})
        previous=cursor['base'].get(stage)
        result=self._adopt(art['id'],vid,previous,f'{cid}:adopt:{vid}',False,actor='ai',continuous_id=cid)
        if user_change:
            self._continuous_log(cid,stage,'apply_user_change',vid,'按你的修改要求采用新稿',dict(previousVersionId=previous,versionId=vid))
        if stage=='story' and value['target_stage']=='delivery':
            title=version['content'].get('title')
            if title:
                with transaction(self.db_path) as db:db.execute("UPDATE episode SET title=? WHERE id=? AND title=''",(title,eid))
        cursor['base'][stage]=vid
        cursor.get('userDrafts',{}).pop(stage,None)
        if result.get('runId'): cursor['queue'].append(result['runId'])
        self._continuous_update(cid,cursor=cursor)

        self._continuous_confirm_supplements(cid,stage)

    def _continuous_create(self,cid,stage,goal=None,base=None,auto=False,readonly=False,prompt_images=None,format_repair=False,rebuild=False,prompt_segments=None,prompt_notes_only=False):
        value=self._continuous_checkpoint(cid); eid=value['episode_id']
        art=self._continuous_art(eid,stage)
        with connect(self.db_path) as db:
            baseline=adopted(db,eid)
            plan={**self._kickoff(db,eid)['executor_plan'],**self._stage_executors(db,eid,stage)}
        self._continuous_cursor(cid,stage=stage,phase='readonly' if readonly else 'draft',base=baseline)
        executor={role:plan[role] for role in ('create','review')}
        if stage=='stress': executor['create']=plan['review']
        if readonly and stage=='stress': executor['create']=self.episode_expert(eid)
        instruction=goal or (value['cursor'].get('storyGoal') if stage=='story' else None) or workflow.CONTINUOUS_GOALS[stage]
        if stage=='plan' and not readonly:instruction+='\n每个镜头给出 visibleRoles（实际可见角色编号数组）和 propStates（道具状态对象）；空景用空数组，没有道具状态用空对象。按段选择 flex 或 grid；grid 段在 segments 中写 gridPrompt 和九项 cells，每项含本段 shotId 与 visual，不写素材版本编号。'
        if stage=='story' and value['target_stage']=='delivery':instruction+='\n故事稿须同时填写 title 标题。'
        job=self.create_task(eid,stage,instruction,auto_revise=auto,executor=executor,
                             creation_role='expert' if readonly and stage=='stress' else 'review' if stage=='stress' else None,
                             base_version_id=base,**({"prompt_segments":prompt_segments,"prompt_notes_only":prompt_notes_only} if prompt_segments else {}),prompt_images=((not readonly) if prompt_images is None else prompt_images) if stage=='prompts' else None,discussion_only=readonly,workspace_revision=art['revision'] if art and readonly else None,
                             command_id=f'{cid}:create:{new_id()}',**({'rebuild':True} if rebuild else {}))
        self._continuous_cursor(cid,taskId=job['taskId'])
        self._continuous_execute(cid,job['runId'])
        return job

    def _continuous_stage(self,cid,stage):
        value=self._continuous_record(cid); eid=value['episode_id']; cursor=value['cursor']
        if stage=='stress' and cursor['stressReports']>=3:
            return self._continuous_choices(cid,'repair','script',None)
        art=self._continuous_art(eid,stage)
        pending=next((v for v in reversed(art['versions']) if v.get('source_run_id') in cursor['ownedRuns'] and v['id']!=art['adopted_version_id'] and v['id'] not in cursor.get('ignoredVersions',[])),None) if art else None
        if cursor.get('userDrafts',{}).get(stage) and art:
            pending=next(v for v in art['versions'] if v['id']==cursor['userDrafts'][stage])
        if not pending:
            self._continuous_create(cid,stage,auto=stage=='story')
        art=self._continuous_art(eid,stage); version=pending or art['versions'][-1]
        prepared=self._continuous_prepare_supplements(cid,stage,version['id'])
        if prepared!=version['id']:
            art=self._continuous_art(eid,stage)
            version=next(v for v in art['versions'] if v['id']==prepared)
        errors=list(version['gate']['formatErrors'])+self._continuous_note_errors(eid,stage,version['content'])+(check_story(version['content']) if stage=='story' else [])
        if stage=='story' and value['target_stage']=='delivery' and not str(version['content'].get('title','')).strip():errors.append('故事稿须提供 title 标题')
        if stage in ('script','plan'):
            from .shooting import writing_problems
            script_art=self._continuous_art(eid,'script')
            script=self._continuous_version(script_art['adopted_version_id']) if script_art and script_art['adopted_version_id'] else None
            errors.extend(writing_problems(stage,version['content'],script))
        if errors:
            self._continuous_create(cid,stage,('按结构错误修正：'+'；'.join(errors)+'。直接提交修正字段。先从当前稿提取已有内容，'
                '如正文已有标题则提取标题，角色资料已有则整理成表并保留额外列。沿用原文事实、情节与其他条目，'
                '只补确实缺失的信息；未涉及的字段从基版继承。'),version['id'],auto=stage=='story',format_repair=True)
            version=self._continuous_art(eid,stage)['versions'][-1]
        if stage=='story' and version['gate']['blocking']:
            history=[dict(goal=t['goal'],messages=t['messages'],reviews=[v['reviews'] for v in art['versions']]) for t in art['tasks']]
            self._continuous_create(cid,stage,'前两轮已用的修法与独审意见：'+dumps(history)+'。换一种表现方式解决同一问题，不改主题和主要角色。',version['id'],auto=False,format_repair=True)
            current=self._continuous_art(eid,stage)['versions'][-1]
            self._continuous_submit_review(cid,current['id'],f'{cid}:changed-method-review:{current["id"]}')
            version=self._continuous_art(eid,stage)['versions'][-1]
            if version['gate']['blocking']:
                return self._continuous_choices(cid,'story',stage,version['id'])
        self._continuous_adopt(cid,stage,version['id'])
        if stage=='stress':
            self._continuous_cursor(cid,stressReports=self._continuous_record(cid)['cursor']['stressReports']+1)

    def _continuous_repair(self,cid,pid):
        self._continuous_cursor(cid,planId=pid,phase='repair')
        previous=None
        while True:
            self._continuous_execute(cid)
            with connect(self.db_path) as db:
                plan=dict(row(db,'SELECT * FROM repair_plan WHERE id=?',pid))
            if plan['state']!='open': return plan['state']
            if plan['steps']==previous:
                # An unchanged completed child cannot advance by being executed again.
                self._continuous_cursor(cid,planId=None)
                self._continuous_problem(cid,'返修计划没有登记子步骤结果：'+pid)
            previous=plan['steps']
            step=next(s for s in json.loads(plan['steps']) if s['state']!='done')
            if step['state']=='adopt':
                self._continuous_adopt(cid,step['stage'],step['versionId'])
            elif step['state']=='running':
                self._continuous_execute(cid,step['runId'])
            else:
                job=self.advance_repair_plan(pid,f'{cid}:repair:{new_id()}')
                self._continuous_execute(cid,job.get('runId'))

    def _advance_continuous(self,cid):
        lock=self._continuous_locks.setdefault(cid,threading.Lock())
        if not lock.acquire(blocking=False): return self._continuous_record(cid)
        try:
            while True:
                try:
                    return self._run_continuous(cid)
                except UserChange:
                    continue
        finally:
            try:
                if self._continuous_record(cid)['state'] in ('done','cancelled'):
                    self._continuous_cursor(cid,blocks=[],wakeCounts={},autoScope=None)
            finally:
                lock.release()

    def _run_continuous(self,cid):
        try:
            value=self._continuous_checkpoint(cid); eid=value['episode_id']
            if value['cursor'].get('nextRepair'):self._expert_repair_step(cid)
            if value.get('mode')=='fix':return self._run_continuous_fix(cid)
            if value['cursor'].get('phase')=='kickoff' and not self._continuous_kickoff(cid): return self._continuous_record(cid)
            value=self._continuous_checkpoint(cid)
            if value['cursor'].get('enableDirections') and not value['cursor'].get('directionDone'):
                if not self._continuous_direction(cid): return self._continuous_record(cid)
            self._continuous_settle_report_impacts(cid)
            self._continuous_execute(cid)
            if not self._image_feedback(cid):return self._continuous_record(cid)
            if value['cursor'].get('planId'):
                self._continuous_repair(cid,value['cursor']['planId'])
            for stage in STAGES:
                self._continuous_checkpoint(cid)
                art=self._continuous_art(eid,stage)
                with connect(self.db_path) as db:
                    impacted=any(i['stage']==stage for i in pending_changes(db,eid))
                    skipped=db.execute('SELECT reason FROM optional_stage_choice WHERE episode_id=? AND stage=?',(eid,stage)).fetchone()
                if skipped and skipped[0]: continue
                cursor=self._continuous_record(cid)['cursor']
                unadopted=bool(art and art['versions'] and art['versions'][-1]['id']!=art['adopted_version_id'] and art['versions'][-1]['id'] not in cursor.get('ignoredVersions',[]) and (art['versions'][-1].get('source_run_id') in cursor['ownedRuns'] or cursor.get('externalDrafts')))
                if art and art['adopted_version_id'] and not impacted and not unadopted and not cursor.get('userDrafts',{}).get(stage):
                    if cursor.get('roleProtocol') and stage=='script':
                        with connect(self.db_path) as db:
                            skip=db.execute("SELECT reason FROM optional_stage_choice WHERE episode_id=? AND stage='stress'",(eid,)).fetchone()
                        vid=art['adopted_version_id']
                        if skip and skip[0] and not self.adoption_gate(vid)['review']:
                            self._continuous_cursor(cid,stage=stage,phase='review')
                            self._continuous_submit_review(cid,vid,f'{cid}:adoption-review:{vid}')
                            self._stage_ruling(cid,stage,next(v for v in art['versions'] if v['id']==vid),self.adoption_gate(vid))
                    continue
                self._continuous_stage(cid,stage)
            self._continuous_stress(cid)
            if value['target_stage']=='delivery' and not self._continuous_images(cid):return self._continuous_record(cid)
            if value['target_stage']=='delivery' and not self._continuous_finish_stages(cid):return self._continuous_record(cid)
            if value['target_stage']=='delivery':
                publication=self._continuous_art(eid,'publish')
                if not publication or not publication['adopted_version_id']:
                    self._continuous_stage(cid,'publish')
                if self._continuous_prepare_handoff(cid):
                    self._continuous_delivery(cid)
            else:self._continuous_update(cid,state='done',reason='01–06 已完成')
        except UserChange:
            raise
        except RunWait:
            pass
        except Halt:
            pass
        except Conflict as exc:
            self._continuous_checkpoint(cid)
            cursor=self._continuous_record(cid)['cursor']
            stamp=cursor.get('userStamp',[])
            prior=cursor.get('conflictRecovery',{})
            count=prior.get('count',0)+1 if prior.get('stamp')==stamp and prior.get('base')==cursor.get('base') else 1
            if count>=3:
                try:self._continuous_problem(cid,'同一基版多次发生写入冲突：'+str(exc))
                except Halt:pass
            else:
                self._continuous_cursor(cid,pendingUserChange=True,conflictRecovery=dict(stamp=stamp,base=cursor.get('base'),count=count))
                raise UserChange()
        except ExecutorUnavailable as exc:
            try:self._continuous_problem(cid,str(exc),blocks=[self._recovery_block('dispatch','transient')])
            except Halt:pass
        except Rejected as exc:
            try:self._continuous_problem(cid,str(exc))
            except Halt:pass
        except Exception as exc:
            logging.exception('Continuous production %s failed',cid)
            self._continuous_cursor(cid,blocks=[],wakeCounts={},autoScope=None,pendingBlocks=[])
            self._continuous_update(cid,state='failed',reason=f'执行中断：{type(exc).__name__}：{exc}')
        return self._continuous_record(cid)

    def _continuous_stress(self,cid):
        while True:
            value=self._continuous_checkpoint(cid); eid=value['episode_id']; cursor=value['cursor']
            work=self.stress_worklist(eid)
            issues=[b for b in work['blockers'] if b['type']=='issue']
            if not issues:
                if any(b['type']=='impact' for b in work['blockers']):
                    job=self.start_repair_plan(eid,'upstream',f'{cid}:upstream:{new_id()}')
                    self._continuous_repair(cid,job['planId'])
                    continue
                art=self._continuous_art(eid,'stress')
                if art and art['adopted_version_id']:
                    report=next(v for v in art['versions'] if v['id']==art['adopted_version_id'])
                    script=self._continuous_art(eid,'script')
                    manifest=self.run_manifest(report['source_run_id'])['manifest'] if report.get('source_run_id') else {}
                    current_script=script['adopted_version_id'] if script else None
                    matches=any(e['role']=='adopted_script' and e['name']=='script:'+str(current_script) for e in manifest.get('inputs',[]))
                    if not matches:
                        with connect(self.db_path) as db:
                            basis = upstream_basis(db, eid)
                            plans = db.execute("SELECT steps FROM repair_plan WHERE episode_id=? AND report_version_id=? AND state='done'", (eid, report['id']))
                            matches = any(step.get('action') == 'verify' and step.get('state') == 'done' and step.get('basis') == basis
                                          for plan in plans for step in json.loads(plan['steps']))
                    if not matches:
                        if cursor['stressReports']>=3:
                            return self._continuous_choices(cid,'repair','script',None)
                        self._continuous_create(cid,'stress')
                        fresh=self._continuous_art(eid,'stress')['versions'][-1]
                        self._continuous_adopt(cid,'stress',fresh['id'])
                        self._continuous_cursor(cid,stressReports=cursor['stressReports']+1)
                        continue
                return
            art=self._continuous_art(eid,'stress')
            report=next(v for v in art['versions'] if v['id']==art['adopted_version_id'])
            content=report['content']
            keys={i['id'] for i in issues}
            options={key:[o for o in content['options'] if o['issueId']==key] for key in keys}
            if any(cursor['rounds'].get(key,0)>=2 for key in keys) or any(not any(o.get('changesCore',True) is False for o in opts) for opts in options.values()):
                targets=[issue_stage(i['stage']) for i in content['issues'] if i['id'] in keys]
                if None in targets:
                    self._continuous_problem(cid,'审查报告含有无法识别的返修阶段')
                stage=min(targets,key=STAGES.index)
                return self._continuous_choices(cid,'repair',stage,None)
            goal=('为每条必须修正的问题选择本轮返修方案。只能从给出的 options 中选择 changesCore 为 false 的方案；如果已用方案都失败，可写 newProposal（不改变目标、结局、核心看点）。reply 为 JSON 数组，每项 issueId、optionIndex（整数或 null）、newProposal（字符串或 null）、reason（一句话）。第二轮必须选择与第一轮不同的方案，或写出新的方案。\n'+dumps(dict(options=options,attempts=cursor['attempts'])))
            selected=None
            with connect(self.db_path) as db:
                saved=db.execute("SELECT detail FROM ai_decision WHERE continuous_id=? AND ref=? AND kind='choose_option' AND reverted_at IS NULL ORDER BY created_at DESC LIMIT 1",(cid,report['id'])).fetchone()
            pending=all(cursor['attempts'].get(k) and cursor['attempts'][k][-1]['result']=='待复核' for k in keys)
            cached=json.loads(saved['detail']).get('choices') if saved and pending else None
            for attempt in range(2):
                if cached and attempt==0:
                    result={'reply':dumps(cached)}
                else:
                    self._continuous_create(cid,'stress',goal,readonly=True)
                    result=json.loads((self.store.run_dir(self._continuous_record(cid)['cursor']['runId'])/'result.json').read_text())
                try:
                    answer=json.loads(result['reply'])
                    if not isinstance(answer,list) or sorted(x['issueId'] for x in answer)!=sorted(keys):
                        raise ValueError('方案须覆盖全部必须修正的问题')
                    selected={}
                    for x in answer:
                        key=x['issueId']; index=x.get('optionIndex'); proposal=x.get('newProposal')
                        if not isinstance(x.get('reason'),str) or not x['reason'].strip(): raise ValueError('缺少推荐理由')
                        if index is None and isinstance(proposal,str) and proposal.strip():
                            choice=dict(proposal=proposal.strip()); method=proposal.strip()
                        elif type(index) is int and 0<=index<len(options[key]) and options[key][index].get('changesCore',True) is False:
                            choice=index; method=options[key][index]['proposal']
                        else: raise ValueError('方案编号不存在或改变核心')
                        history=cursor['attempts'].get(key,[])
                        if cursor['rounds'].get(key,0) and history and history[-1]['result']!='待复核' and history[-1]['method']==method:
                            raise ValueError('第二轮须换一种修法')
                        selected[key]=choice
                        x['method']=method
                    break
                except (ValueError,KeyError,TypeError) as exc:
                    selected=None
                    goal+='\n上次回包错误：'+str(exc)+'；请只修正格式和选择。'
            if selected is None:
                selected={};answer=[]
                for key in sorted(keys):
                    previous={v['method'] for v in cursor['attempts'].get(key,[])}
                    pick=next(((i,o) for i,o in enumerate(options[key]) if o.get('changesCore',True) is False and o['proposal'] not in previous),None)
                    if pick is None:return self._continuous_choices(cid,'repair',issue_stage(next(v['stage'] for v in content['issues'] if v['id']==key)) or 'script',None)
                    i,o=pick;selected[key]=i
                    answer.append(dict(issueId=key,optionIndex=i,method=o['proposal'],reason='采用未试过且保留核心的现有修法'))
            for x in answer:
                key=x['issueId']
                if cached and attempt==0: continue
                cursor['attempts'].setdefault(key,[]).append(dict(round=cursor['rounds'].get(key,0)+1,method=x['method'],result='待复核'))
            self._continuous_update(cid,cursor={**self._continuous_record(cid)['cursor'], 'attempts':cursor['attempts']})
            if not cached or attempt!=0:
                self._continuous_log(cid,'stress','choose_option',report['id'],'按不改变核心的方案返修',dict(choices=answer))
            job=self.start_repair_plan(eid,'report',f'{cid}:repair:{new_id()}',report['id'],selected,max_rounds=2)
            state=self._continuous_repair(cid,job['planId'])
            cursor=self._continuous_record(cid)['cursor']
            for key in keys:
                cursor['rounds'][key]=cursor['rounds'].get(key,0)+1
                cursor['attempts'][key][-1]['result']=state
            cursor['planId']=None
            self._continuous_update(cid,cursor=cursor)


    def _continuous_discuss(self,cid,stage,goal,base=None,prompt_images=None):
        self._continuous_create(cid,stage,goal,base=base,readonly=True,prompt_images=prompt_images)
        rid=self._continuous_record(cid)['cursor']['runId']
        return json.loads((self.store.run_dir(rid)/'result.json').read_text())['reply']

    def _continuous_choices(self,cid,kind,stage,version_id):
        value=self._continuous_checkpoint(cid); eid=value['episode_id']
        art=self._continuous_art(eid,stage)
        base=version_id if kind=='story' else art['adopted_version_id']
        report_art=self._continuous_art(eid,'stress')
        report=next((v for v in report_art['versions'] if v['id']==report_art['adopted_version_id']),None) if report_art else None
        blockers=self.stress_worklist(eid)['blockers'] if report else []
        keys={b['id'] for b in blockers if b['type']=='issue'}
        issues=[i for i in report['content']['issues'] if i['id'] in keys] if report else []
        candidates=[]
        rejected=[]
        for key in ('A','B'):
            if kind=='story':
                instruction='保留原故事核心，按第三种表现方式修订。' if key=='A' else '允许调整引发问题的情节元素，保留主题和主要角色。'
            else:
                options=[o for o in report['content']['options'] if o['issueId'] in keys and o.get('changesCore',True)==(key=='B')] if report else []
                instruction='采用以下方案修改：'+dumps(options) if options else ('提出并落实一个不改变目标、结局、核心看点的新方案。' if key=='A' else '调整引发问题的情节元素，保留主题和主要角色。')
                if key=='A' and not options:
                    proposal=self._continuous_discuss(cid,stage,'针对这些问题提出不改变目标、结局、核心看点的 newProposal。reply 只写具体方案，不改稿。'+dumps(issues),base)
                    instruction='落实此新方案：'+proposal
                elif key=='A':
                    proposal=self._continuous_discuss(cid,stage,'从以下不改核心的方案中推荐本次候选的具体改法，结合已失败的修法换一种表现方式。只写推荐方案与理由。'+dumps(dict(options=options,attempts=value['cursor']['attempts'])),base)
                    instruction='落实模型推荐的方案：'+proposal
                instruction += (' 所有待解决问题如下，逐项考虑；在本阶段落实本阶段的修订，'
                                '并为后续阶段保留必要条件。没有对应备选方案的问题也须处理。'
                                +dumps(issues))
            for retry in range(2):
                token=candidate_scope.set(cid)
                try:
                    self._continuous_create(cid,stage,instruction+' 提交完整阶段稿。'+('本候选上次不合格，请换另一种表现方式。' if retry else ''),base)
                    candidate=self._continuous_art(eid,stage)['versions'][-1]
                    if stage=='story':
                        self._continuous_submit_review(cid,candidate['id'],f'{cid}:candidate-review:{candidate["id"]}')
                        candidate=self._continuous_art(eid,stage)['versions'][-1]
                    gate=self.adoption_gate(candidate['id'])
                    problems=list(gate['formatErrors'])+(check_story(candidate['content']) if stage=='story' else [])
                    if gate['blocking']:
                        problems.extend(str(item.get('reason') or item) if isinstance(item,dict) else str(item) for item in gate['blocking'])
                    if problems:
                        rejected.extend(problems)
                        continue
                    for issue in gate['unresolved']:
                        self.waive(issue,candidate['id'],'连续制作：普通意见按本版放行（AI）',f'{cid}:waive:{issue}')
                        self._continuous_log(cid,stage,'waive',issue,'候选普通意见按本版放行',dict(versionId=candidate['id']))
                    if not self.adoption_gate(candidate['id'])['adoptable']: continue
                    summary=self._continuous_discuss(cid,stage,'概括本候选与原采用版相比改了什么，最多三句，用给导演看的白话。只写摘要。',candidate['id'])
                    candidates.append(dict(key=key,label=('保留核心，换一种呈现' if key=='A' else '调整问题情节的版本'),stage=stage,versionId=candidate['id'],summary=summary,recommended=key=='A',check='独审通过' if stage=='story' else '结构与采用检查通过'))
                    break
                except CandidateRejected as exc:
                    rejected.append(str(exc))
                    self._continuous_cursor(cid,queue=[])
                finally:
                    candidate_scope.reset(token)
        if not candidates:
            self._continuous_problem(cid,'候选均未通过检查：'+'；'.join(dict.fromkeys([str(i.get('evidence') or i.get('problem','')) for i in issues]+rejected)))
        materials=dict(original=value['cursor'].get('storyGoal') or self._direction_inputs(eid)['idea'],
                       issues=issues, current=self._continuous_version(base),
                       candidates=[dict(key=c['key'],content=self._continuous_version(c['versionId'])) for c in candidates])
        allowed={c['key'] for c in candidates}
        def validate(answer):
            if not isinstance(answer,dict) or answer.get('key') not in allowed or not isinstance(answer.get('reason'),str) or not answer['reason'].strip():
                return ['须选择一个实际候选并写明理由']
            return []
        answer,_=self._continuous_bounded_json(cid,'返修选稿',base,
            '代导演从已通过检查的实际稿件中选最合适的一份。优先保留原故事方向、结局、角色目标和核心看点，'
            '比较具体动作能否解决审查问题，选择改动最小而有效的版本。直接决定。reply 为 {"key":"候选编号","reason":"具体理由"}。',
            materials,validate,stage=stage,fallback=lambda:dict(key=next((c['key'] for c in candidates if c.get('recommended')),candidates[0]['key']),reason='选用已经通过检查、优先保留原故事方向的候选'))
        candidate=next(c for c in candidates if c['key']==answer['key'])
        self._continuous_log(cid,stage,'expert_judgment',candidate['versionId'],answer['reason'],dict(choice=answer,base=base))
        cursor=self._continuous_record(cid)['cursor']
        cursor.update(ignoredVersions=[v['id'] for a in self.episode_view(eid)['artifacts'] for v in a['versions']],
                      stressReports=0,rounds={},planId=None,queue=[],externalDrafts=False)
        self._continuous_update(cid,cursor=cursor,choice=None)
        self._continuous_adopt(cid,stage,candidate['versionId'])
        if kind=='repair':
            with connect(self.db_path) as db: impacts=pending_changes(db,eid)
            downstream={i['id']: True for i in issues if issue_stage(i.get('stage')) != stage}
            if impacts or downstream:
                job=self.start_repair_plan(eid,'upstream',f'{cid}:expert-upstream:{candidate["versionId"]}',
                                           report['id'] if downstream else None,downstream or None)
                self._continuous_repair(cid,job['planId'])
            if keys:
                job=self.start_repair_plan(eid,'verify',f'{cid}:verify-choice:{candidate["versionId"]}',report['id'],{key:True for key in keys})
                result=self._continuous_repair(cid,job['planId'])
                self._continuous_cursor(cid,planId=None)
                if result=='unresolved':
                    with connect(self.db_path) as db:
                        steps=json.loads(row(db,'SELECT steps FROM repair_plan WHERE id=?',job['planId'])['steps'])
                    findings=[f"{f['id']}：{f['note']}" for s in steps for f in s.get('findings',[]) if f['status']=='仍存在']
                    remaining={f['id'] for step in steps for f in step.get('findings',[]) if f['status']=='仍存在'}
                    target=min((issue_stage(i['stage']) for i in issues if i['id'] in remaining),key=STAGES.index)
                    self._continuous_cursor(cid,stage=target)
                    self._continuous_problem(cid,'专家修订后仍未解决：'+'；'.join(findings),
                                             repair_base=self._continuous_art(eid,target)['adopted_version_id'])
            else:
                self._continuous_create(cid,'stress')
                fresh=self._continuous_art(eid,'stress')['versions'][-1]
                self._continuous_adopt(cid,'stress',fresh['id'])
                self._continuous_cursor(cid,stressReports=1,planId=None)
        # Restart the stage walk under the existing worker lock.
        raise UserChange()
