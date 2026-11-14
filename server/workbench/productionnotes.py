"""Version-bound production-note assignments for continuous 07–09 adoption."""
import json

STAGES=('asset_design','acting','plan','prompts')

def handoff_value(value):
    """Ignore typographic quotation changes, retaining all words and identifiers."""
    if isinstance(value,str):return value.translate(str.maketrans({'“':'"','”':'"','‘':'"','’':'"'}))
    if isinstance(value,list):return [handoff_value(v) for v in value]
    if isinstance(value,dict):return {k:handoff_value(v) for k,v in value.items()}
    return value

def same_handoff(before,after):
    return all(handoff_value(before.get(k))==handoff_value(after.get(k)) for k in ('productionNotes','supplements'))
CONTRACT='''productionNoteResponses 为数组。每项 id、reportVersionId、owner、status。引用当前采用的06报告版本与提醒编号。07必须覆盖全部提醒：本阶段落实时 owner=asset_design,status=applied,whereIds=本稿条目编号数组；交给后续阶段则 status=assigned,owner=plan/prompts,reason=非空理由。后续阶段只回应本阶段负责的提醒：落实用 applied 和本稿场次/镜头/段号数组 whereIds，reason 写具体落实说明；不能落实用 reassign，owner只能为更后阶段并写 reason。prompts 仅负责文字表达、素材标签与组装要求，不能接收素材质量、空间制作或动作可行性事项。负责人依实际职责确定，不能为消除缺项不断向后转交。prompts 不可再转交。不得借制作提醒改动已采用剧情。'''


def context(db,eid):
    report=db.execute("SELECT v.id,v.content FROM artifact a JOIN version v ON v.id=a.adopted_version_id WHERE a.episode_id=? AND a.stage='stress'",(eid,)).fetchone()
    notes=json.loads(report['content']).get('productionNotes',[]) if report else []
    stages={}
    for stage in STAGES:
        value=db.execute('SELECT v.content FROM artifact a JOIN version v ON v.id=a.adopted_version_id WHERE a.episode_id=? AND a.stage=?',(eid,stage)).fetchone()
        stages[stage]=json.loads(value[0]) if value else {}
    equivalent=[]
    if report:
        equivalent=[r['id'] for r in db.execute('SELECT id,content FROM version WHERE artifact_id=(SELECT artifact_id FROM version WHERE id=?)',(report['id'],))
                    if handoff_value(json.loads(r['content']).get('productionNotes',[]))==handoff_value(notes)]
    return dict(reportVersionId=report['id'] if report else None,reportVersionIds=equivalent,notes=notes,stages=stages)


def identifiers(stage,content):
    field,key={'asset_design':('assets','id'),'acting':('scenes','sceneId'),'plan':('shots','id'),'prompts':('segments',None)}[stage]
    values=content.get(field,{})
    result=set(values) if key is None and isinstance(values,dict) else {v[key] for v in values if isinstance(v,dict) and isinstance(v.get(key),str)} if isinstance(values,list) else set()
    if stage=='plan':result.update(s['id'] for s in content.get('segments',[]) if isinstance(s,dict) and isinstance(s.get('id'),str))
    return result


def normalize_where_ids(item):
    values=item.get('whereIds')
    if values is None and 'whereIds' not in item:
        values=[item['where']] if isinstance(item.get('where'),str) else []
    if not isinstance(values,list) or any(not isinstance(v,str) or not v.strip() for v in values):
        raise ValueError('whereIds 须为编号数组')
    if 'where' in item and values != [item['where']]:raise ValueError('where 与 whereIds 冲突')
    if not values:raise ValueError('whereIds 不能为空')
    if len(set(values))!=len(values):raise ValueError('whereIds 存在重复编号')
    return values


