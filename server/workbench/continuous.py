"""Episode-scoped, persistent orchestration; all writing uses ordinary run inputs."""
import json
import logging
import threading

from .db import connect, transaction, dumps, new_id
from .errors import Conflict, Rejected, row
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
ACTIVE = ('running', 'paused', 'waiting_choice', 'waiting_quota', 'blocked')
FORMAT_REASONS = {'schema', '回包两次不符合契约', '已收到回包，未通过入库校验'}


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
            if changes.get("state") in ("blocked","paused","waiting_quota","waiting_choice"):
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
                FROM continuous_run c JOIN episode e ON e.id=c.episode_id JOIN project p ON p.id=e.project_id
                WHERE c.rowid IN (SELECT max(rowid) FROM continuous_run GROUP BY episode_id) AND c.state IN ('blocked','waiting_choice')
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
        return dict(run=value,execution=execution,methods=methods,decisions=decisions,expert=self.episode_expert(episode_id),issues=self.material_issues(episode_id),methodNotice=f'本项目有 {len(missing)} 份方法本集尚未采用，将按本集现有方法运行' if missing else '')

    def start_continuous_precheck(self, episode_id, target_stage='stress', command_id=None, image_options=None):
        if target_stage not in ('stress','delivery'): raise Rejected('请选择故事审查或图文交付')
        with connect(self.db_path) as db:
            self._kickoff(db,episode_id)
            if db.execute("SELECT 1 FROM continuous_run WHERE episode_id=? AND state NOT IN ('done','cancelled')",(episode_id,)).fetchone(): raise Conflict('本集已有连续制作，请从当前进度继续')
            if any(episode_id in json.loads(r[0]) for r in db.execute("SELECT episodes FROM expert_auth WHERE state='running'")): raise Conflict('本集专家授权正在运行')
            if db.execute("SELECT 1 FROM run r JOIN task t ON t.id=r.task_id WHERE t.episode_id=? AND r.status IN ('queued','running')",(episode_id,)).fetchone(): raise Conflict('本集有正在执行的任务，结束后再开始')

    def start_continuous(self, episode_id, target_stage='stress', command_id=None, image_options=None):
        if target_stage not in ('stress','delivery'):
            raise Rejected('请选择故事审查或图文交付')
        with transaction(self.db_path) as db:
            self._kickoff(db,episode_id)
            def create():
                if db.execute("SELECT 1 FROM continuous_run WHERE episode_id=? AND state NOT IN ('done','cancelled')",(episode_id,)).fetchone():
                    raise Conflict('本集已有连续制作，请从当前进度继续')
                if any(episode_id in json.loads(r[0]) for r in db.execute("SELECT episodes FROM expert_auth WHERE state='running'")):
                    raise Conflict('本集专家授权正在运行')
                if db.execute("SELECT 1 FROM run r JOIN task t ON t.id=r.task_id WHERE t.episode_id=? AND r.status IN ('queued','running')",(episode_id,)).fetchone():
                    raise Conflict('本集有正在执行的任务，结束后再开始')
                cid=new_id()
                cursor=dict(imageOptions=image_options or {},enableDirections=target_stage=='delivery',base=adopted(db,episode_id),attempts={},rounds={},stressReports=0,queue=[],ownedRuns=[],userStamp=self._continuous_user_stamp(db,episode_id,[]))
                db.execute('INSERT INTO continuous_run(id,episode_id,target_stage,state,cursor,reason,choice,pause_requested,created_at,updated_at) VALUES(?,?,?,?,?,NULL,NULL,0,?,?)',(cid,episode_id,target_stage,'running',dumps(cursor),self.clock(),self.clock()))
                return dict(continuousId=cid)
            result=self._command(db,command_id,'start_continuous',[episode_id,target_stage,image_options],create)
        return self._advance_continuous(result['continuousId'])

    def pause_continuous(self, continuous_id):
        self._continuous_update(continuous_id,pause_requested=1)
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
        for value in db.execute("SELECT * FROM continuous_run WHERE episode_id=? AND state IN ('running','waiting_choice','waiting_quota','blocked')", (eid,)).fetchall():
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

    def _continuous_stop(self, cid, state, reason, blocks=None):
        from .continuous_images import cursor_lock
        with cursor_lock(cid):
            cursor=self._continuous_record(cid)['cursor']
            cursor['pendingBlocks']=blocks or []
            self._continuous_update(cid,state=state,reason=reason,cursor=cursor)
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
        if name in (f'{cid}:retry:{parent}',f'{cid}:restart-retry:{parent}'):
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
                    action_text = f"导演继续后用 {run['provider']} 重试" if parent['provider'] == run['provider'] else f"回包未通过格式或契约校验，换 {run['provider']} 重试"
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
            ancestor=run['id']; switched=None; switched_from=None
            while True:
                origin=self._retry_origin(db,cid,ancestor)
                if not origin or origin[0]=='manual':break
                if origin[0]=='format-switch':
                    if origin[1]==cursor.get('formatRound',0):
                        switched=ancestor
                        switched_from=row(db,'SELECT provider FROM run WHERE id=?',origin[2])['provider']
                    break
                ancestor=origin[2]
            stage=row(db,'SELECT stage FROM task WHERE id=?',run['task_id'])['stage']
            eid=row(db,'SELECT episode_id FROM continuous_run WHERE id=?',cid)['episode_id']
            plan={**self._kickoff(db,eid)['executor_plan'],**self._stage_executors(db,eid,stage)}
        reason=run.get('diagnostic') or (run.get('reason') or '').split(' → 已重试为 ')[0]
        if switched:
            verb='重试过一次' if switched_from==run['provider'] else '换过一次执行器'
            self._continuous_stop(cid,'blocked',f"本轮已{verb}（{run['provider']}），仍未通过：{reason}")
        candidates=[plan.get('review',{}),self.episode_expert(eid),dict(provider='codex')]
        seen={run['provider']}
        for slot in candidates:
            provider=slot.get('provider')
            if not provider or provider in seen or provider not in self.executors:continue
            seen.add(provider)
            connection=self.run_connection(provider,slot.get('model'),slot.get('effort'))
            try:self.validate_selection(provider,connection['model'],connection['effort'])
            except Rejected:continue
            return self.retry_run(run['id'],provider=provider,model=connection['model'],effort=connection['effort'],
                                  command_id=f"{cid}:format-switch:{cursor.get('formatRound',0)}:{run['id']}")['runId']
        if cursor.get('formatRound',0)>0 and run['provider'] in self.executors:
            connection=self.run_connection(run['provider'],run.get('model'),run.get('effort'))
            try:
                self.validate_selection(run['provider'],connection['model'],connection['effort'])
                return self.retry_run(run['id'],provider=run['provider'],model=connection['model'],effort=connection['effort'],
                                      command_id=f"{cid}:format-switch:{cursor.get('formatRound',0)}:{run['id']}")['runId']
            except Rejected:
                pass
        self._continuous_stop(cid,'blocked',f'回包未通过格式或契约校验，没有其他可用执行器：{reason}')

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
            self._continuous_stop(cid,'paused','已暂停，可继续')
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
                run=dict(row(db,'SELECT * FROM run WHERE id=?',rid))
            if run['purpose'] in ('cold_read','comparison'):
                if run['status']=='running':raise RunWait()
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
                if run['purpose'] in ('create','revise','parallel') and (run.get('reason') or '').split(' → 已重试为 ')[0] in FORMAT_REASONS:
                    retry=self._format_retry(cid,run,cursor)
                    cursor['queue'][0]=retry
                    self._continuous_update(cid,cursor=cursor)
                    continue
                if run['status']!='waiting_quota' and cursor.get('retried',{}).get(rid):
                    self._continuous_stop(cid,'blocked',run.get('diagnostic') or run.get('reason') or '运行失败',blocks=self._run_blocks(run))
                retry=self.retry_run(rid,command_id=f'{cid}:retry:{rid}',**self._continuous_retry_slot(cid,run))['runId']
                cursor['queue'][0]=retry
                cursor.setdefault('retried',{})[retry]=True
                self._continuous_update(cid,cursor=cursor)
                continue
            follow=self.execute_run(rid) if run['status']=='queued' else []
            with connect(self.db_path) as db:
                run=dict(row(db,'SELECT * FROM run WHERE id=?',rid))
            if run['status']=='waiting_quota':
                self._continuous_stop(cid,'waiting_quota','执行器额度用完，恢复后点继续',blocks=self._run_blocks(run))
            if run['status']=='failed':
                continue
            if run['status']!='succeeded':
                if run['purpose']=='image_assess' and run['status'] in ('running','preparing'):
                    from .continuous_images import MaterialWait
                    raise MaterialWait('正在评估图片',running=True)
                if run['status'] in ('running','preparing'):
                    self._continuous_cursor(cid,waitingRun=rid)
                    self._continuous_update(cid,reason=None)
                    raise RunWait()
                self._continuous_stop(cid,'blocked',run.get('reason') or '运行尚未完成')
            cursor=self._continuous_record(cid)['cursor']
            cursor.pop('waitingRun',None)
            cursor['queue']=[*cursor['queue'][1:],*follow]
            self._continuous_update(cid,cursor=cursor)
            self._continuous_checkpoint(cid)

    def _continuous_art(self,eid,stage):
        return next((a for a in self.episode_view(eid)['artifacts'] if a['stage']==stage),None)

    def _continuous_note_errors(self,eid,stage,content):
        from . import productionnotes
        if stage not in productionnotes.STAGES:return []
        with connect(self.db_path) as db:basis=productionnotes.context(db,eid)
        errors=productionnotes.check(stage,content,basis,coverage=False)
        if stage=='plan':
            errors.extend(str(shot.get('id','镜头'))+' 缺少 visibleRoles 或 propStates' for shot in content.get('shots',[]) if not isinstance(shot.get('visibleRoles'),list) or not isinstance(shot.get('propStates'),dict))
        return errors

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
            if errors: self._continuous_stop(cid,'blocked','；'.join(errors))
        gate=self.adoption_gate(vid)
        gate['formatErrors'].extend(self._continuous_note_errors(eid,stage,version['content']))
        if stage=='prompts':
            placements=self.product_placements(eid,candidate=version['content'])
            if placements['verified']<placements['minimum']:gate['formatErrors'].append('商品入景覆盖不足：须同时有当前镜头记录、必用标记和候选提示词引用')
        if stage=='story' and gate['review'] is None:
            self._continuous_execute(cid,self.submit_review(vid,f'{cid}:adoption-review:{vid}')["runId"])
            gate=self.adoption_gate(vid)
        if gate['formatErrors'] or gate['blocking']:
            self._continuous_stop(cid,'blocked','；'.join(gate['formatErrors']) or '仍有必须修正的问题')
        for issue in gate['unresolved']:
            self.waive(issue,vid,'连续制作：普通意见按本版放行（AI）',f'{cid}:waive:{issue}')
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

    def _continuous_create(self,cid,stage,goal=None,base=None,auto=False,readonly=False,prompt_images=None):
        value=self._continuous_checkpoint(cid); eid=value['episode_id']
        art=self._continuous_art(eid,stage)
        with connect(self.db_path) as db:
            baseline=adopted(db,eid)
            plan={**self._kickoff(db,eid)['executor_plan'],**self._stage_executors(db,eid,stage)}
        self._continuous_cursor(cid,stage=stage,phase='draft',base=baseline)
        executor={role:plan[role] for role in ('create','review')}
        if stage=='stress': executor['create']=plan['review']
        instruction=goal or (value['cursor'].get('storyGoal') if stage=='story' else None) or workflow.CONTINUOUS_GOALS[stage]
        if stage=='story' and value['target_stage']=='delivery':instruction+='\n故事稿须同时填写 title 标题。'
        job=self.create_task(eid,stage,instruction,auto_revise=auto,executor=executor,
                             base_version_id=base,prompt_images=((not readonly) if prompt_images is None else prompt_images) if stage=='prompts' else None,discussion_only=readonly,workspace_revision=art['revision'] if art and readonly else None,
                             command_id=f'{cid}:create:{new_id()}')
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
            if stage=='story' and not pending['gate']['review']:
                self._continuous_execute(cid,self.submit_review(pending['id'],f'{cid}:resume-review:{pending["id"]}')["runId"])
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
            self._continuous_create(cid,stage,'按结构错误修正：'+'；'.join(errors),version['id'],auto=stage=='story')
            version=self._continuous_art(eid,stage)['versions'][-1]
        if stage=='story' and version['gate']['blocking']:
            history=[dict(goal=t['goal'],messages=t['messages'],reviews=[v['reviews'] for v in art['versions']]) for t in art['tasks']]
            self._continuous_create(cid,stage,'前两轮已用的修法与独审意见：'+dumps(history)+'。换一种表现方式解决同一问题，不改主题和主要角色。',version['id'],auto=False)
            current=self._continuous_art(eid,stage)['versions'][-1]
            self._continuous_execute(cid,self.submit_review(current['id'],f'{cid}:changed-method-review:{current["id"]}')["runId"])
            version=self._continuous_art(eid,stage)['versions'][-1]
            if version['gate']['blocking']:
                return self._continuous_choices(cid,'story',stage,version['id'])
        self._continuous_adopt(cid,stage,version['id'])
        if stage=='stress':
            self._continuous_cursor(cid,stressReports=self._continuous_record(cid)['cursor']['stressReports']+1)

    def _continuous_repair(self,cid,pid):
        self._continuous_cursor(cid,planId=pid,phase='repair')
        while True:
            self._continuous_execute(cid)
            with connect(self.db_path) as db:
                plan=dict(row(db,'SELECT * FROM repair_plan WHERE id=?',pid))
            if plan['state']!='open': return plan['state']
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
            if self._continuous_record(cid)['state'] in ('done','cancelled'):
                self._continuous_cursor(cid,blocks=[],wakeCounts={},autoScope=None)
            lock.release()

    def _run_continuous(self,cid):
        try:
            value=self._continuous_checkpoint(cid); eid=value['episode_id']
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
                if art and art['adopted_version_id'] and not impacted and not unadopted and not cursor.get('userDrafts',{}).get(stage): continue
                self._continuous_stage(cid,stage)
            self._continuous_stress(cid)
            if value['target_stage']=='delivery' and not self._continuous_images(cid):return self._continuous_record(cid)
            if value['target_stage']=='delivery' and not self._continuous_finish_stages(cid):return self._continuous_record(cid)
            if value['target_stage']=='delivery':self._continuous_delivery(cid)
            else:self._continuous_update(cid,state='done',reason='01–06 已完成')
        except UserChange:
            raise
        except RunWait:
            pass
        except Halt:
            self._commit_blocks(cid)
        except Conflict as exc:
            self._continuous_checkpoint(cid)
            self._continuous_update(cid,state='paused',reason=str(exc)+'；点继续后从实际状态接续')
        except (Rejected,OSError,ValueError) as exc:
            self._continuous_cursor(cid,blocks=[],wakeCounts={},autoScope=None,pendingBlocks=[])
            self._continuous_update(cid,state='blocked',reason=str(exc))
        except Exception as exc:
            logging.exception('Continuous production %s failed',cid)
            self._continuous_cursor(cid,blocks=[],wakeCounts={},autoScope=None,pendingBlocks=[])
            self._continuous_update(cid,state='blocked',reason=f'执行中断：{type(exc).__name__}：{exc}')
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
                stage=min((issue_stage(i['stage']) for i in content['issues'] if i['id'] in keys),key=STAGES.index)
                return self._continuous_choices(cid,'repair',stage,None)
            goal=('为每条必须修正的问题选择本轮返修方案。只能从给出的 options 中选择 changesCore 为 false 的方案；如果已用方案都失败，可写 newProposal（不改变目标、结局、核心看点）。reply 为 JSON 数组，每项 issueId、optionIndex（整数或 null）、newProposal（字符串或 null）、reason（一句话）。第二轮必须选择与第一轮不同的方案，或写出新的方案。\n'+dumps(dict(options=options,attempts=cursor['attempts'])))
            selected=None
            for attempt in range(2):
                job=self._continuous_create(cid,'stress',goal,readonly=True)
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
                        if cursor['rounds'].get(key,0) and history and history[-1]['method']==method:
                            raise ValueError('第二轮须换一种修法')
                        selected[key]=choice
                        x['method']=method
                    break
                except (ValueError,KeyError,TypeError) as exc:
                    selected=None
                    goal+='\n上次回包错误：'+str(exc)+'；请只修正格式和选择。'
            if selected is None: self._continuous_stop(cid,'blocked','选方案回包两次不符合契约')
            for x in answer:
                key=x['issueId']
                cursor['attempts'].setdefault(key,[]).append(dict(round=cursor['rounds'].get(key,0)+1,method=x['method'],result='待复核'))
            self._continuous_update(cid,cursor={**self._continuous_record(cid)['cursor'], 'attempts':cursor['attempts']})
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
            for retry in range(2):
                try:
                    self._continuous_create(cid,stage,instruction+' 提交完整阶段稿。'+('本候选上次不合格，请换另一种表现方式。' if retry else ''),base)
                    candidate=self._continuous_art(eid,stage)['versions'][-1]
                    if stage=='story':
                        self._continuous_execute(cid,self.submit_review(candidate['id'],f'{cid}:candidate-review:{candidate["id"]}')['runId'])
                        candidate=self._continuous_art(eid,stage)['versions'][-1]
                    gate=self.adoption_gate(candidate['id'])
                    if gate['formatErrors'] or gate['blocking'] or (stage=='story' and check_story(candidate['content'])): continue
                    for issue in gate['unresolved']:
                        self.waive(issue,candidate['id'],'连续制作：普通意见按本版放行（AI）',f'{cid}:waive:{issue}')
                        self._continuous_log(cid,stage,'waive',issue,'候选普通意见按本版放行',dict(versionId=candidate['id']))
                    if not self.adoption_gate(candidate['id'])['adoptable']: continue
                    summary=self._continuous_discuss(cid,stage,'概括本候选与原采用版相比改了什么，最多三句，用给导演看的白话。只写摘要。',candidate['id'])
                    candidates.append(dict(key=key,label=('保留核心，换一种呈现' if key=='A' else '调整问题情节的版本'),stage=stage,versionId=candidate['id'],summary=summary,recommended=key=='A',check='独审通过' if stage=='story' else '结构与采用检查通过'))
                    break
                except Halt:
                    stopped=self._continuous_record(cid)
                    if stopped['state']!='blocked': raise
                    self._continuous_update(cid,state='running',reason=None)
                    self._continuous_cursor(cid,queue=[],pendingBlocks=None)   # 候选内部停下已转回运行，不留待定稿的受阻项
        current=None
        if len(candidates)<2:
            forbidden=[i for i in issues if i.get('category') in ('source','decision','required_setting','format')]
            reason='；'.join(str(i.get('category'))+'：'+str(i.get('evidence') or i.get('problem')) for i in forbidden)
            current=dict(stage=stage,versionId=base,label='当前版本',adoptable=kind=='repair' and not forbidden,disabledReason=('仅供对照：仍有必须修正的问题' if kind=='story' else reason or None))
        title='两种改好的版本，请选一份'
        if kind=='story' and not candidates:
            with connect(self.db_path) as db:
                problems=[dict(r) for r in db.execute('SELECT i.reason,s.content FROM issue i JOIN review r ON r.id=i.review_id LEFT JOIN setting_entry s ON s.id=i.setting_id WHERE r.version_id=? AND i.blocking=1',(base,))]
            title='仍需处理：'+'；'.join(p['content'] or p['reason'] for p in problems)
        choice=dict(kind=kind,title=title,candidates=candidates,current=current,free=True,stage=stage,reportVersionId=report['id'] if report else None,issueIds=sorted(keys),settings=kind=='story' and not candidates)
        self._continuous_update(cid,choice=choice,state='waiting_choice',reason=title)
        if not candidates and current and not current['adoptable'] and kind=='repair':
            self._continuous_update(cid,state='blocked',reason=current['disabledReason'])
        raise Halt()

    def choose_continuous(self,continuous_id,choice_key,command_id=None,text=None,files=()):
        lock=self._continuous_choice_locks.setdefault(continuous_id,threading.Lock())
        if not lock.acquire(blocking=False): raise Conflict('正在处理此选择，请稍候')
        try:
            return self._choose_continuous(continuous_id,choice_key,command_id,text,files)
        finally:
            lock.release()

    def _choose_continuous(self,continuous_id,choice_key,command_id=None,text=None,files=()):
        value=self._continuous_record(continuous_id); choice=value['choice']; eid=value['episode_id']
        if value['state']!='waiting_choice' or not choice: raise Conflict('当前没有待选稿件')
        if choice['kind']=='preview':
            if choice_key=='free':return self.fix_after_delivery(eid,text,command_id)
            return self._choose_delivery_preview(value,choice_key)
        if choice['kind'] in ('visual','image_failed'):return self._choose_image_card(value,choice_key,text,command_id,files)
        if choice['kind']=='direction': return self._choose_direction(value,choice_key,text,files)
        if choice['kind']=='kickoff_conflict': return self._choose_kickoff(value,choice_key,text,files)
        current=choice.get('current')
        candidate=next((c for c in choice['candidates'] if c['key']==choice_key),None)
        if choice_key=='current':
            if not current or not current['adoptable']: raise Rejected('当前版本只供对照，不能采用')
        elif candidate is None:
            raise Rejected('请选择卡片中可用的版本')
        cursor=value['cursor']; cursor['ignoredVersions']=[v['id'] for a in self.episode_view(eid)['artifacts'] for v in a['versions']]; cursor.update(stressReports=0,rounds={},planId=None,queue=[],externalDrafts=False)
        with transaction(self.db_path) as db:
            claimed=db.execute("UPDATE continuous_run SET state='running',cursor=?,reason=NULL,pause_requested=0,updated_at=? WHERE id=? AND state='waiting_choice'",(dumps(cursor),self.clock(),continuous_id))
            if claimed.rowcount!=1: raise Conflict('此选择已处理，请刷新进度')
        try:
            if choice_key=='current':
                for key in choice['issueIds']:
                    self.carry_stress_issue(eid,choice['reportVersionId'],key,'用户选择保持现状，带着问题继续','当前版本',f'{continuous_id}:carry:{key}:{command_id}')
            else:
                self._continuous_adopt(continuous_id,candidate['stage'],candidate['versionId'])
                if choice['kind']=='repair':
                    with connect(self.db_path) as db: impacts=pending_changes(db,eid)
                    if impacts:
                        job=self.start_repair_plan(eid,'upstream',f'{continuous_id}:choice-upstream:{command_id}')
                        self._continuous_repair(continuous_id,job['planId'])
                    self._continuous_create(continuous_id,'stress')
                    fresh=self._continuous_art(eid,'stress')['versions'][-1]
                    self._continuous_adopt(continuous_id,'stress',fresh['id'])
                    self._continuous_cursor(continuous_id,stressReports=1,planId=None)
            # Retain choice receipt for a comparison route that is still open.
            choice['selected']=choice_key
            self._continuous_update(continuous_id,choice=choice)
            return self._advance_continuous(continuous_id)
        except UserChange:
            return self._advance_continuous(continuous_id)
        except Halt:
            return self._continuous_record(continuous_id)
        except (Rejected,Conflict) as exc:
            self._continuous_update(continuous_id,state='paused',reason=str(exc))
            return self._continuous_record(continuous_id)
