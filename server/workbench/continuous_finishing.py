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
        self._continuous_stop(cid,'blocked','只读判断格式重试后仍不符合契约')

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
                if present and not vid:self._continuous_stop(cid,'blocked',sid+' · '+role+' 在镜头中出场，但尚无采用的身份图')
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
        placement_state=self.product_placements(eid)
        if not placement_state['minimum']:return
        plan_art=self._continuous_art(eid,'plan');plan_id=plan_art['adopted_version_id']
        plan=self._continuous_version(plan_id);shots=compact_shots(plan)
        design=self._continuous_art(eid,'asset_design')
        if not design or not design['adopted_version_id']:return
        items=self._continuous_version(design['adopted_version_id'])['assets']
        needs={r['need_key']:r for r in self.asset_worksheet(eid)['rows']}
        for item in items:
            code=asset_product_code(item);need=needs.get(item['id']);vid=need.get('adopted_version_id') if need else None
            if not code or not vid:continue
            rule=self._continuous_image_rule(eid,vid)
            with connect(self.db_path) as db:
                product=db.execute('SELECT source,version_id FROM episode_product WHERE episode_id=? AND code=?',(eid,code)).fetchone()
            if not product:continue
            evidence=self._continuous_existing_assessment(eid,vid)
            # Existing shot hashes and scene evidence are authoritative even when
            # old INSERT OR IGNORE lost the image's required scope.
            existing=[r for r in placement_state['records'] if r['code']==code and r['scene_version']==vid
                      and r['observation'].strip() and r['intent'].strip()
                      and r['shot_id'] in {s['id'] for s in plan['shots']}
                      and r['shot_hash']==fingerprint(next(s for s in plan['shots'] if s['id']==r['shot_id']))]
            materials=dict(productCode=code,product=json.loads(product['source']),productVersion=product['version_id'],
                           sceneVersion=vid,assessment=evidence,shots=shots,minimum=placement_state['minimum'],
                           limitations=(rule or {}).get('limitations',''))
            source='product:'+code
            prior=(rule or {}).get('autoBindings',{}).get(source,{})
            current_basis=fingerprint(materials)
            receipt_key='商品入景选择:'+code+':'+vid
            recorded=self._continuous_record(cid)['cursor'].get('judgmentReceipts',{}).get(receipt_key)
            complete_prior=prior.get('basisHash')==current_basis and set(prior.get('shotIds',[]))=={r['shot_id'] for r in existing}
            if existing and (complete_prior or (not prior and not recorded)):
                answer=dict(placements=[dict(shotId=r['shot_id'],intent=r['intent'],observation=r['observation']) for r in existing])
            else:
                if evidence is None:
                    evidence=self.assess_images(eid,[vid],f'{cid}:product-observe:{vid}',context=dict(productCode=code),continuous_id=cid)[0]
                    materials['assessment']=evidence
                    current_basis=fingerprint(materials)
                shot_ids={s['id'] for s in shots}
                def validate(answer):
                    if not isinstance(answer,dict) or not isinstance(answer.get('placements'),list):return ['placements 须为数组']
                    records=answer['placements']
                    if not records:return [] if isinstance(answer.get('unresolvedReason'),str) and answer['unresolvedReason'].strip() else ['无入景镜头时须说明 unresolvedReason']
                    errors=[];seen=set()
                    for record in records:
                        if not isinstance(record,dict):errors.append('入景记录须为对象');continue
                        key=record.get('shotId')
                        if not isinstance(key,str) or key not in shot_ids or key in seen:errors.append('shotId 不存在或重复：'+str(key))
                        else:seen.add(key)
                        for field in ('intent','observation'):
                            if not isinstance(record.get(field),str) or not record[field].strip():errors.append(str(key)+' 缺 '+field)
                    return errors
                instruction='从已提供的场景图观察及当前镜头选择含该商品的镜头。不可将转述称为本轮亲自看图，不臆造商品。reply 内业务对象 {"placements":[{"shotId":"现有镜头编号","intent":"位置朝向、识别特征及用途","observation":"所提供图片观察中的商品描述"}],"unresolvedReason":"无证据时说明"}。'
                answer,_=self._continuous_bounded_json(cid,'商品入景选择',code+':'+vid,instruction,materials,validate)
            self._continuous_checkpoint(cid)
            if self._continuous_art(eid,'plan')['adopted_version_id']!=plan_id or self._continuous_image_rule(eid,vid)!=rule:
                from .continuous import UserChange
                raise UserChange()
            current_need=next((n for n in self.asset_worksheet(eid)['rows'] if n['need_key']==item['id']),{})
            if current_need.get('adopted_version_id')!=vid:
                from .continuous import UserChange
                raise UserChange()
            if not answer['placements']:
                self._continuous_stop(cid,'blocked',code+'：'+answer['unresolvedReason'])
            for record in answer['placements']:
                matching=next((r for r in existing if r['shot_id']==record['shotId'] and r['intent']==record['intent'] and r['observation']==record['observation']),None)
                if matching:continue
                self.save_product_placement(eid,code,record['shotId'],record['intent'],vid,record['observation'],
                    f'{cid}:placement:{code}:{vid}:{current_basis}:{fingerprint(record)}')
                self._continuous_log(cid,'plan','fill_observation',record['shotId'],'商品入景',dict(productCode=code,sceneVersion=vid,**record))
            self._continuous_bind(cid,vid,[r['shotId'] for r in answer['placements']],plan_id,source,current_basis,'商品入景',rule,need_key=item['id'])

    def _continuous_prompt_revision(self,cid,vid,affected,instruction):
        eid=self._continuous_record(cid)['episode_id'];old=self._continuous_version(vid)
        from . import timeline, stages
        plan=self._continuous_version(self._continuous_art(eid,'plan')['adopted_version_id'])
        affected=set(affected)
        for segment in timeline.layout(plan)['segments']:
            sid=segment['id'];entry=old['segments'].get(sid)
            if not entry or stages.check_segment_prompt(sid,entry,segment['duration_ms'],segment['strategy'],segment['shots']):affected.add(sid)
        resegmented=set(old['segments'])!={s['id'] for s in plan['segments']}
        self._continuous_create(cid,'prompts',instruction+' 只修受影响段 '+dumps(sorted(affected))+'。segments只返回这些段的完整内容，其他仍在当前方案中的段由系统从基版继承。照已采用分镜逐窗撰写自然正文，不能机械拼接字段；保留未变化窗口文字。',base=vid,prompt_images=True)
        new=self._continuous_art(eid,'prompts')['versions'][-1];content=new['content']
        plan=self._continuous_version(self._continuous_art(eid,'plan')['adopted_version_id'])
        current_ids={s['id'] for s in plan['segments']}
        # Preserve exact content, references and observation receipts outside the
        # affected scope, even when the model reformats its complete reply.
        merged={sid:entry for sid,entry in content['segments'].items() if sid in current_ids}
        for sid,entry in old['segments'].items():
            if sid not in affected and sid in current_ids:merged[sid]=entry
        if set(merged)!=current_ids:self._continuous_stop(cid,'blocked','分段修订未覆盖当前方案全部段号')
        preserved=dict(old,segments=merged)
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
                self._continuous_stop(cid,'blocked','结构修订改变了未受影响段，需保留原段后重试')
            plan_vid=candidate['id']
        self._continuous_adopt(cid,'plan',plan_vid)
        self._continuous_cast(cid)
        self._continuous_required_bindings(cid)
        self._continuous_empty_scene_bindings(cid)
        self._continuous_products(cid)
        return self._continuous_prompt_revision(cid,vid,affected,'按新采用镜头方案和段号映射重写受影响段，更新受影响段的引用和用途说明，继承已采用绑定，不新增逐镜图片审核。'+dumps(answer))

    def _continuous_prompt_checks(self,cid,vid):
        """Writing includes the content check; fix concrete assembly errors locally.

        This is not another review/model judgment pass. Historical review methods
        remain available for reading old records, but active execution never calls them.
        """
        errors=self.adoption_gate(vid)['formatErrors']
        if errors:
            affected=set(self._continuous_version(vid).get('segments',{}))
            vid=self._continuous_prompt_revision(cid,vid,affected,'修正本次提示词的具体格式或引用错误，并按原尺度检查正文：'+'；'.join(errors))
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
        if receipt and art and receipt==dict(plan=self._continuous_art(eid,'plan')['adopted_version_id'],prompts=art['adopted_version_id']):
            if not self.adoption_gate(art['adopted_version_id'])['formatErrors'] and not self._continuous_reference_gaps(eid,self._continuous_version(art['adopted_version_id'])):return True
        # Last whole-content review belongs to 08, after actual reference bindings.
        plan_art=self._continuous_art(eid,'plan')
        plan_id=plan_art['adopted_version_id']
        gate=self.adoption_gate(plan_id)
        if gate['review'] is None:
            self._continuous_execute(cid,self.submit_review(plan_id,f'{cid}:plan-review:{plan_id}')["runId"])
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
            plan_id=self._continuous_art(eid,'plan')['adopted_version_id']
            gate=self.adoption_gate(plan_id)
        for issue in gate['unresolved']:
            self.waive(issue,plan_id,'08分镜：不影响当前用途的普通意见按本版采用',f'{cid}:plan-waive:{issue}')
        placements=self.product_placements(eid,check_prompt_refs=False)
        if placements['verified']<placements['minimum']:
            self._continuous_stop(cid,'blocked','镜头交接的商品落点未完成：请在素材和镜头阶段落实指定商品场面。')
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
        if errors:self._continuous_stop(cid,'blocked','提示词记录待修正：'+'；'.join(errors))
        placements=self.product_placements(eid,candidate=self._continuous_version(vid))
        if placements['verified']<placements['minimum']:
            self._continuous_stop(cid,'blocked','提示词未引用已登记商品场面，请修正对应段引用；不在此重写素材或镜头。')
        vid=self._continuous_shot_observations(cid,vid)
        vid=self._continuous_prompt_checks(cid,vid)
        self._continuous_adopt(cid,'prompts',vid)
        self._continuous_cursor(cid,finishingReceipt=dict(plan=self._continuous_art(eid,'plan')['adopted_version_id'],prompts=self._continuous_art(eid,'prompts')['adopted_version_id']))
        return True
