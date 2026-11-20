"""Candidate-bound note repairs. Record patches never rewrite segment prose."""
import json
import re
from .db import connect, transaction, dumps
from .errors import row, Conflict
from . import productionnotes as pn
from .continuous_judgments import fingerprint


def note_proof(content,basis):
    # Evidence concerns prose and assignments, not observation IDs, management
    # notes, or the adopted copy of this same prompt.
    return fingerprint(dict(segments={k:v.get('txt','') for k,v in content.get('segments',{}).items()},
        responses=content.get('productionNoteResponses',[]),report=basis['reportVersionId'],notes=basis['notes'],
        upstream={s:basis['stages'].get(s,{}) for s in pn.STAGES if s!='prompts'}))


def retained_writer_notes(db, cursor):
    """Recognize the retired merge that put old notes back over a writer's correction."""
    vid = cursor.get('promptCandidate')
    if vid not in cursor.get('ownedVersions',[]):
        return None
    row = db.execute("""SELECT v.content,p.id,p.content AS written,p.source_run_id,g.content AS original
        FROM version v JOIN version p ON p.id=v.base_id JOIN version g ON g.id=p.base_id
        JOIN artifact a ON a.id=v.artifact_id WHERE v.id=? AND v.origin='manual' AND a.stage='prompts'
        AND v.artifact_id=p.artifact_id AND p.artifact_id=g.artifact_id""",(vid,)).fetchone()
    if not row or row['source_run_id'] not in cursor.get('ownedRuns',[]):
        return None
    merged, written, original = (json.loads(row[key]) for key in ('content','written','original'))
    before, after = merged.pop('notes',None), written.pop('notes',None)
    if merged!=written or before!=original.get('notes') or not before or not after or before==after:
        return None
    issues = db.execute("""SELECT i.loc FROM issue i JOIN review r ON r.id=i.review_id
        WHERE r.id=(SELECT id FROM review WHERE version_id=? AND pass='prompts' AND scope=''
        ORDER BY rowid DESC LIMIT 1) AND i.blocking=1""",(vid,)).fetchall()
    locations = [json.loads(i[0]) for i in issues]
    if not locations or not all(re.search(r'(?<![A-Za-z_])notes(?![A-Za-z_])',loc.get('anchor',''),re.I) for loc in locations):
        return None
    affected = {loc.get('objectId') for loc in locations}
    from .segmentwriting import saved_note_blocks
    blocks = saved_note_blocks(db,vid,before)
    if set(blocks)!=set(merged.get('segments',{})) or not affected<=blocks.keys():
        return None
    if any(after.count(block)!=1 for sid,block in blocks.items() if sid not in affected):
        return None
    return row['id']


