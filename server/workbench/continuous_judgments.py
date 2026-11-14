"""Small, persisted judgments using the ordinary run/retry machinery."""
import json
from .db import connect, dumps
from .storage import canonical, sha256_bytes
from . import imagechoices


def fingerprint(value):
    return sha256_bytes(canonical(value))


def compact_shots(plan):
    return [{k: s[k] for k in ('id','segment','origin','action','propStates','visibleRoles','camera','continuity') if k in s}
            for s in plan['shots']]


class ContinuousJudgments:
    def _continuous_bounded_json(self, cid, kind, object_id, instruction, materials, validate, stage='plan'):
        value = self._continuous_checkpoint(cid)
        eid = value['episode_id']
        basis = fingerprint(dict(protocol=1, kind=kind, instruction=instruction, materials=materials, stage=stage))
        key = kind + ':' + object_id
        receipts = value['cursor'].get('judgmentReceipts', {})
        receipt = receipts.get(key, {})
        if receipt.get('basisHash') != basis:
            receipt = dict(basisHash=basis, attempts=[], round=value['cursor'].get('formatRound',0))
        if 'result' in receipt:
            try:cached_problems=validate(receipt['result'])
            except (ValueError,TypeError,KeyError,AttributeError) as exc:cached_problems=['缓存业务对象无效：'+str(exc)]
            if not cached_problems:return receipt['result'], basis
            # A result accepted by an older validator is not a current success.
            # Keep attempts/evidence and use the same bounded retry allowance.
            receipt={k:v for k,v in receipt.items() if k!='result'}
        current_round=value['cursor'].get('formatRound',0)
        if 'result' not in receipt and len(receipt['attempts'])==2 and receipt['attempts'][-1].get('problems') and current_round>receipt.get('round',0):
            receipt=dict(basisHash=basis,attempts=[],round=current_round)
        instruction_now = instruction
        for attempt in range(2):
            attempts = receipt['attempts']
            if len(attempts) <= attempt:
                art = self._continuous_art(eid, stage)
                expert = self.episode_expert(eid)
                job = self.create_task(eid, stage, instruction_now, discussion_only=True,
                    workspace_revision=art['revision'], executor=dict(create=expert),
                    bounded_judgment=dict(kind=kind, materials=materials),
                    command_id=f'{cid}:judgment:{key}:{basis}:{receipt.get("round",0)}:{attempt}')
                attempts.append(dict(runId=job['runId']))
                receipts = self._continuous_record(cid)['cursor'].get('judgmentReceipts', {})
                receipts[key] = receipt
                self._continuous_cursor(cid, judgmentReceipts=receipts)
            rid = attempts[attempt]['runId']
            self._continuous_cursor(cid, stage=stage, phase='judgment', judgmentKind=kind)
            self._continuous_execute(cid, rid)
            # Automatic provider switches may have replaced the original run.
            actual = self._continuous_record(cid)['cursor']['runId']
            raw = json.loads((self.store.run_dir(actual)/'result.json').read_text())['reply']
            try:
                answer = json.loads(raw)
                problems = validate(answer)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                answer = None
                problems = ['业务 JSON 无效：' + str(exc)]
            attempts[attempt].update(actualRunId=actual, reply=raw, problems=problems)
            if not problems:
                receipt.update(result=answer, runId=actual)
            receipts = self._continuous_record(cid)['cursor'].get('judgmentReceipts', {})
            receipts[key] = receipt
            self._continuous_cursor(cid, judgmentReceipts=receipts)
            if not problems:
                return answer, basis
            instruction_now = instruction + '\n前次业务回复：\n' + raw + '\n具体校验问题：' + dumps(problems) + '\n只修正上述问题，提交完整业务对象。'
        self._continuous_stop(cid, 'blocked', kind + '：' + '；'.join(problems))

    def _continuous_image_rule(self, eid, vid):
        with connect(self.db_path) as db:
            return next((r for r in imagechoices.requirements(db, eid) if r['versionId'] == vid), None)

    def _continuous_existing_assessment(self, eid, vid):
        values = [r for r in self.image_assessments(eid) if r['version_id'] == vid]
        return values[-1]['body'] if values else None

    def _continuous_bind(self, cid, vid, shots, plan_id, source, basis, reason, expected, need_key=None):
        old = (expected or {}).get('autoBindings', {}).get(source, {})
        if old.get('basisHash') == basis and old.get('shotIds') == shots and set(shots) <= set((expected or {}).get('shotIds', [])):
            return
        self.bind_required_image(self._continuous_record(cid)['episode_id'], vid, shots, plan_id,
            source, basis, reason, expected,
            command_id=f'{cid}:bind:{vid}:{source}:{basis}:{fingerprint(expected)}',need_key=need_key)
        self._continuous_log(cid,'plan','fill_observation',vid,f'已匹配 {len(shots)} 个素材参考镜头',dict(source=source,shotIds=shots,reason=reason,basisHash=basis))

    def _continuous_empty_scene_bindings(self,cid):
        eid=self._continuous_record(cid)['episode_id'];art=self._continuous_art(eid,'plan')
        plan=self._continuous_version(art['adopted_version_id']);shots={s['id']:s for s in plan['shots']}
        matrix=self.cast_matrix(eid);cast={r['segment']:r['cells'] for r in matrix['rows']}
        needs=[n for n in self.asset_worksheet(eid)['rows'] if n.get('kind')=='scene' and n.get('adopted_version_id')]
        with connect(self.db_path) as db:rules=imagechoices.requirements(db,eid)
        pending=[]
        for seg in plan['segments']:
            sid=seg['id'];group=[s for s in shots.values() if s['segment']==sid]
            ids={s['id'] for s in group}
            if seg.get('strategy')=='grid' or any(s.get('startAssetId') or s.get('endAssetId') for s in group):continue
            if any(b.get('status')=='ok' and b.get('assetVersionId') for b in cast.get(sid,{}).values()):continue
            if any(sid in r.get('targetSegments',[]) or ids.intersection(r['shotIds']) for r in rules):continue
            scenes={s.get('origin',{}).get('scene') for s in group}
            candidates=[dict(versionId=n['adopted_version_id'],title=n['title'],description=n['description']) for n in needs if scenes.intersection(n.get('scene_ids',[]))]
            if candidates:pending.append(dict(segment=sid,shots=group,candidates=candidates))
        if not pending:return
        def valid(answer):
            picks=answer.get('selections') if isinstance(answer,dict) else None
            ok=isinstance(picks,list) and len(picks)==len(pending) and {p.get('segment') for p in picks if isinstance(p,dict)}=={p['segment'] for p in pending} and all(isinstance(p,dict) and p.get('versionId') in {c['versionId'] for row in pending if row['segment']==p.get('segment') for c in row['candidates']} and isinstance(p.get('reason'),str) and p['reason'].strip() for p in picks)
            return [] if ok else ['每个待选图段须选择一张候选场景图并说明用途']
        answer,basis=self._continuous_bounded_json(cid,'空镜场景选图',','.join(p['segment'] for p in pending),'为没有角色身份图或镜头绑定图的生成段，从已采用场景图候选中选一张最适合作为环境参考的图。只选图，不审核或重做图片。reply {"selections":[{"segment":"段号","versionId":"候选版本","reason":"用途"}]}。',dict(segments=pending),valid)
        for pick in answer['selections']:
            with connect(self.db_path) as db:expected=next((r for r in imagechoices.requirements(db,eid) if r['versionId']==pick['versionId']),None)
            ids=[s['id'] for s in shots.values() if s['segment']==pick['segment']]
            prior=(expected or {}).get('autoBindings',{}).get('scene',{}).get('shotIds',[])
            self._continuous_bind(cid,pick['versionId'],list(dict.fromkeys([*prior,*ids])),art['adopted_version_id'],'scene',basis,pick['reason'],expected)

    def _continuous_required_bindings(self, cid):
        eid = self._continuous_record(cid)['episode_id']
        art = self._continuous_art(eid, 'plan'); plan_id = art['adopted_version_id']
        plan = self._continuous_version(plan_id)
        shots = compact_shots(plan); byid = {s['id']: s for s in shots}
        matrix = self.cast_matrix(eid)
        identities = {v['assetVersionId'] for v in matrix['roster'].values() if v.get('assetVersionId')}
        identities.update(b.get('assetVersionId') for r in matrix['rows'] for b in r['cells'].values() if b.get('status') == 'ok')
        needs = {r.get('adopted_version_id'): r for r in self.asset_worksheet(eid)['rows'] if r.get('adopted_version_id')}
        with connect(self.db_path) as db: rules = imagechoices.requirements(db, eid)
        pending = []; expected = {}; cached = []
        saved = self._continuous_record(cid)['cursor'].get('materialJudgments', {})
        for rule in rules:
            vid = rule['versionId']; asset = self.asset_version(vid)
            # Cast creates exact identity scope; combined boards retain their existing
            # extra scope for the observation/validate_scope path, never widen it here.
            if vid in identities or asset.get('category') == 'character':continue
            if rule.get('selectedBy') != 'agent' and not rule.get('autoBindings'):continue
            if asset.get('grid'):continue
            need = needs.get(vid, {})
            known = rule.get('autoBindings', {}).get('material', {})
            existing = set(rule['shotIds'])
            if existing and existing <= byid.keys() and not known:continue
            candidates = shots
            details = {k: need.get(k) for k in ('title','kind','description','image_prompt','role_id','state_id','scene_ids')}
            scene_ids = need.get('scene_ids', [])
            if scene_ids:
                candidates = [s for s in candidates if s.get('origin', {}).get('scene') in scene_ids]
            # Director segment scope limits candidates; AI-derived scope does not.
            auto_segments = {s for a in rule.get('autoBindings', {}).values() for s in a.get('addedSegments', [])}
            director_targets = set(rule.get('targetSegments', [])) - auto_segments
            if director_targets:
                candidates = [s for s in candidates if s['segment'] in director_targets]
            item = dict(needKey=need.get('need_key') or vid, versionId=vid,
                purpose=details, reason=rule['reason'], limitations=rule['limitations'],
                assessment=self._continuous_existing_assessment(eid,vid), candidateShotIds=[s['id'] for s in candidates])
            item_basis = fingerprint(dict(image=item, shots=candidates, protocol=1))
            if known.get('basisHash') == item_basis and set(known.get('shotIds', [])) <= existing and existing <= byid.keys():continue
            item['basisHash'] = item_basis
            expected[vid] = rule
            if saved.get(vid, {}).get('basisHash') == item_basis:
                cached.append((saved[vid]['result'],item_basis))
            else:pending.append(item)
        for item,item_basis in cached:
            if item['shotIds']:
                self._continuous_bind(cid,item['versionId'],item['shotIds'],plan_id,'material',item_basis,item['reason'],expected[item['versionId']],need_key=needs.get(item['versionId'],{}).get('need_key'))
            else:self._continuous_stop(cid,'blocked',item['needKey']+'：'+item['unresolvedReason'])
        if not pending:return
        self._continuous_cursor(cid, phase='judgment', judgmentKind='素材用途匹配')
        material = dict(images=pending, shots=[s for s in shots if any(s['id'] in p['candidateShotIds'] for p in pending)])
        def validate(answer):
            if not isinstance(answer,dict) or not isinstance(answer.get('bindings'),list):return ['bindings 须为数组']
            items = answer['bindings']; keys = [(p['needKey'],p['versionId']) for p in pending]
            if len(items)!=len(keys) or any(not isinstance(i,dict) for i in items):return ['每张素材须有且只有一个结果']
            if sorted((i.get('needKey',''),i.get('versionId','')) for i in items)!=sorted(keys):return ['素材编号或版本缺失、重复或不属于请求']
            errors=[]
            for item in items:
                src=next(p for p in pending if p['versionId']==item['versionId'])
                chosen=item.get('shotIds')
                if not isinstance(chosen,list) or any(not isinstance(s,str) for s in chosen) or len(set(chosen))!=len(chosen) or set(chosen)-set(src['candidateShotIds']):errors.append(item['needKey']+'：镜头编号无效')
                elif not chosen and (not isinstance(item.get('unresolvedReason'),str) or not item['unresolvedReason'].strip()):errors.append(item['needKey']+'：未匹配须说明原因')
                elif chosen and (not isinstance(item.get('reason'),str) or not item['reason'].strip()):errors.append(item['needKey']+'：缺用途依据')
            return errors
        instruction='为每张素材选择适合作为生成参考的候选镜头。场次只提供候选，不证明每镜可见；propStates 不单独证明可见。不改变镜头，不编造看图证据。reply 内业务对象 {"bindings":[{"needKey":"原编号","versionId":"原版本","shotIds":["候选镜头"],"reason":"依据","unresolvedReason":"无匹配时说明"}]}。'
        answer,basis=self._continuous_bounded_json(cid,'素材用途匹配',','.join(p['versionId'] for p in pending),instruction,material,validate)
        saved=self._continuous_record(cid)['cursor'].get('materialJudgments', {})
        for item in answer['bindings']:
            saved[item['versionId']]=dict(basisHash=next(p['basisHash'] for p in pending if p['versionId']==item['versionId']),result=item)
        self._continuous_cursor(cid,materialJudgments=saved)
        self._continuous_checkpoint(cid)
        current_needs={n['need_key']:n.get('adopted_version_id') for n in self.asset_worksheet(eid)['rows']}
        if any(p['needKey'] in current_needs and current_needs[p['needKey']]!=p['versionId'] for p in pending):
            from .continuous import UserChange
            raise UserChange()
        unresolved=[]
        for item in answer['bindings']:
            if not item['shotIds']:
                unresolved.append(item['needKey']+'：'+item['unresolvedReason']);continue
            self._continuous_bind(cid,item['versionId'],item['shotIds'],plan_id,'material',next(p['basisHash'] for p in pending if p['versionId']==item['versionId']),item['reason'],expected[item['versionId']],need_key=needs.get(item['versionId'],{}).get('need_key'))
        if unresolved:self._continuous_stop(cid,'blocked','素材用途未匹配：'+'；'.join(unresolved))

    def _continuous_reference_props(self, cid, sid, ref, shots, assessed, identity_roles=(), prompt_text="", usage_conclusion=None):
        """Align observation identifiers, keeping raw evidence and real state conflicts."""
        props = assessed['props']
        states = {}
        for shot in shots:
            for key,value in shot.get('propStates',{}).items():states.setdefault(key,set()).add(value)
        used = {key for shot in shots if shot['id'] in ref.get('shots',[]) for key in shot.get('propStates',{})}
        expected = {s['id'] for s in shots if set(s.get('visibleRoles',[])) & set(identity_roles)}
        extra = set(ref.get('shots',[]))-expected if identity_roles else set()
        if not extra and all(k in used and states.get(k)=={v} for k,v in props.items()):
            return props, False, None
        instruction=('本步仅整理观察编号与当前引用范围，继承 usageConclusion 已通过的用途结论，不重新评选图片或要求换图。'
            '每个原 props 必须保留一个 mapping：source、target、mode、reason；原始观察不会被删除。'
            'target 只能选择 usedPropIds 中不重复的编号；编号不同但确为同一物体时映射到已有编号，不得冒用别的物体编号。'
            'state 保留原观察状态；appearance 仅在本次只借外形颜色材质且观察支持时使用。'
            'background 表示本次不承担道具状态参考，并非物理背景或删除物体；target 必须为空字符串。'
            '身份图附带饰物、服饰配件、包袋或环境物，若当前正文及作用镜头未借用其动作或状态，可用 background，保留观察及用途限制。'
            '不能仅因缺少登记编号就忽略物体。若当前正文或作用镜头确实借用该物体且没有可映射编号，使用 unresolved，target 为空，'
            '另填 shotId 和 quote（该镜头 action 或当前 promptText 中的逐字原句），reason 说明缺失关系；不要伪造编号或反复提交无效 state。'
            'reply 业务对象 {"mappings":[{"source":"原编号","target":"方案编号","mode":"state","reason":"具体证据"}],'
            '"combined":false,"otherUses":{}}。身份与道具组合图的额外镜头须逐镜用 otherUses 映射到方案道具编号，combined=true；普通图片 otherUses 留空。')
        materials=dict(segment=sid, reference={k:ref[k] for k in ('assetVersionId','shots','role') if k in ref}, assessment=assessed, shots=compact_shots(dict(shots=shots)),
                       identityRoles=list(identity_roles), extraShotIds=sorted(extra), usedPropIds=sorted(used),
                       promptText=prompt_text, usageConclusion=usage_conclusion)
        def validate(answer):
            if not isinstance(answer,dict) or not isinstance(answer.get('mappings'),list):return ['mappings 须为数组']
            mappings=answer['mappings']
            if any(not isinstance(m,dict) for m in mappings):return ['mapping 须为对象']
            if sorted(m.get('source','') for m in mappings)!=sorted(props):return ['每个观察道具须有且只有一个映射']
            errors=[];targets=set()
            for m in mappings:
                key=m.get('target');mode=m.get('mode');source=m['source']
                if mode not in ('state','background','appearance','unresolved') or not isinstance(m.get('reason'),str) or not m['reason'].strip():errors.append(source+'：缺用途判断和依据');continue
                if mode=='unresolved':
                    shot=next((s for s in shots if s['id']==m.get('shotId') and s['id'] in ref.get('shots',[])),None)
                    quote=m.get('quote')
                    if key!='' or not shot or not isinstance(quote,str) or not quote.strip() or not (quote in prompt_text or quote in shot.get('action','')):
                        errors.append(source+'：未匹配用途须提供作用镜头 shotId、正文或该镜头 action 的逐字 quote，target 为空')
                elif mode=='background':
                    if key!='' or source in used:errors.append(source+'：不能将当前镜头使用的道具隐藏为背景')
                elif key not in used or key in targets:errors.append(source+'：target 须为 usedPropIds 中不重复的编号；仅不承担本段状态参考的附带物用 background 且 target 为空；真实缺失映射用 unresolved 并引用镜头原句')
                else:targets.add(key)
                if source in used and key!=source:errors.append(source+'：已有精确编号不可改成其他道具')
            other=answer.get('otherUses')
            if type(answer.get('combined')) is not bool or not isinstance(other,dict):errors.append('combined/otherUses 格式无效')
            elif set(other)!=extra or any(v not in targets or v not in next(s for s in shots if s['id']==k).get('propStates',{}) for k,v in other.items() if k in extra):errors.append('组合图额外用途须逐镜对应图内与该镜头道具')
            elif extra and not answer['combined']:errors.append('额外身份用途须有组合图证据')
            return errors
        answer,basis=self._continuous_bounded_json(cid,'引用用途核对',sid+':'+assessed['versionId'],instruction,materials,validate,stage='prompts')
        unresolved=[m for m in answer['mappings'] if m['mode']=='unresolved']
        if unresolved:
            self._continuous_stop(cid,'blocked',sid+' 的引用道具缺少对应关系：'+'；'.join(m['source']+' / '+m['shotId']+'：'+m['reason']+'；原句：'+m['quote'] for m in unresolved))
        aligned={m['target']:('仅外观' if m['mode']=='appearance' else props[m['source']]) for m in answer['mappings'] if m['mode']!='background'}
        self._continuous_log(cid,'prompts','fill_observation',assessed['versionId'],'保留原始观察，核对道具编号与参考用途',dict(segment=sid,basisHash=basis,mappings=answer['mappings']))
        return aligned,answer['combined'],answer['otherUses'] or None

    def _continuous_reference_gaps(self, eid, content):
        """A finishing receipt cannot hide newly changed required scope."""
        with connect(self.db_path) as db:
            rules=imagechoices.requirements(db,eid)
            plan=self._adopted_content(db,eid,'plan') or {}
        byid={s['id']:s['segment'] for s in plan.get('shots',[])}
        gaps={}
        for rule in rules:
            required={byid[s] for s in rule['shotIds'] if s in byid} | set(rule['targetSegments'])
            for sid in required:
                refs=content.get('segments',{}).get(sid,{}).get('refs',[])
                matching=[]
                for ref in refs:
                    if ref.get('kind')=='image' and self.asset_version(ref['assetVersionId'])['sha256']==rule['sha256']:matching.append(ref)
                expected={s for s in rule['shotIds'] if byid.get(s)==sid}
                if not matching or (expected and not any(expected<=set(r.get('shots',[])) for r in matching)):
                    gaps.setdefault(sid,[]).append(dict(versionId=rule['versionId'],name=rule['name'],shotIds=sorted(expected),reason=rule['reason']))
        return gaps
