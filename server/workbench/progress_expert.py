"""Small, optional judgments at material bottlenecks; ordinary execution stays in control."""
import json
from .db import connect, transaction, dumps, new_id
from .errors import Rejected, Conflict, row
from .executors.base import ExecRequest
from .executors.cli import redact
from .storage import canonical, sha256_bytes


def provisional_image_issues(db, episode_id, version_id):
    """Only current adopted material versions can supply provisional issue receipts."""
    records=db.execute("""SELECT i.* FROM continuous_issue i JOIN asset_need n
        ON n.episode_id=i.episode_id AND i.target='need:'||n.need_key
        WHERE i.episode_id=? AND i.version_id=? AND i.state='provisional'
        AND n.removed=0 AND n.adopted_version_id=i.version_id""",(episode_id,version_id))
    return [dict(asset_version_id=version_id,issue_key=key,text=r['text'],state='provisional')
            for r in records for key in json.loads(r['issue_refs'])]


class ProgressExpert:
    def film_executors(self, episode_id, stage):
        from . import filmroles
        with connect(self.db_path) as db:
            return {role: self.active_selection(slot) for role, slot in
                    filmroles.selections(db, episode_id, stage).items()}

    def save_film_executor(self, episode_id, stage, role, provider, model=None, effort=None, command_id=None):
        from . import filmroles
        key = filmroles.settings_key(episode_id, stage)
        if role not in ("create", "review"):
            raise Rejected("请选择写稿或独立评审")
        self.validate_selection(provider, model, effort)
        slot = dict(provider=provider, model=model, effort=effort)
        with transaction(self.db_path) as db:
            def save():
                self._episode_in(db, episode_id)
                db.execute("INSERT INTO app_setting(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
                           "SET value=json_set(app_setting.value,?,json(?))",
                           (key, dumps({role: slot}), "$." + role, dumps(slot)))
                return slot
            return self._command(db, command_id, "save_film_executor", [key, role, slot], save)

    def executor_defaults(self):
        saved = self.get_setting('executor-defaults', {})
        create = saved.get('create', dict(provider='codex', model=None, effort=None))
        return {role:self.active_selection(slot) for role,slot in dict(create=create,
                    review=saved.get('review', dict(provider='codex', model=None, effort=None)),
                    expert=saved.get('expert', dict(create))).items()}

    def save_executor_defaults(self, plan, command_id=None):
        if not isinstance(plan, dict) or set(plan) != {'create', 'review', 'expert'}:
            raise Rejected('请设置起草、独审和专家')
        for slot in plan.values():
            self.validate_selection(slot.get('provider'), slot.get('model'), slot.get('effort'))
        self.put_setting('executor-defaults', plan)
        return self.executor_defaults()

    def episode_expert(self, episode_id):
        with connect(self.db_path) as db:
            rec = db.execute('SELECT executor_plan FROM kickoff WHERE episode_id=?', (episode_id,)).fetchone()
        plan = json.loads(rec[0]) if rec else {}
        return self.active_selection(plan.get('expert') or self.executor_defaults()['expert'])

    def save_episode_expert(self, episode_id, provider, model=None, effort=None, command_id=None):
        self.validate_selection(provider, model, effort)
        with transaction(self.db_path) as db:
            def save():
                rec = row(db, 'SELECT executor_plan FROM kickoff WHERE episode_id=?', episode_id)
                plan = json.loads(rec['executor_plan']); plan['expert'] = dict(provider=provider, model=model, effort=effort)
                db.execute('UPDATE kickoff SET executor_plan=? WHERE episode_id=?', (dumps(plan), episode_id))
                return plan['expert']
            return self._command(db, command_id, 'episode_expert', [episode_id,provider,model,effort], save)

    def _material_issue(self, cid, need, assessment=None, text='', state='provisional'):
        vid = assessment['versionId'] if assessment else None
        refs = [i['type']+':'+i['where'] for i in assessment['critical']] if assessment else []
        # A blocked verdict without detailed issues still needs an explicit scoped receipt.
        if assessment and not refs: refs = ['verdict:'+assessment['verdict']]
        with transaction(self.db_path) as db:
            previous = db.execute('SELECT id FROM continuous_issue WHERE continuous_id=? AND target=? AND version_id IS ? AND state=?',
                                  (cid, 'need:'+need['need_key'], vid, state)).fetchone()
            ident = previous[0] if previous else new_id()
            db.execute('INSERT INTO continuous_issue VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET issue_refs=excluded.issue_refs,text=excluded.text,updated_at=excluded.updated_at',
                       (ident,need['episode_id'],cid,'need:'+need['need_key'],vid,dumps(refs),state,text or (assessment or {}).get('reason','此项缺少可用素材'),self.clock(),self.clock(),need['revision']))
        return ident

    def material_issues(self, episode_id):
        # Reconcile manual worksheet changes as part of the read model too.
        with transaction(self.db_path) as db:
            items = [dict(r) for r in db.execute('SELECT * FROM continuous_issue WHERE episode_id=? ORDER BY created_at,id', (episode_id,))]
            for item in items:
                if item['state'] in ('resolved','superseded'): continue
                need = db.execute('SELECT * FROM asset_need WHERE episode_id=? AND need_key=? AND removed=0', (episode_id,item['target'][5:])).fetchone()
                if not need or (item['version_id'] and need['adopted_version_id'] != item['version_id']) or (item['state']=='missing' and (need['adopted_version_id'] or need['decision']=='text_only')):
                    item['state']='superseded'
                    db.execute("UPDATE continuous_issue SET state='superseded',updated_at=? WHERE id=?",(self.clock(),item['id']))
                item['issue_refs']=json.loads(item['issue_refs'])
                if item['state']=='provisional' and item['issue_refs']:
                    accepted={r[0] for r in db.execute('SELECT issue_key FROM accepted_image_issue WHERE asset_version_id=?',(item['version_id'],))}
                    if set(item['issue_refs']) <= accepted:
                        item['state']='accepted'
                        db.execute("UPDATE continuous_issue SET state='accepted',updated_at=? WHERE id=?",(self.clock(),item['id']))
                item['title']=need['title'] if need else item['target']
        return items

    def _material_usable(self, eid, assessment, need_key=None):
        from .continuous_images import usable
        if usable(assessment): return True
        vid=assessment['versionId']
        keys={i['type']+':'+i['where'] for i in assessment['critical']} or {'verdict:'+assessment['verdict']}
        with connect(self.db_path) as db:
            accepted={r[0] for r in db.execute('SELECT issue_key FROM accepted_image_issue WHERE asset_version_id=?',(vid,))}
            params=[eid,vid]
            sql="SELECT issue_refs FROM continuous_issue WHERE episode_id=? AND version_id=? AND state IN ('provisional','accepted')"
            if need_key: sql+=' AND target=?'; params.append('need:'+need_key)
            allowed={key for r in db.execute(sql,params) for key in json.loads(r[0])}
        return keys <= accepted | allowed

    def accept_material_issue(self, issue_id, command_id=None):
        with connect(self.db_path) as db:
            previous=self._existing_command(db,command_id,'accept_material_issue',issue_id) if command_id else None
            if previous:return previous
            item=dict(row(db,'SELECT * FROM continuous_issue WHERE id=?',issue_id))
        need=self._need(item['episode_id'],item['target'][5:])
        if item['state']!='provisional' or need['adopted_version_id']!=item['version_id']:
            raise Conflict('图片已变化，请按当前图片处理')
        values=[r['body'] for r in self.image_assessments(item['episode_id'],need['need_key']) if r['version_id']==item['version_id']]
        assessment=values[-1]
        refs=set(json.loads(item['issue_refs']))
        with transaction(self.db_path) as db:
            def accept():
                decision=new_id()
                detail=dict(userDirection=dict(instruction='用这张，保留这个问题'),versionId=item['version_id'],issueRefs=list(refs))
                db.execute('INSERT INTO ai_decision VALUES(?,?,?,?,?,?,?,?,NULL,?)',(decision,item['continuous_id'],item['episode_id'],'assets','accept_issue',issue_id,'导演接受图片问题',dumps(detail),self.clock()))
                for issue in assessment['critical']:
                    key=issue['type']+':'+issue['where']
                    if key in refs: db.execute('INSERT OR IGNORE INTO accepted_image_issue VALUES(?,?,?,?,?)',(item['version_id'],key,issue['text'],decision,self.clock()))
                db.execute("UPDATE continuous_issue SET state='accepted',updated_at=? WHERE id=?",(self.clock(),issue_id))
                return dict(issueId=issue_id)
            return self._command(db,command_id,'accept_material_issue',issue_id,accept)

    def _material_judgment(self, cid, need, assessments, reason, actions, wait_for=()):
        """One cached judgment per exact material snapshot, at most one format retry."""
        self._continuous_checkpoint(cid)
        slot=self.episode_expert(need['episode_id'])
        evidence=dict(target=need['need_key'],revision=need['revision'],title=need['title'],description=need['description'],
                      candidates=assessments,reason=reason,actions=actions,attempts=self._image_state(cid,need['need_key'])['attempts'],waitFor=list(wait_for))
        key=sha256_bytes(canonical([evidence,slot]))
        state=self._image_state(cid,need['need_key'])
        if state.get('judgmentKey')==key:return state.get('judgment')
        props={k:{'type':'string'} for k in ('action','target','versionId','instruction','reason','summary')}
        props['action']['enum']=actions
        props['issueKeys']={'type':'array','items':{'type':'string'}}
        schema=dict(type='object',additionalProperties=False,required=list(props),properties=props)
        prompt=('你只处理素材制作卡点，不重新创作或审稿。只选 actions 中实际可执行的动作。'
                '小问题直接沿用候选，不追求精确复刻。外观基准的细刻度、指针角度、微小文字或装饰偏差不触发 edit；已有此类 critical 时选 provisional 保留说明继续，不再反复校准。'
                'provisional 必须选择 candidates 的 versionId，并逐条列出该图所有 critical 的 类型:位置；'
                '没有 critical 时用 verdict:结论。fill_input 补写 imagePrompt，text_only 仅用于可文字降级项。'
                'wait 只在 waitFor 非空时可选，summary 必须写出 waitFor 中实际等待的对象。'
                'edit 只用于局部、可描述的错误（整体构图或身份错误不要选 edit）：versionId 选要修的候选，instruction 写成“只修改……，其余保持不变”。'
                'target 必须为当前 target；instruction 只用于补写、降级或局部修图。'
                'summary 用白话说明发生什么、试过什么和下一步；不宣称问题已解决。以下是资料：\n'+dumps(evidence))
        answer=None; error=''
        for attempt in range(2):
            rid=new_id(); actual=None; connection=slot
            try:
                connection=self.run_connection(slot['provider'],slot.get('model'),slot.get('effort'))
                work=self.store.run_dir(rid)/'work';work.mkdir(parents=True)
                req=ExecRequest(rid,'progress_expert','assets',prompt,schema,'ephemeral',work,connection['model'],connection['effort'],connection=connection)
                result=self.executors[slot['provider']].start(req)
                if not result.ok: raise Rejected(result.diagnostic or '专家不可用')
                actual=result.reported_model
                out=result.structured
                if not isinstance(out,dict) or any(k not in out for k in props):raise ValueError('字段不完整')
                if any(not isinstance(out[k],str) for k in props if k!='issueKeys'):raise ValueError('文字字段格式不合格')
                if out['action'] not in actions or out['target']!=need['need_key'] or not out['summary'].strip() or not out['reason'].strip():raise ValueError('动作、对象或说明不合格')
                if out['action']=='provisional':
                    candidate=next((a for a in assessments if a['versionId']==out['versionId']),None)
                    expected={i['type']+':'+i['where'] for i in candidate['critical']} or {'verdict:'+candidate['verdict']} if candidate else set()
                    if not candidate or not isinstance(out['issueKeys'],list) or set(out['issueKeys'])!=expected:raise ValueError('暂用版本或问题键不匹配')
                if out['action']=='fill_input' and not out['instruction'].strip():raise ValueError('缺少补写指令')
                # 实测 143：等待必须有实际等待对象，不能把质量问题说成等待。
                if out['action']=='wait' and not any(w in out['summary'] or w in out['reason'] for w in wait_for):raise ValueError('等待须写明实际等待对象：'+'、'.join(wait_for))
                if out['action']=='edit' and (not any(a['versionId']==out['versionId'] for a in assessments) or not out['instruction'].strip()):raise ValueError('局部修图须指定候选和修改指令')
                answer=out;error=''
            except (Rejected,ValueError,TypeError,KeyError,OSError) as exc:
                error=redact(str(exc),500)
            self._continuous_log(cid,'assets','expert_judgment',rid,answer['reason'] if answer else '专家判断未完成',dict(provider=slot['provider'],requestedModel=connection.get('model'),model=actual,effort=connection.get('effort'),input=evidence,output=answer,error=error))
            self._continuous_checkpoint(cid)
            current=self._need(need['episode_id'],need['need_key'])
            if current['revision']!=need['revision']:
                from .continuous import UserChange
                raise UserChange()
            if answer:break
            prompt+='\n上次回包不合格：'+error
        self._image_state(cid,need['need_key'],judgmentKey=key,judgment=answer)
        self._continuous_cursor(cid,expertUnavailable=bool(error))
        return answer

    def _library_pick(self, cid, need, entries):
        """按文字挑出可能适用于本条目的库图，最多两张；判断不了就不用库图，直接出图。"""
        if not entries:return []
        slot=self.episode_expert(need['episode_id'])
        listing=[dict(id=e['id'],name=e['name'],usage=e.get('usage'),limits=e.get('limits'),aspect=e.get('aspect')) for e in entries]
        need_info=dict(target=need['need_key'],title=need['title'],description=need['description'],scenes=need.get('scene_ids'))
        schema=dict(type='object',additionalProperties=False,required=['ids','reason'],
                    properties=dict(ids=dict(type='array',items=dict(type='string')),reason=dict(type='string')))
        prompt=('从项目图库中挑出能直接充当本素材条目的图（同一地点、同一物件或同一角色），最多两张；'
                '只是同类或沾边的不选，没有合适的返回空数组。只依据名称与用途说明判断。reply JSON {"ids":[库图 id],"reason":"一句话理由"}。\n'
                +dumps(dict(need=need_info,library=listing)))
        rid=new_id(); picked=[]; error=''; actual=None; connection=slot
        try:
            connection=self.run_connection(slot['provider'],slot.get('model'),slot.get('effort'))
            work=self.store.run_dir(rid)/'work';work.mkdir(parents=True)
            result=self.executors[slot['provider']].start(ExecRequest(rid,'progress_expert','assets',prompt,schema,'ephemeral',work,connection['model'],connection['effort'],connection=connection))
            if not result.ok:raise Rejected(result.diagnostic or '专家不可用')
            actual=result.reported_model
            out=result.structured; known={e['id'] for e in entries}
            if not isinstance(out,dict) or not isinstance(out.get('ids'),list):raise ValueError('回包格式不合格')
            picked=[i for i in dict.fromkeys(out['ids']) if i in known][:2]
            reason=str(out.get('reason') or '')
        except (Rejected,ValueError,TypeError,KeyError,OSError) as exc:
            error=redact(str(exc),500); reason='库图挑选未完成，本条目直接出图'
        self._continuous_log(cid,'assets','expert_judgment',need['need_key'],reason or ('选用库图' if picked else '图库没有适用图'),
                             dict(purpose='library_pick',provider=slot['provider'],requestedModel=connection.get('model'),model=actual,picked=picked,offered=len(entries),error=error))
        return [e for e in entries if e['id'] in picked]
