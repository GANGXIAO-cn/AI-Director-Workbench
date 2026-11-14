"""Read prior material conclusions without turning adoption into blanket approval."""
import json

RULE = ('本阶段只继承已采用素材的用途与限制，不重新批准图片质量。正文出现动作、道具或声音，不代表每张参考图承担该状态依据。'
        '前阶段已采用的素材及已确定用途是本阶段依据，不重新做图片质量选优。'
        '身份、外观参考不等于服装、动作或首帧参考；按用途读取评估中的限制。'
        '若当前引用和正文已落实限制，同一差异只保留说明，不再次列为冲突或要求换图。'
        '仅报告当前新增的错引、越界用途或与明确要求的矛盾，指出具体镜头、原句及改变的依据。'
        '不得仅因上一阶段保留了缺点就推翻采用结论，不要求参考图预先演出后续动作。')


def identity_context(db,eid,vid):
    baselines=[dict(r) for r in db.execute('SELECT role,appearance FROM episode_role_baseline WHERE episode_id=? AND asset_version_id=?',(eid,vid))]
    fixed=[r[0] for r in db.execute('SELECT role FROM fixed_role_asset WHERE episode_id=? AND asset_version_id=?',(eid,vid))]
    return dict(roleBaselines=baselines,fixedRoles=fixed)


def material_context(db,eid,refs):
    result=[]
    for ref in refs:
        vid=ref['assetVersionId']
        assessment=db.execute('SELECT id,body FROM image_assessment WHERE episode_id=? AND version_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(eid,vid)).fetchone()
        marks=[r[0] for r in db.execute('SELECT mark FROM image_choice WHERE episode_id=? AND asset_version_id=?',(eid,vid))]
        adopted=[dict(r) for r in db.execute('SELECT need_key,source_version_id,role_id,state_id,scene_ids,description FROM asset_need WHERE episode_id=? AND adopted_version_id=? AND removed=0',(eid,vid))]
        for item in adopted:item['scene_ids']=json.loads(item['scene_ids'])
        result.append(dict(adoptedUses=adopted,assetVersionId=vid,reference={k:v for k,v in ref.items() if k!='visualReviewId'},
                           marks=marks,binding=binding_context(ref,json.loads(assessment['body']) if assessment else None),identity=identity_context(db,eid,vid),assessmentId=assessment['id'] if assessment else None,assessment=json.loads(assessment['body']) if assessment else {}))
    return result


def usage_clearances(db,eid,segment,entry):
    """Previously checked SAME use; retain known limitations rather than erase them."""
    rows=material_context(db,eid,entry.get('refs',[]));by={r['assetVersionId']:r for r in rows};out=[]
    for ref in entry.get('refs',[]):
        source=by.get(ref['assetVersionId'])
        if not source or source['assessment'].get('critical'):continue
        observation=db.execute('SELECT details FROM shot_image_observation WHERE id=? AND episode_id=? AND segment=? AND asset_version_id=?',
            (ref.get('visualReviewId'),eid,segment,ref['assetVersionId'])).fetchone()
        if not observation or json.loads(observation[0]).get('conflicts')!=[]:continue
        for issue in source['assessment'].get('minor',[]):
            if isinstance(issue,dict) and issue.get('text'):
                out.append(dict(assetVersionId=ref['assetVersionId'],assessmentId=source['assessmentId'],
                    observationId=ref['visualReviewId'],sourceIssue=issue['text'],reference=source['reference'],
                    fitness=source['assessment'].get('fitness'),reason=source['assessment'].get('reason')))
    return out


def binding_context(ref, assessment=None):
    """Lossless compatibility projection: no semantic guessing of old role text."""
    return dict(assetVersionId=ref['assetVersionId'],
                role=ref.get('role',''),shots=ref.get('shots',[]),
                usage=ref.get('usage','legacy'),subjectIds=ref.get('subjectIds',[]),
                limitations=ref.get('limitations',[]),origin=ref.get('origin','inherited_binding'),
                observation=(assessment or {}).get('observation'),
                fitness=(assessment or {}).get('fitness'))
