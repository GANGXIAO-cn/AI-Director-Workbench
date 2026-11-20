"""Continuous delivery verification, preview and explicit package acceptance."""
import json
from .db import connect,dumps,new_id
from .errors import Rejected
from .stressrepairs import pending_changes
from .productionnotes import same_handoff
from .workflow import needs_manuscript_review


class ContinuousDelivery:
    def _continuous_settle_report_impacts(self,cid):
        eid=self._continuous_record(cid)['episode_id']
        from .stressrepairs import upstream_basis,adopted
        from .db import transaction
        retired=[]
        with transaction(self.db_path) as db:
            report=adopted(db,eid).get('stress')
            if report:
                settled=self._settle_verified_report_impacts(db,report,upstream_basis(db,eid))
                if settled:
                    owned=set(self._continuous_record(cid)['cursor'].get('ownedRuns',[]))
                    retired=[r['id'] for r in db.execute("SELECT v.id,v.source_run_id FROM version v JOIN run r ON r.id=v.source_run_id JOIN task t ON t.id=r.task_id WHERE v.base_id=? AND t.goal LIKE '根据核对结果修订受影响内容：%'",(report,)) if r['source_run_id'] in owned]
        if retired:
            cursor=self._continuous_record(cid)['cursor']
            self._continuous_cursor(cid,ignoredVersions=list(dict.fromkeys([*cursor.get('ignoredVersions',[]),*retired])))
        with connect(self.db_path) as db:
            impacts=[dict(r) for r in db.execute("SELECT * FROM impact WHERE episode_id=? AND state='pending' AND kind='workflow_change' AND source_ref LIKE 'stress:%'",(eid,))]
            settled=[]
            for impact in impacts:
                version=db.execute('SELECT content,base_id FROM version WHERE id=?',(impact['source_ref'].split(':',1)[1],)).fetchone()
                base=db.execute('SELECT content FROM version WHERE id=?',(version['base_id'],)).fetchone() if version else None
                if not base:continue
                old,new=json.loads(base[0]),json.loads(version['content'])
                if same_handoff(old,new):settled.append(impact)
        for impact in settled:
            self.resolve_impact(impact['id'],f'{cid}:report-text-only:{impact["id"]}')
            self._continuous_log(cid,'delivery','choose_option',impact['id'],'审查报告仅更新诊断文字，制作交接未变，沿用已有成果',{})
        if settled:
            stages={i['target_ref'].removeprefix('workflow:') for i in settled}
            cursor=self._continuous_record(cid)['cursor'];obsolete=[]
            with connect(self.db_path) as db:
                for rid in cursor.get('queue',[]):
                    task=db.execute('SELECT t.stage,t.goal,r.status FROM run r JOIN task t ON t.id=r.task_id WHERE r.id=?',(rid,)).fetchone()
                    if task and task['stage'] in stages and task['status']!='running' and task['goal'].startswith('根据核对结果修订受影响内容：'):obsolete.append(rid)
            if obsolete:self._continuous_cursor(cid,queue=[rid for rid in cursor.get('queue',[]) if rid not in obsolete],runId=None)

    def _continuous_delivery_problem(self,cid,problem):
        value=self._continuous_record(cid);eid=value['episode_id'];stage=problem['stage'];reason=problem['problem']
        if stage=='publish':
            art=self._continuous_art(eid,'publish')
            if art and art['adopted_version_id']:
                self._continuous_create(cid,'publish','按当前采用材料修订发布文案和配乐说明：'+reason,art['adopted_version_id'])
            self._continuous_stage(cid,'publish')
            return True
        if reason.endswith(' 尚未采用') or stage=='spec':self._continuous_problem(cid,reason)
        if stage=='space':
            self._continuous_confirm_supplements(cid,'prompts')
            return self._image_fill_records(cid)
        if stage=='cast':
            self._continuous_cast(cid)
            return True
        prompts=self._continuous_art(eid,'prompts')
        vid=prompts['adopted_version_id'] if prompts else None
        if stage=='recheck' and problem.get('segment'):
            final=self._continuous_prompt_revision(cid,vid,set(problem.get('affectedSegments') or [problem['segment']]),
                '按当前已采用分镜同步本段变化，保留其他段；完成提示词自身内容检查：'+reason)
            final=self._continuous_prompt_checks(cid,final)
            self._continuous_adopt(cid,'prompts',final)
            return True
        if stage=='review':
            final=self._continuous_prompt_checks(cid,vid)
            if final!=vid:self._continuous_adopt(cid,'prompts',final)
            return True
        if stage=='recheck':
            with connect(self.db_path) as db:impacted=pending_changes(db,eid)
            if impacted and value.get('mode')!='fix':
                job=self.start_repair_plan(eid,'upstream',f'{cid}:delivery-recheck:{new_id()}')
                self._continuous_repair(cid,job['planId'])
            self._continuous_late_recheck(cid)
            return True
        if stage=='prompts' or problem.get('kind')=='required_reference':
            affected=set(problem.get('affectedSegments') or ([problem['segment']] if problem.get('segment') else self._prompt_problem_segments(cid,vid,reason)))
            if problem.get('kind')=='required_reference':
                reason += '。沿用指定原图 '+problem['file']+'，版本 '+problem['versionId']
            final=self._continuous_prompt_revision(cid,vid,affected,'修复交付核验指出的问题：'+reason)
            final=self._continuous_prompt_checks(cid,final)
            self._continuous_adopt(cid,'prompts',final)
            return True
        if stage=='plan':
            art=self._continuous_art(eid,'plan')
            self._continuous_create(cid,'plan','修复已有交付核验指出的问题，保留无关镜头及剧情：'+reason,base=art['adopted_version_id'])
            self._continuous_adopt(cid,'plan',self._continuous_art(eid,'plan')['versions'][-1]['id'])
            self._continuous_cursor(cid,promptCandidate=None,finishingReceipt=None)
            return self._continuous_finish_stages(cid)
        if problem.get('kind')=='product_reference':
            self._continuous_products(cid)
            self._continuous_cursor(cid,finishingReceipt=None)
            return self._continuous_finish_stages(cid)
        if stage=='assets':
            # Route the concrete broken references back through the same 07
            # generation/assessment path; keep every historical image and file.
            self._continuous_repair_delivery_assets(cid,problem)
            if not self._continuous_images(cid):return False
            self._continuous_cursor(cid,promptCandidate=None,finishingReceipt=None)
            return self._continuous_finish_stages(cid)
        # Existing optional adopted documents (e.g. publish) can become stale.
        art=self._continuous_art(eid,stage)
        if art and art['adopted_version_id']:
            self._continuous_create(cid,stage,'修复交付核验问题：'+reason,base=art['adopted_version_id'])
            self._continuous_adopt(cid,stage,self._continuous_art(eid,stage)['versions'][-1]['id'])
            return True
        self._continuous_problem(cid,reason)

    def _continuous_repair_delivery_assets(self,cid,problem):
        eid=self._continuous_record(cid)['episode_id']
        needs=self.asset_worksheet(eid)['rows']
        art=self._continuous_art(eid,'asset_design');base=art['adopted_version_id']
        instruction='根据具体交付问题修订必要素材条目，保留其余条目。问题：'+dumps(problem)
        self._continuous_create(cid,'asset_design',instruction,base=base)
        self._continuous_adopt(cid,'asset_design',self._continuous_art(eid,'asset_design')['versions'][-1]['id'])
        # Actual file problems name a reference label and segment. Resolve that
        # exact reference to its need; do not reset all adopted images.
        prompts=self._continuous_art(eid,'prompts')
        content=self._continuous_version(prompts['adopted_version_id']) if prompts else {}
        broken={r['assetVersionId'] for sid,entry in content.get('segments',{}).items() if not problem.get('segment') or sid==problem['segment'] for r in entry.get('refs',[]) if problem.get('file')==r.get('label')}
        for need in needs:
            if need['adopted_version_id'] in broken:
                self.set_asset_need(eid,need['need_key'],need['revision'],'generate',f'{cid}:delivery-image:{new_id()}',prompt=need['image_prompt']+'\n'+problem['problem'])
                state=self._image_state(cid,need['need_key'])
                self._image_state(cid,need['need_key'],attempts=[],priorAttempts=[*state.get('priorAttempts',[]),*state['attempts']],assessedRevision=None,selected=None)

    def _continuous_prepare_handoff(self,cid):
        value=self._continuous_checkpoint(cid);eid=value['episode_id']
        self._continuous_settle_report_impacts(cid)
        while True:
            problems,_=self.delivery_problems(eid,'full')
            if not problems:break
            problem=problems[0];cursor=self._continuous_record(cid)['cursor']
            key=dumps({k:problem.get(k) for k in ('stage','kind','segment','file')});attempts=cursor.get('deliveryAttempts',{})
            if attempts.get(key,0)>=2:self._continuous_problem(cid,problem['problem'],segments=problem.get('affectedSegments') or ([problem['segment']] if problem.get('segment') else None))
            attempts[key]=attempts.get(key,0)+1
            self._continuous_cursor(cid,deliveryAttempts=attempts,stage=problem['stage'],phase='repair')
            if not self._continuous_delivery_problem(cid,problem):return False
            self._continuous_checkpoint(cid)
        return True

    def _continuous_delivery(self,cid):
        value=self._continuous_checkpoint(cid)
        eid=value['episode_id']
        self._continuous_cursor(cid,stage='delivery',phase='verify')
        cursor=value['cursor']
        if cursor.get('roleProtocol'):
            with connect(self.db_path) as db:
                receipts=self._role_receipts(db,eid)
                missing=[]
                for rec in receipts:
                    owned=db.execute("SELECT 1 FROM adoption a JOIN ai_decision d ON d.ref=a.id AND d.kind='adopt' WHERE d.continuous_id=? AND a.version_id=?",(cid,rec['version'])).fetchone()
                    if not owned:continue
                    stage=rec['stage']
                    review_needed=needs_manuscript_review(stage,skips_diagnosis=not any(r['stage']=='stress' for r in receipts))
                    if (stage!='prompts' and not rec['ruling']) or (review_needed and not rec['review']):
                        missing.append(stage)
            if missing:
                self._continuous_update(cid,state='failed',reason='交付核验未通过，本版缺少独审或专家决定：'+'、'.join(missing))
                return False
        result=self.deliver(eid,'full',f'{cid}:deliver:{cursor.get("deliveryRound",0)}')
        delivery=self.delivery_view(result['deliveryId'])
        # A resumed episode may already have its adopted publication copy from an older workbench.
        from .filmauto import resume_delivery
        resume_delivery(self, delivery['id'])
        self._continuous_cursor(cid,stage='publish',phase='done',deliveryId=delivery['id'])
        self._continuous_update(cid,state='done',reason='发布文案与图文交付已完成，视频和配乐由后台接续，专家将自动选材精剪',choice=None)
        return True

    def _continuous_late_recheck(self,cid):
        from .decisions import _text_fields
        run=self._continuous_record(cid);eid=run['episode_id']
        with connect(self.db_path) as db:
            impacts=[dict(r) for r in db.execute("SELECT * FROM impact WHERE episode_id=? AND state='pending' AND kind IN ('workflow_change','decision_change','supplement_change','palette_change')",(eid,)) if r['target_ref'].startswith('workflow:') and (run.get('mode')=='fix' or r['target_ref'][9:] not in ('story','characters','world','treatment','script'))]
        for impact in impacts:
            stage=impact['target_ref'][9:];art=self._continuous_art(eid,stage)
            if not art or not art['adopted_version_id']:continue
            vid=art['adopted_version_id'];content=self._continuous_version(vid)
            if stage=='prompts':
                instruction='核对当前上游变化是否影响该阶段采用稿。reply JSON {"needsRevision":true|false,"reason":"具体依据","quote":"当前稿中的逐字引文","segments":["受影响段号"]}。不受影响则保留，不得凭空宣称已修复；如须修改，segments 仅列确实受影响的段号（只改备注可留空数组），禁止整批重写。\n'+dumps(dict(impact=impact,current=content,context=self._fix_context(eid)))
            else:
                instruction='核对当前上游变化是否影响该阶段采用稿。reply JSON {"needsRevision":true|false,"reason":"具体依据","quote":"当前稿中的逐字引文"}。不受影响则保留，不得凭空宣称已修复。\n'+dumps(dict(impact=impact,current=content,context=self._fix_context(eid)))
            answer=self._continuous_json(cid,stage,instruction,lambda a:isinstance(a,dict) and type(a.get('needsRevision')) is bool and isinstance(a.get('reason'),str) and bool(a['reason'].strip()) and isinstance(a.get('quote'),str) and bool(a['quote'].strip()) and any(a['quote'] in text for text in _text_fields(content)),base=vid)
            self._continuous_checkpoint(cid)
            if answer['needsRevision']:
                run=self._continuous_record(cid)
                target_segments=[]; notes_only=False; top_notes_only=False
                if stage=='prompts':
                    seg_map=content.get('segments') or {}
                    raw_segs=answer.get('segments')
                    explicit_segs=[s for s in raw_segs if isinstance(s,str) and s in seg_map] if isinstance(raw_segs,list) else []
                    quote_hits=[sid for sid,seg in seg_map.items() if any(answer['quote'] in text for text in _text_fields(seg))]
                    if explicit_segs:
                        target_segments=list(dict.fromkeys(explicit_segs))
                    elif quote_hits:
                        target_segments=quote_hits
                    if target_segments and quote_hits and all(answer['quote'] not in str((seg_map.get(sid) or {}).get('txt') or '') for sid in target_segments):
                        notes_only=True
                    if not target_segments and answer['quote'] in str(content.get('notes') or ''):
                        mentioned=[sid for sid in seg_map if sid in answer['quote'] or sid in answer['reason']]
                        if mentioned:
                            target_segments=mentioned
                            notes_only=True
                        else:
                            top_notes_only=True
                    if not target_segments and not top_notes_only:
                        target_segments=self._prompt_problem_segments(cid,vid,answer['reason'])
                if run.get('mode')=='fix':
                    tasks=run['scope']
                    scoped_segments=sorted({s for t in tasks for s in t['segments']} | set(target_segments))
                    if stage=='prompts' and target_segments:
                        scoped_segments=sorted(set(target_segments))
                    task=dict(type='other',stages=[stage],targets=[],segments=scoped_segments,scenes=sorted({s for t in tasks for s in t['scenes']}),mappedScenes=sorted({s for t in tasks for s in t.get('mappedScenes',[])}),needKeys=sorted({s for t in tasks for s in t['needKeys']}),instruction=run['cursor']['fixText']+'\n'+answer['reason'],reason=answer['reason'])
                    self._fix_stage(cid,stage,task)
                elif stage=='prompts':
                    if top_notes_only:
                        self._continuous_create(cid,'prompts','仅修正顶层 notes 备注，不改动任何段正文：'+answer['reason'],base=vid,prompt_images=False)
                        revised=self._continuous_art(eid,'prompts')['versions'][-1]['id']
                    else:
                        revised=self._continuous_prompt_revision(cid,vid,target_segments,'根据核对结果修订受影响段：'+answer['reason'],notes_only=notes_only)
                    revised=self._continuous_prompt_checks(cid,self._continuous_shot_observations(cid,revised))
                    self._continuous_adopt(cid,stage,revised)
                else:
                    self._continuous_create(cid,stage,'根据核对结果修订受影响内容：'+answer['reason'],base=vid)
                    revised=self._continuous_art(eid,stage)['versions'][-1]['id']
                    self._continuous_adopt(cid,stage,revised)
            else:self.resolve_impact(impact['id'],f'{cid}:keep-impact:{impact["id"]}')
            self._continuous_log(cid,stage,'choose_option',impact['id'],answer['reason'],dict(judgment=answer,versionId=vid))
