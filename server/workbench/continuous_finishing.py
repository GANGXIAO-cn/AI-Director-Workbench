"""Continuous acting, shot planning, observations and segment reviews."""
import json
from .worksheet import asset_product_code
from .db import connect,transaction,dumps,new_id
from .errors import Rejected,row
from . import timeline,shotrefs,productionnotes


from .continuous_judgments import ContinuousJudgments, compact_shots, fingerprint


from .promptrepairs import PromptRepairs


class ContinuousFinishing(ContinuousJudgments, PromptRepairs):
    def _continuous_internal_version(self,cid,vid):
        cursor=self._continuous_record(cid)['cursor']
        cursor['ownedVersions']=list(dict.fromkeys([*cursor.get('ownedVersions',[]),vid]))
        # Internal version writes use existing public editing commands. Reconcile
        # their notification without swallowing a concurrent user's real draft.
        with connect(self.db_path) as db:
            eid=self._continuous_record(cid)['episode_id']
            actual=[r['id'] for r in db.execute('SELECT v.id,v.source_run_id FROM version v JOIN artifact a ON a.id=v.artifact_id WHERE a.episode_id=? ORDER BY v.created_at,v.rowid',(eid,)) if r['source_run_id'] not in cursor['ownedRuns'] and r['id'] not in cursor['ownedVersions']]
        if actual==cursor.get('userStamp',actual):cursor['pendingUserChange']=False
        self._continuous_update(cid,cursor=cursor)
        return vid

    def _continuous_json(self,cid,stage,instruction,validate,base=None,prompt_images=None):
        for attempt in range(2):
            try:
                answer=json.loads(self._continuous_discuss(cid,stage,instruction,base,prompt_images=prompt_images))
                if validate(answer):return answer
            except (ValueError,TypeError,KeyError,AttributeError):pass
            instruction+='\n上次回复格式不合格，请按指定 JSON 契约重回。'
        self._continuous_problem(cid,'只读判断格式重试后仍不符合契约',allow_content_repair=False)

    def _continuous_cast(self,cid):
        eid=self._continuous_record(cid)['episode_id'];art=self._continuous_art(eid,'plan')
        plan=next(v['content'] for v in art['versions'] if v['id']==art['adopted_version_id'])
        matrix=self.cast_matrix(eid)
        for segment in matrix['rows']:
            sid=segment['segment'];shots=[s for s in plan['shots'] if s['segment']==sid]
            visible={role for shot in shots for role in shot.get('visibleRoles',[])}
            for role in matrix['roles']:
                cell=segment['cells'].get(role,{})
                if cell.get('status') in ('ok','absent'):continue
                vid=cell.get('assetVersionId');note='按采用方案的 visibleRoles 确认出镜'
                present=role in visible
                vid=vid or matrix['roster'][role].get('assetVersionId')
                if present and not vid:self._continuous_problem(cid,sid+' · '+role+' 在镜头中出场，但尚无采用的身份图')
                self.set_cast(eid,sid,role,'ok' if present else 'absent',vid if present else None,note=note,command_id=f'{cid}:cast:{art["adopted_version_id"]}:{sid}:{role}:{vid}:{present}')
                self._continuous_log(cid,'plan','fill_observation',sid+':'+role,note,dict(status='ok' if present else 'absent',assetVersionId=vid if present else None))

    def _continuous_shot_observations(self,cid,vid,segments=None):
        # Compatibility entry point for existing repair callers. Observations
        # remain historical evidence, never a prompt assembly prerequisite.
        return vid

    def _continuous_version(self,vid):
        with connect(self.db_path) as db:return json.loads(row(db,'SELECT content FROM version WHERE id=?',vid)['content'])

    def _continuous_products(self,cid):
        eid=self._continuous_record(cid)['episode_id']
        placement_state=self.product_placements(eid,check_prompt_refs=False)
        if not placement_state['minimum']:return
        plan_art=self._continuous_art(eid,'plan');plan_id=plan_art['adopted_version_id']
        plan=self._continuous_version(plan_id);shots=compact_shots(plan)
        cursor=self._continuous_record(cid)['cursor']
        with connect(self.db_path) as db:
            drawing_allowed=set(self._kickoff(db,eid)['spec'].get('productDrawingReferences',[]))
        pending=cursor.get('pendingProductBindings') or {}
        if pending.get('planId')!=plan_id:pending={}
        grid_ids={s['id'] for s in plan['segments'] if s['strategy']=='grid'}
        outdated=any(r.get('drawingReference') and (r['code'] not in drawing_allowed or r['segment'] not in grid_ids)
                     for r in placement_state['records'])
        if not pending and not outdated and placement_state['verified']>=placement_state['minimum']:
            scopes={}
            for r in placement_state['records']:
                if not r['issue']:scopes.setdefault((r['code'],r['scene_version']),[]).append(r['shot_id'])
            for (code,vid),shot_ids in scopes.items():
                rule=self._continuous_image_rule(eid,vid)
                if not set(shot_ids)<=set((rule or {}).get('shotIds',[])):
                    self._continuous_bind(cid,vid,shot_ids,plan_id,'product:'+code,fingerprint([plan_id,code,vid,shot_ids]),'沿用已登记商品镜头',rule)
            direct={r['segment'] for r in placement_state['records'] if not r['issue'] and r['directReference']}
            if any(s['strategy']=='grid' and s['id'] in direct for s in plan['segments']):
                self._product_flex(cid,plan_art,plan_id,plan,direct)
            return
        with connect(self.db_path) as db:
            pid=self._episode_in(db,eid)['project_id']
            products=[dict(r) for r in db.execute('SELECT code,version_id,source FROM episode_product WHERE episode_id=? ORDER BY code',(eid,))]
        catalog=[json.loads(r['source']) for r in products] or self.project_resources(pid,eid)['products']
        if not catalog:raise Rejected('项目商品目录缺少可使用的商品主图')
        bycode={r['code']:r for r in catalog}
        ids={s['id'] for s in shots}
        materials=dict(products=[dict(code=r['code'],name=r['name']) for r in catalog],shots=shots,minimum=placement_state['minimum'])
        pinned={(code,shot['id']) for code,product in bycode.items() for shot in shots
                if product['name'] in dumps(shot) or any(r['code']==code and r['shot_id']==shot['id'] for r in placement_state['records'])}
        def valid(a):
            entries=a.get('placements') if isinstance(a,dict) else None
            if not isinstance(entries,list) or not all(isinstance(v,dict) and v.get('code') in bycode and v.get('shotId') in ids and isinstance(v.get('intent'),str) and v['intent'].strip() for v in entries):return ['须引用实际商品和镜头并说明用途']
            actual={(v['code'],v['shotId']) for v in entries}
            if not pinned<=actual:return ['保留分镜指定的商品镜头']
            return [] if len(actual)>=materials['minimum'] else ['须覆盖规定数量的商品落点']
        def use_existing_shots():
            picks=[]
            for code,product in bycode.items():
                for shot in shots:
                    if product['name'] in dumps(shot) or any(r['code']==code and r['shot_id']==shot['id'] for r in placement_state['records']):
                        picks.append((code,shot['id']))
            pairs=list(dict.fromkeys(picks+[(code,shot['id']) for shot in shots for code in bycode]))
            return dict(placements=[dict(code=code,shotId=sid,intent='此镜按独立商品图呈现'+bycode[code]['name']) for code,sid in pairs[:max(len(picks),materials['minimum'])]])
        if pending:
            answer,basis=pending['answer'],pending['basis']
        else:answer,basis=self._continuous_bounded_json(cid,'商品镜头安排',plan_id,
            '若分镜已有商品镜头，沿用其位置；否则选自然落点。商品主图独立随段输入，空景无需显示商品。reply {"placements":[{"code":"目录编号","shotId":"已有镜号","intent":"摆放或使用安排"}]}。',materials,valid,fallback=use_existing_shots)
        byshot={s['id']:s for s in shots}
        segments={byshot[v['shotId']]['segment'] for v in answer['placements'] if v['code'] not in drawing_allowed}
        grid_ids={s['id'] for s in plan['segments'] if s['strategy']=='grid'}-segments
        drawings={}

        if any(s['id'] in segments and s['strategy']=='grid' for s in plan['segments']):
            plan_id=self._product_flex(cid,plan_art,plan_id,plan,segments)
        self._continuous_cursor(cid,pendingProductBindings=dict(planId=plan_id,answer=answer,basis=basis))
        scopes={}
        for v in answer['placements']:
            code=v['code'];sid=v['shotId']
            vid=self.use_project_product(eid,code,f'{cid}:product-ref:{code}')['versionId']
            target=byshot[sid]['segment']
            if target in grid_ids:
                drawings.setdefault(target,[]).append(dict(code=code,shotId=sid,intent=v['intent'],master=vid))
                continue
            self.save_product_placement(eid,code,sid,v['intent'],vid,'',f'{cid}:product-placement:{plan_id}:{code}:{sid}:{basis}')
            scopes.setdefault((code,vid),[]).append(sid)
        for (code,vid),shot_ids in scopes.items():
            self._continuous_bind(cid,vid,list(dict.fromkeys(shot_ids)),plan_id,'product:'+code,basis,'视频段携带独立商品主图',self._continuous_image_rule(eid,vid))

        self._continuous_cursor(cid,pendingProductBindings=None,productDrawings=drawings)

    def _product_flex(self,cid,art,plan_id,plan,segments):
        updated=[dict(s,strategy='flex') if s['id'] in segments else s for s in plan['segments']]
        result=self.save_manual(art['id'],plan_id,dict(segments=updated),f'{cid}:product-flex:{plan_id}:'+fingerprint(updated),note='商品主图独立随段输入')
        vid=self._continuous_internal_version(cid,result['versionId'])
        from .workflow import binding_candidate
        working=binding_candidate.get()
        if working and working['episode']==art['episode_id']:
            working['version']=vid
        else:
            self._continuous_adopt(cid,'plan',vid)
        return vid

    def _continuous_prompt_revision(self,cid,vid,affected,instruction,*,notes_only=False):
        eid=self._continuous_record(cid)['episode_id'];old=self._continuous_version(vid)
        from . import timeline, stages
        plan=self._continuous_version(self._continuous_art(eid,'plan')['adopted_version_id'])
        affected=set(affected)
        for segment in ([] if notes_only else timeline.layout(plan)['segments']):
            sid=segment['id'];entry=old['segments'].get(sid)
            if not entry or stages.check_segment_prompt(sid,entry,segment['duration_ms'],segment['strategy'],segment['shots']):affected.add(sid)
        current_ids={s['id'] for s in plan['segments']}
        resegmented=set(old['segments'])!=current_ids
        affected.intersection_update(current_ids)
        if affected:
            self._continuous_create(cid,'prompts',instruction+'。只返回当前请求指定段的完整内容。保留未变化窗口文字。',base=vid,prompt_images=True,prompt_segments=sorted(affected),prompt_notes_only=notes_only)
            new=self._continuous_art(eid,'prompts')['versions'][-1]
        elif resegmented:
            new=next(v for v in self._continuous_art(eid,'prompts')['versions'] if v['id']==vid)
        else:
            return vid
        content=new['content']
        plan=self._continuous_version(self._continuous_art(eid,'plan')['adopted_version_id'])
        current_ids={s['id'] for s in plan['segments']}
        # Preserve exact content, references and observation receipts outside the
        # affected scope, even when the model reformats its complete reply.
        merged={sid:entry for sid,entry in content['segments'].items() if sid in current_ids}
        for sid,entry in old['segments'].items():
            if sid not in affected and sid in current_ids:merged[sid]=entry
        if set(merged)!=current_ids:self._continuous_problem(cid,'分段修订未覆盖当前方案全部段号')
        preserved=dict(old,segments=merged)
        if 'notes' in content:preserved['notes']=content['notes']
        if 'decisionNotes' in content:
            from .decisions import revised_segment_notes
            preserved['decisionNotes']=revised_segment_notes(old.get('decisionNotes',[]),content['decisionNotes'],affected,current_ids)
        if resegmented or preserved!=content:
            content=preserved
            with transaction(self.db_path) as db:
                result=self._new_version(db,new['artifact_id'] if 'artifact_id' in new else self._continuous_art(eid,'prompts')['id'],new['id'],'manual',content,baseline_from=new['id'])
            result=self._continuous_internal_version(cid,result)
        else:result=new['id']
        from . import promptbasis
        with transaction(self.db_path) as db:
            promptbasis.record(db,result,vid,self._continuous_version(result),plan,scope=affected)
        self._set_prompt_candidate(cid,result)
        result=self._continuous_prepare_supplements(cid,'prompts',result)
        self._set_prompt_candidate(cid,result)
        return self._set_prompt_candidate(cid,self._continuous_shot_observations(cid,result,affected))

    def _continuous_structure(self,cid,vid,segment,issues):
        eid=self._continuous_record(cid)['episode_id'];art=self._continuous_art(eid,'plan');base=art['adopted_version_id'];plan=self._continuous_version(base)
        shots=[s for s in plan['shots'] if s['segment']==segment]
        answer=self._continuous_json(cid,'plan','解决该段结构问题，决定简化动作或拆段。reply JSON {"action":"simplify|split","firstShotOfSecond":"拆段时第二段首镜编号，否则 null","reason":"具体改法"}。\n'+dumps(dict(segment=segment,shots=shots,issues=issues)),lambda a:isinstance(a,dict) and a.get('action') in ('simplify','split') and isinstance(a.get('reason'),str) and bool(a['reason'].strip()) and (a['action']=='simplify' or a.get('firstShotOfSecond') in {s['id'] for s in shots[1:]}),base)
        affected={segment}
        if answer['action']=='split':
            split=self.split_segment(base,segment,answer['firstShotOfSecond'],f'{cid}:split:{new_id()}')
            plan_vid=self._continuous_internal_version(cid,split['versionId']);affected.update(split['mapping'][segment])
        else:
            self._continuous_create(cid,'plan','只简化 '+segment+' 段动作与结构，其他段及镜头保持不变。'+answer['reason']+'\n问题原文：'+dumps(issues),base=base)
            candidate=self._continuous_art(eid,'plan')['versions'][-1];changed=candidate['content']
            if [s for s in changed['shots'] if s['segment']!=segment]!=[s for s in plan['shots'] if s['segment']!=segment] or [s for s in changed['segments'] if s['id']!=segment]!=[s for s in plan['segments'] if s['id']!=segment]:
                self._continuous_problem(cid,'结构修订改变了未受影响段，需保留原段后重试')
            plan_vid=candidate['id']
        self._continuous_adopt(cid,'plan',plan_vid)
        self._continuous_cast(cid)
        self._continuous_required_bindings(cid)
        self._continuous_empty_scene_bindings(cid)
        self._continuous_products(cid)
        self._grid_artwork(cid,eid,self._continuous_art(eid,'plan')['adopted_version_id'])
        return self._continuous_prompt_revision(cid,vid,affected,'按新采用镜头方案和段号映射重写受影响段，更新受影响段的引用和用途说明，继承已采用绑定，不新增逐镜图片审核。'+dumps(answer))

    def _prompt_problem_segments(self,cid,vid,reason):
        import re
        eid=self._continuous_record(cid)['episode_id']
        with connect(self.db_path) as db:plan=self._adopted_content(db,eid,'plan')
        ids={s['id'] for s in plan['segments']}
        def mentioned(key):
            return re.search(r'(?<![a-zA-Z0-9_-])'+re.escape(key)+r'(?![a-zA-Z0-9_-])',reason)
        affected={sid for sid in ids if mentioned(sid)}
        affected.update(s['segment'] for s in plan['shots'] if mentioned(s['id']))
        if affected:return affected
        # A location-free diagnostic is a request to locate the fault, never an all-segment edit.
        material=dict(problem=reason,shots={s['id']:s['segment'] for s in plan['shots']},
                      segments={sid:entry.get('txt','') for sid,entry in self._continuous_version(vid).get('segments',{}).items()})
        def validate(answer):
            items=answer.get('segments') if isinstance(answer,dict) else None
            return [] if isinstance(items,list) and items and all(isinstance(s,str) and s in ids for s in items) else ['请给出现有受影响段号']
        answer,_=self._continuous_bounded_json(cid,'定位提示词问题',vid,'根据已发现的问题定位具体段，不重审。reply {"segments":["受影响段号"]}。',material,validate,stage='prompts')
        return set(answer['segments'])

    def _continuous_prompt_checks(self,cid,vid):
        """Writing includes the content check; fix concrete assembly errors locally.

        This is not another review/model judgment pass. Historical review methods
        remain available for reading old records, but active execution never calls them.
        """
        errors=self.adoption_gate(vid)['formatErrors']
        if errors:
            affected=self._prompt_problem_segments(cid,vid,'；'.join(errors))
            vid=self._continuous_prompt_revision(cid,vid,affected,'修正本次提示词的具体格式或引用错误：'+'；'.join(errors))
        return vid

    def _continuous_finish_stages(self,cid):
        from .stressrepairs import pending_changes
        eid=self._continuous_record(cid)['episode_id']
        for stage in ('plan',):
            art=self._continuous_art(eid,stage)
            with connect(self.db_path) as db:
                impacted=any(i['stage']==stage for i in pending_changes(db,eid))
                skipped=db.execute('SELECT reason FROM optional_stage_choice WHERE episode_id=? AND stage=?',(eid,stage)).fetchone()
            if skipped and skipped[0]:continue
            if stage=='plan' and art and art['adopted_version_id']:
                with connect(self.db_path) as db:
                    segment_changes=[dict(r) for r in db.execute("SELECT target_ref,reason FROM impact WHERE episode_id=? AND kind='script_change' AND target_ref LIKE 'plan:%' AND state='pending'",(eid,))]
                if segment_changes:
                    self._continuous_create(cid,'plan','按当前剧本拍摄段仅同步这些段号及直接相邻接点，其他段保留原镜号、顺序和内容；不重写上游：'+dumps(segment_changes),art['adopted_version_id'])
                    impacted=True
            if not art or not art['adopted_version_id'] or impacted:self._continuous_stage(cid,stage)
        self._continuous_cast(cid)
        self._continuous_required_bindings(cid)
        self._continuous_empty_scene_bindings(cid)
        self._continuous_products(cid)
        cursor=self._continuous_record(cid)['cursor'];art=self._continuous_art(eid,'prompts')
        receipt=cursor.get('finishingReceipt')
        if receipt and art and receipt==dict(worksheetRefs=1,plan=self._continuous_art(eid,'plan')['adopted_version_id'],prompts=art['adopted_version_id']):
            if not self.adoption_gate(art['adopted_version_id'])['formatErrors'] and not self._continuous_reference_gaps(eid,self._continuous_version(art['adopted_version_id'])):return True
        # Last whole-content review belongs to 08, after actual reference bindings.
        plan_art=self._continuous_art(eid,'plan')
        plan_id=plan_art['adopted_version_id']
        gate=self.adoption_gate(plan_id)
        if gate['review'] is None:
            self._continuous_cursor(cid,stage='plan',phase='plan_review')
            self._continuous_submit_review(cid,plan_id,f'{cid}:plan-review:{plan_id}')
            gate=self.adoption_gate(plan_id)
        if gate['blocking']:
            with connect(self.db_path) as db:
                issues=[dict(r) for r in db.execute('SELECT loc,reason,category FROM issue WHERE review_id=? AND blocking=1',
                                                   (gate['review']['id'],))]
            self._continuous_create(cid,'plan',
                '只修正本次08审核指出的关键结果问题，保持未涉及的段、镜号、动作因果及已接受素材用途不变。'
                '在本次修订内检查问题是否已落实，不扩充素材状态或重跑上游。关键问题及位置：'+dumps(issues),plan_id)
            self._continuous_stage(cid,'plan')
            self._continuous_cast(cid)
            self._continuous_required_bindings(cid)
            self._continuous_empty_scene_bindings(cid)
            self._continuous_products(cid)
            plan_id=self._continuous_art(eid,'plan')['adopted_version_id']
            gate=self.adoption_gate(plan_id)
        if gate['review'] is None:
            self._continuous_cursor(cid,stage='plan',phase='plan_review')
            self._continuous_submit_review(cid,plan_id,f'{cid}:plan-review:{plan_id}')
            gate=self.adoption_gate(plan_id)
        if gate['blocking']:
            self._continuous_cursor(cid,stage='plan')
            self._continuous_problem(cid,'分镜定稿仍有独审指出的关键问题：'+dumps(gate['blocking']))
        if gate['unresolved']:
            ruling=self._stage_ruling(cid,'plan',next(v for v in self._continuous_art(eid,'plan')['versions'] if v['id']==plan_id),gate)
            for issue in gate['unresolved']:
                self.waive(issue,plan_id,ruling,f'{cid}:plan-waive:{issue}')
        grids=self._grid_artwork(cid,eid,plan_id)
        self._episode_image_review(cid,eid,self.asset_worksheet(eid)['rows'],grids)
        placements=self.product_placements(eid,check_prompt_refs=False)
        if placements['verified']<placements['minimum']:
            self._continuous_problem(cid,'镜头交接的商品落点未完成：对应镜头缺少商品参考绑定。')
        cursor=self._continuous_record(cid)['cursor'];art=self._continuous_art(eid,'prompts')
        pending=next((v['id'] for v in reversed(art['versions']) if v['id'] not in cursor.get('ignoredVersions',[]) and (v.get('source_run_id') in cursor['ownedRuns'] or v['id'] in cursor.get('ownedVersions',[]))),None) if art else None
        vid=self._resume_prompt_candidate(cid,cursor.get('promptCandidate') or pending)
        if not vid:
            self._continuous_create(cid,'prompts',prompt_images=True)
            vid=self._continuous_art(eid,'prompts')['versions'][-1]['id']
        self._set_prompt_candidate(cid,vid)
        plan=self._continuous_version(self._continuous_art(eid,'plan')['adopted_version_id'])
        if set(self._continuous_version(vid)['segments'])!={s['id'] for s in plan['segments']}:
            vid=self._continuous_prompt_revision(cid,vid,set(),'按已采用方案直接接续拆分后的生成段。')
        gaps=self._continuous_reference_gaps(eid,self._continuous_version(vid))
        if gaps:
            vid=self._continuous_prompt_revision(cid,vid,set(gaps),'将已匹配的必用图落实到引用和说明，保留原镜头动作及其他段。'+dumps(gaps))
        self._continuous_cursor(cid,promptCandidate=vid)
        vid=self._continuous_prepare_supplements(cid,'prompts',vid)
        self._set_prompt_candidate(cid,vid)
        vid=self._repair_prompt_notes(cid,vid)
        # Only this stage's expression records may be repaired here.
        errors=self._continuous_note_errors(eid,'prompts',self._continuous_version(vid))
        if errors:self._continuous_problem(cid,'提示词记录待修正：'+'；'.join(errors))
        placements=self.product_placements(eid,candidate=self._continuous_version(vid))
        if placements['verified']<placements['minimum']:
            self._continuous_problem(cid,'提示词未引用已登记商品参考，请修正对应段引用；不在此重写素材或镜头。')
        vid=self._continuous_shot_observations(cid,vid)
        vid=self._continuous_prompt_checks(cid,vid)
        self._continuous_adopt(cid,'prompts',vid)
        self._continuous_cursor(cid,finishingReceipt=dict(worksheetRefs=1,plan=self._continuous_art(eid,'plan')['adopted_version_id'],prompts=self._continuous_art(eid,'prompts')['adopted_version_id']))
        return True