def coverage_status(stage,content,basis,coverage=False):
    notes={n['id']:n for n in basis['notes']};report=basis['reportVersionId']
    reports=set(basis.get('reportVersionIds',[report]))
    owners={key:'asset_design' for key in notes};applied=set();problems=[]
    def error(key,code,text):problems.append(dict(id=key,code=code,message=text))
    def locations(item,source_stage,source):
        values=normalize_where_ids(item)
        existing=identifiers(source_stage,source)
        valid=[value for value in values if value in existing]
        if not valid:raise ValueError('whereIds 含不存在的编号：'+'、'.join(sorted(set(values)-existing)))
        return valid
    for prior in STAGES[:STAGES.index(stage)]:
        source=basis['stages'].get(prior,{})
        for item in source.get('productionNoteResponses',[]):
            if not isinstance(item,dict) or item.get('reportVersionId') not in reports or item.get('id') not in notes:continue
            key=item['id'];owners[key]='plan' if item.get('owner')=='acting' else item.get('owner')
            if item.get('status')=='applied' and item.get('owner')==prior:
                try:locations(item,prior,source)
                except ValueError:continue
                applied.add(key)
    required=set(notes) if stage=='asset_design' else {key for key,owner in owners.items() if owner==stage and key not in applied}
    responses=content.get('productionNoteResponses',[])
    if not isinstance(responses,list):
        error(None,'responses_type','productionNoteResponses 须为数组');responses=[]
    seen=set()
    for item in responses:
        if not isinstance(item,dict):error(None,'response_type','制作提醒回应须为对象');continue
        key=item.get('id');owner=item.get('owner');status=item.get('status')
        if owner=='acting' and stage!='acting':owner='plan'
        if key not in notes or item.get('reportVersionId') not in reports:
            error(key,'source','制作提醒回应须引用当前06采用报告中的编号');continue
        if key in seen:error(key,'duplicate','制作提醒 '+key+' 回应重复')
        seen.add(key)
        if owner not in STAGES:error(key,'owner','制作提醒 '+key+' 负责阶段无效');continue
        if status=='applied':
            valid=True
            if owner!=stage:
                error(key,'owner','制作提醒 '+key+' 状态为 applied 时 owner 须为本阶段 '+stage);valid=False
            try:locations(item,stage,content)
            except ValueError as exc:error(key,'location','制作提醒 '+key+' 的 '+str(exc));valid=False
            if valid:applied.add(key)
        elif status==('assigned' if stage=='asset_design' else 'reassign'):
            if STAGES.index(owner)<=STAGES.index(stage) or not isinstance(item.get('reason'),str) or not item['reason'].strip():
                error(key,'assignment','制作提醒 '+key+' 转交须指定更后阶段及非空理由')
            else:owners[key]=owner
        else:error(key,'status','制作提醒 '+key+' 回应状态无效')
    for key in sorted(required-seen):error(key,'missing_response','制作提醒 '+key+' 尚未回应（负责阶段：'+stage+'）')
    missing=set(notes)-applied
    if coverage:
        for key in sorted(missing):error(key,'unapplied','制作提醒 '+key+' 尚未落实（负责阶段：'+str(owners[key])+'）')
    return dict(owners=owners,applied=sorted(applied),missing=sorted(missing),problems=problems)


def check(stage,content,basis,coverage=False):
    if stage not in STAGES:return []
    return [p['message'] for p in coverage_status(stage,content,basis,coverage)['problems']]


def model_context(db, eid):
    """Model projection; the full context remains available to coverage checks."""
    basis = context(db, eid)
    assignments = []
    for stage, content in basis['stages'].items():
        assignments.extend(dict(stage=stage, **{k: v for k, v in item.items()
                           if k in ('id', 'reportVersionId', 'owner', 'status', 'where', 'whereIds', 'reason')})
                           for item in content.get('productionNoteResponses', []) if isinstance(item, dict))
    for item in assignments:
        if item.get('status')=='applied':
            try:item['whereIds']=normalize_where_ids(item)
            except ValueError:pass  # Keep malformed historical evidence for targeted repair.
    return dict(reportVersionId=basis['reportVersionId'], notes=basis['notes'], assignments=assignments)