class PromptRepairs:
    def _set_prompt_candidate(self,cid,vid):
        # Cursor movement does not change version ownership or user-edit flags.
        self._continuous_cursor(cid,promptCandidate=vid)
        return vid

    def _resume_prompt_candidate(self,cid,vid):
        if not vid:return vid
        cursor=self._continuous_record(cid)['cursor']
        with connect(self.db_path) as db:
            versions=db.execute('SELECT id,base_id,source_run_id FROM version WHERE artifact_id=(SELECT artifact_id FROM version WHERE id=?) ORDER BY display_no',(vid,)).fetchall()
        for v in versions:
            if v['base_id']==vid and v['id'] not in cursor.get('ignoredVersions',[]) and (
                    v['source_run_id'] in cursor['ownedRuns'] or v['id'] in cursor.get('ownedVersions',[])):
                vid=v['id']
        return self._set_prompt_candidate(cid,vid)

    def _patch_prompt_notes(self,cid,vid,patches,basis):
        self._continuous_checkpoint(cid)
        eid=self._continuous_record(cid)['episode_id']
        with transaction(self.db_path) as db:
            if pn.context(db,eid)!=basis:raise Conflict('制作提醒依据已变化，请按当前版本继续')
            cursor=json.loads(row(db,'SELECT cursor FROM continuous_run WHERE id=?',cid)['cursor'])
            if cursor.get('promptCandidate')!=vid:raise Conflict('提示词候选已变化')
            version=row(db,'SELECT * FROM version WHERE id=?',vid)
            content=json.loads(version['content']);ids={p['id'] for p in patches}
            content['productionNoteResponses']=[p for p in content.get('productionNoteResponses',[]) if p.get('id') not in ids]+patches
            result=self._new_version(db,version['artifact_id'],vid,'manual',content,note='局部修正制作提醒落实记录',baseline_from=vid)
            cursor['ownedVersions']=list(dict.fromkeys([*cursor.get('ownedVersions',[]),result]))
            cursor['promptCandidate']=result
            db.execute('UPDATE continuous_run SET cursor=? WHERE id=?',(dumps(cursor),cid))
        self._continuous_internal_version(cid,result)
        return result

    def _repair_prompt_notes(self,cid,vid,allow_revision=True):
        eid=self._continuous_record(cid)['episode_id']
        for attempt in range(2):
            self._set_prompt_candidate(cid,vid)
            content=self._continuous_version(vid)
            with connect(self.db_path) as db:basis=pn.context(db,eid)
            responses = content.get('productionNoteResponses', [])
            merged = pn.merge_segment_responses(responses) if isinstance(responses, list) else responses
            if merged != responses:
                vid = self._patch_prompt_notes(cid, vid, merged, basis)
                content = self._continuous_version(vid)
            state=pn.coverage_status('prompts',content,basis,coverage=False)
            proof=note_proof(content,basis)
            if not state['problems']:return vid
            # Upstream assignments/design use the existing owning-stage flow.
            targets={state['owners'][key] for key in state['missing']}
            if any(t!='prompts' for t in targets):return vid
            if attempt:self._continuous_problem(cid,'；'.join(p['message'] for p in state['problems']))
            keys={p['id'] for p in state['problems'] if p['id']}
            keys.update(key for key,owner in state['owners'].items() if owner=='prompts')
            if not keys and not state['problems']:return vid
            if not keys:self._continuous_problem(cid,'制作提醒记录结构无效，须修正 productionNoteResponses')
            notes=[n for n in basis['notes'] if n['id'] in keys]
            segments=content['segments'];valid=set(segments)
            material=dict(candidateVersion=vid,reportVersionId=basis['reportVersionId'],notes=notes,
                          responses=content.get('productionNoteResponses',[]),segments={sid:e.get('txt','') for sid,e in segments.items()},validIds=sorted(valid))
            instruction=('核对制作提醒在当前提示词文字层是否落实，不把未来视频验收当作本阶段未完成。'
                'reply 业务对象 {"resolutions":[{"id":"提醒编号","mode":"record_only|revise_segments|upstream_issue",'
                '"whereIds":["段号"],"evidence":[{"segmentId":"段号","quote":"当前正文准确原句"}],'
                '"reason":"落实或缺失依据","missingSegments":[],"revisionInstruction":"", "owner":"prompts"}]}。'
                '每条提醒一个结果。record_only 必须根据正文证据确认内容成立，不仅确认编号存在；每个 whereIds 段提供准确引文。'
                '正文缺失用 revise_segments，写具体缺失及修改段；上游矛盾用 upstream_issue 指明负责阶段，不擅自编造完成。')
            def validate(answer):
                values=answer.get('resolutions') if isinstance(answer,dict) else None
                if not isinstance(values,list) or any(not isinstance(v,dict) for v in values):return ['resolutions 须为对象数组']
                if sorted(v.get('id','') for v in values)!=sorted(keys):return ['提醒须逐一对应，不得遗漏或重复']
                errors=[]
                for v in values:
                    key=v['id'];mode=v.get('mode')
                    if mode not in ('record_only','revise_segments','upstream_issue'):errors.append(key+' mode 无效');continue
                    if not isinstance(v.get('reason'),str) or not v['reason'].strip():errors.append(key+' 缺少具体依据')
                    if mode=='record_only':
                        try:ids=pn.normalize_where_ids(v)
                        except ValueError as exc:errors.append(key+' '+str(exc));continue
                        if set(ids)-valid:errors.append(key+' whereIds 不属于候选稿')
                        evidence=v.get('evidence')
                        if not isinstance(evidence,list) or any(not isinstance(e,dict) for e in evidence):errors.append(key+' 缺少正文证据');continue
                        cited=set()
                        for e in evidence:
                            sid=e.get('segmentId');quote=e.get('quote')
                            if sid not in ids or not isinstance(quote,str) or not quote.strip() or quote not in segments.get(sid,{}).get('txt',''):errors.append(key+' 引文不属于对应段正文')
                            else:cited.add(sid)
                        if cited!=set(ids):errors.append(key+' 每个落实段须提供正文证据')
                    elif mode=='revise_segments':
                        ids=v.get('missingSegments')
                        if not isinstance(ids,list) or not ids or any(not isinstance(s,str) or s not in valid for s in ids):errors.append(key+' missingSegments 无效')
                        if not isinstance(v.get('revisionInstruction'),str) or not v['revisionInstruction'].strip():errors.append(key+' 缺修订指令')
                    elif v.get('owner') not in ('asset_design','plan'):errors.append(key+' 上游负责阶段无效')
                return errors
            answer,_=self._continuous_bounded_json(cid,'制作提醒核对',vid,instruction,material,validate,stage='prompts')
            patches=[];affected=set();instructions=[];upstream=[]
            for v in answer['resolutions']:
                if v['mode']=='record_only':patches.append(dict(id=v['id'],reportVersionId=basis['reportVersionId'],owner='prompts',status='applied',whereIds=pn.normalize_where_ids(v),reason=v['reason'],evidence=v['evidence']))
                elif v['mode']=='revise_segments':affected.update(v['missingSegments']);instructions.append(v['id']+'：'+v['revisionInstruction'])
                else:upstream.append(v)
            if patches:vid=self._patch_prompt_notes(cid,vid,patches,basis)
            if upstream:
                self._continuous_cursor(cid,upstreamNoteRepair=upstream)
                return vid
            if affected:
                if not allow_revision:self._continuous_problem(cid,'制作提醒正文返修后仍未落实：'+'；'.join(instructions))
                vid=self._continuous_prompt_revision(cid,vid,affected,'；'.join(instructions))
                # A second bounded check sees the NEW prose; no pre-emptive applied.
                return self._repair_prompt_notes(cid,vid,allow_revision=False)
            updated=self._continuous_version(vid)
            if not pn.check('prompts',updated,basis,coverage=True):
                self._continuous_cursor(cid,promptNoteVerification=note_proof(updated,basis))
                return vid
        return vid

