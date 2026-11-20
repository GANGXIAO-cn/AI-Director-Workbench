"""07 image assessments with frozen image bytes and the approved local method."""
import json
import re
from .db import connect, transaction, dumps, new_id
from .errors import Rejected, row
from io import BytesIO
from PIL import Image
from .images import image_attachment
from .worksheet import CATEGORIES

ASSESS_EDGE=1280


def assess_attachment(name,data):
    """实测 136：看图评估只需判断能否用，先把长边缩到 1280 再传，减少上传量与断线。"""
    try:
        with Image.open(BytesIO(data),formats=('PNG','JPEG')) as source:
            if max(source.size)<=ASSESS_EDGE:return image_attachment(name,data)
            image=source.convert('RGB');image.thumbnail((ASSESS_EDGE,ASSESS_EDGE),Image.LANCZOS)
            out=BytesIO();image.save(out,'JPEG',quality=90)
            return image_attachment(name,out.getvalue())
    except (ValueError,OSError,SyntaxError,Image.DecompressionBombError):
        return image_attachment(name,data)
from . import unifiedmethods
from .methodpack import method_entries

CONTRACT='''审核范围仅为当前指定用途：身份图不证明姿态；环境图不证明动作、运行或声音；外观图不证明开场或接触。
07 道具外观基准不承担最终镜头的精确读数：细刻度、指针角度、微小文字和装饰差异写 minor，直接 limited/usable，不升级为关键因果问题，不要求为这些小差异修图。不能因剧情未来有特写就把外观基准升级为最终特写成片。
轻微背景差异默认保留使用，写 minor；limited 是可用结论，不要求升级为完美。遮挡或看不清不等于结构错误，动态未测单列而不据此 blocked。
只有明确身份错配、明确畸形或实质破坏当前用途的结构错误才列 critical，并写明可见位置及实际影响。props 仅记录观察，不是必须绑定的剧情道具清单。
实际打开候选图和角色对照图，按 image_assess.md 评估当前用途。reply 只返回 JSON 数组，每张候选恰一项，按当前用途的优先顺序排列，最好在前。
每项含 versionId、verdict（usable/limited/blocked）、value、fitness、critical、minor、observation、props、recommendRequired（布尔）、reason。
critical 每项含 type（identity/anatomy/usage）、where、text；minor 每项含 where、text。critical 非空时 verdict 必须 blocked。不影响本次用途的轻微差异只写 minor，不升级为 usage。value、fitness、observation、reason 均为非空文字。
props 是道具 ID 到实际可见状态的对象，ID 只用小写字母开头的字母、数字、下划线或连字符。不能确认的内容如实写明，不用提示词代替所见。输入的用户已接受问题仍保留原观察，不伪称修复。'''


COARSE = """按每张图声明的用途和时刻核对：开场图看开场，过程图与九宫格看对应镜头时刻；只看每张图是否是需要的东西、本身是否有明显缺陷（如有角色对照图，核对是否是对应角色，不核对细节完全一致）。单图只看大体主体与机位，除明确承担人物站位的大场景外，不核空间细节。九宫格整图看关键过程，不逐格扣细节。不要对照剧本、不要排名、不检查商品是否在空景。每个版本一项 JSON 数组：versionId、verdict（usable 或 blocked）；只有 blocked 须写 defect（明显缺陷的位置和影响）；usable 时可附简短 observation（画面观察）和 minor（不影响当前用途、可交给后续沿用的轻微差异数组，每项含 where 和 text）。"""


def coarse_results(items,ids):
    if not isinstance(items,list) or len(items)!=len(ids) or any(not isinstance(x,dict) for x in items) or {x.get('versionId') for x in items}!=set(ids):
        raise Rejected('评图须逐一对应送审版本')
    out=[]
    for x in items:
        defect=x.get('defect')
        if x.get('verdict') not in ('usable','blocked') or (x['verdict']=='blocked' and not (isinstance(defect,str) and defect.strip())):
            raise Rejected('明显缺陷须说明具体影响')
        critical=[dict(type='usage',where='整图',text=defect)] if x['verdict']=='blocked' else []
        minor=[]
        for m in (x.get('minor') if isinstance(x.get('minor'),list) else []):
            if isinstance(m,dict) and isinstance(m.get('text'),str) and m['text'].strip():
                minor.append(dict(where=str(m.get('where') or '画面').strip() or '画面',text=m['text'].strip()))
            elif isinstance(m,str) and m.strip():
                minor.append(dict(where='画面',text=m.strip()))
        observation=x.get('observation').strip() if isinstance(x.get('observation'),str) else ''
        out.append(dict(versionId=x['versionId'],verdict=x['verdict'],critical=critical,minor=minor,props={},recommendRequired=False,
                        value='',fitness='',observation=observation,reason=defect if critical else '基础要求符合，无明显缺陷'))
    return out


def validate_assessments(value,ids):
    if not isinstance(value,list) or len(value)!=len(ids):raise Rejected('每张候选须有一条图片评估')
    seen=[]
    for item in value:
        if not isinstance(item,dict):raise Rejected('图片评估须为对象')
        seen.append(item.get('versionId'))
        if item.get('verdict') not in ('usable','limited','blocked'):raise Rejected('图片评估 verdict 无效')
        if any(not isinstance(item.get(k),str) or not item[k].strip() for k in ('value','fitness','observation','reason')):raise Rejected('图片评估须填写画面价值、用途、实际观察和结论')
        if type(item.get('recommendRequired')) is not bool:raise Rejected('recommendRequired 须为布尔值')
        for key in ('critical','minor'):
            issues=item.get(key)
            if not isinstance(issues,list):raise Rejected(key+' 须为数组')
            for issue in issues:
                if not isinstance(issue,dict) or any(not isinstance(issue.get(k),str) or not issue[k].strip() for k in ('where','text')):raise Rejected('图片问题须含位置和原文')
                if key=='critical' and issue.get('type') not in ('identity','anatomy','usage'):raise Rejected('critical.type 无效')
        if item['critical'] and item['verdict']!='blocked':raise Rejected('critical 非空时 verdict 必须为 blocked')
        props=item.get('props')
        if not isinstance(props,dict) or any(not re.fullmatch(r'[a-z][a-z0-9_-]*',k) or not isinstance(v,str) or not v.strip() for k,v in props.items()):raise Rejected('props 须使用有效道具 ID 和可见状态')
    if set(seen)!=set(ids) or len(set(seen))!=len(ids):raise Rejected('图片评估版本编号须与候选逐一对应')
    return value


class ImageAssessmentMixin:
    def image_assessments(self,episode_id,need_key=None):
        with connect(self.db_path) as db:
            self._episode_in(db,episode_id)
            need=row(db,'SELECT id FROM asset_need WHERE episode_id=? AND need_key=?',episode_id,need_key)['id'] if need_key else None
            rows=db.execute('SELECT * FROM image_assessment WHERE episode_id=? AND (? IS NULL OR need_id=?) ORDER BY created_at,rowid',(episode_id,need,need)).fetchall()
        latest={}
        for item in rows:latest[(item['need_id'],item['version_id'])]=dict(item,body=json.loads(item['body']))
        return list(latest.values())

    def prepare_image_assess(self,episode_id,version_ids,command_id,need_key=None,context=None,episode_review=False):
        if not isinstance(version_ids,list) or not version_ids or (not episode_review and len(version_ids)>4) or len(set(version_ids))!=len(version_ids):raise Rejected('每次评估须有 1–4 张不同候选图')
        with transaction(self.db_path) as db:
            def create():
                ep=self._episode_in(db,episode_id);art=row(db,"SELECT * FROM artifact WHERE episode_id=? AND stage='asset_design'",episode_id)
                need=dict(row(db,'SELECT * FROM asset_need WHERE episode_id=? AND need_key=? AND removed=0',episode_id,need_key)) if need_key else None
                if need:
                    info={k:need.get(k) for k in ('title','kind','description','image_prompt','role_id','state_id')}
                    info['sceneIds']=json.loads(need['scene_ids'])
                else:info=dict(context or {})
                purposes=[]
                if episode_review:
                    purposes=[dict(r) for r in db.execute("SELECT id AS needId,title,kind,description,role_id AS roleId,state_id AS stateId,scene_ids AS sceneIds,adopted_version_id AS versionId FROM asset_need WHERE episode_id=? AND removed=0",(episode_id,)) if r['versionId'] in version_ids]
                    grid_uses=(context or {}).get('gridPurposes',[])
                    for use in grid_uses:
                        av=self.asset_version(use['versionId'],db)
                        if av['episode_id']!=episode_id or not av['grid']:raise Rejected('本段九宫格版本不符')
                        purposes.append(dict(needId=None,kind='grid',moment='对应段镜头过程',**use))
                    if {p['versionId'] for p in purposes}!=set(version_ids):
                        raise Rejected('整集检查的素材已变化')
                    choice_details={}
                    for r in db.execute("SELECT c.asset_version_id,c.shot_ids,d.details FROM image_choice c LEFT JOIN required_image_details d ON d.choice_id=c.id WHERE c.episode_id=?",(episode_id,)):
                        if r['asset_version_id'] in version_ids:
                            detail=json.loads(r['details']) if r['details'] else {}
                            choice_details[r['asset_version_id']]=dict(shotIds=json.loads(r['shot_ids'] or '[]'),depictsMoment=detail.get('depictsMoment') or detail.get('moment') or '')
                    for p in purposes:
                        cd=choice_details.get(p['versionId'],{})
                        if cd.get('depictsMoment') and not p.get('moment'):p['moment']=cd['depictsMoment']
                        if cd.get('shotIds'):p['shotIds']=cd['shotIds']
                        if not p.get('moment'):
                            cat=CATEGORIES.get(p.get('kind'),p.get('kind'))
                            p['moment']='对应段镜头过程' if cat=='grid' else '开场基准/对应声明用途'
                    info={'purposes':purposes}
                contract=COARSE if episode_review else CONTRACT
                if episode_review and (context or {}).get("correction"):
                    contract+="\n前轮结果无法读取，请按本契约补交完整结果："+context["correction"]
                if episode_review:
                    purpose_rows=[{k:v for k,v in dict(versionId=p['versionId'],title=p['title'],kind=p.get('kind'),description=p.get('description',''),roleId=p.get('roleId'),stateId=p.get('stateId'),moment=p.get('moment'),shotIds=p.get('shotIds')).items() if v not in (None,'',[])} for p in purposes]
                    entries=[dict(role='request',name='基础用途',text=dumps(purpose_rows)),dict(role='request',name='整集一次评图',text=contract)]
                else:
                    script=self._adopted_content(db,episode_id,'script') or {}
                    scenes=[s for s in script.get('scenes',[]) if not info.get('sceneIds') or s.get('id') in info['sceneIds']]
                    entries=[dict(role='image_assess_context',name='条目用途与场次.json',text=dumps(dict(item=info,context=context or {},scenes=scenes))),dict(role='request',name='评估回包契约',text=contract)]
                for vid in version_ids:
                    asset=self.asset_version(vid,db)
                    if asset['project_id']!=ep['project_id'] or asset['kind']!='image':raise Rejected('只能评估本项目候选图片')
                    entries.append(assess_attachment('候选-'+vid,self.store.read_blob(asset['sha256'])))
                accepted=[dict(r) for vid in version_ids for r in db.execute('SELECT * FROM accepted_image_issue WHERE asset_version_id=?',(vid,))]
                entries.append(dict(role='accepted_image_issue',name='用户已接受的问题清单.json',text=dumps(accepted)))
                matrix=self._cast_matrix(db,episode_id)
                role=info.get('role_id') or info.get('roleId')
                # 实测 136：有角色的条目只附该角色；没有角色的道具、空景、商品不附角色图。
                faceless=not role and CATEGORIES.get(info.get('kind'),info.get('kind')) in ('scene','prop','product')
                if episode_review:
                    review_roles={p.get('roleId') for p in purposes if p.get('roleId')}
                    has_grid=any(p.get('kind')=='grid' for p in purposes)
                    baselines={k:v for k,v in matrix['roster'].items() if v.get('assetVersionId') and v['assetVersionId'] not in version_ids and (has_grid or not review_roles or k in review_roles)}
                else:
                    baselines={} if faceless else {k:v for k,v in matrix['roster'].items() if (not role or k==role) and v.get('assetVersionId')}
                entries.append(dict(role='role_baselines',name='固定角色外观与身份基准.json',text=dumps(baselines)))
                for key,baseline in baselines.items():
                    asset=self.asset_version(baseline['assetVersionId'],db)
                    entries.append(dict(assess_attachment('角色对照-'+key,self.store.read_blob(asset['sha256'])),role='cast_baseline_image'))
                slot = self.episode_expert(episode_id)
                if episode_review:
                    selections={**self._kickoff(db,episode_id)['executor_plan'],**self._stage_executors(db,episode_id,'asset_design')}
                    slot=self.active_selection(selections['review'])
                provider = slot['provider']
                plan = dict(create=slot, review=slot)
                tid,_=self._draft_thread(db,ep['project_id'],episode_id,art['id'],'asset_design',contract,plan)
                # Freeze the approved method even for a project whose earlier pack predates H.
                pack,profile=self._episode_method(db,episode_id)
                if not episode_review and not method_entries(pack,'image_assess',profile):
                    entries=method_entries(unifiedmethods.load(),'image_assess')+entries
                rid=self._queue_run(db,tid,'image_assess',None,contract,provider=provider,entries=entries,discussion_only=True,workspace_revision=art['revision'],extra_header=dict(imageAssessment=dict(coarse=episode_review,observationOnly=bool((context or {}).get('observationOnly')),needId=need['id'] if need else None,versionIds=version_ids,purposes=purposes)))
                return dict(runId=rid,taskId=tid)
            return self._command(db,command_id,'prepare_image_assess',[episode_id,version_ids,need_key,context],create)

    def finish_image_assess(self,run_id):
        with connect(self.db_path) as db:
            run=row(db,'SELECT * FROM run WHERE id=?',run_id)
            if run['purpose']!='image_assess' or run['status']!='succeeded':raise Rejected('看图评估运行尚未成功')
            manifest,_=self.store.load_manifest(run['manifest_relpath'])
        spec=manifest['imageAssessment']
        reply=json.loads((self.store.run_dir(run_id)/'result.json').read_text())['reply']
        if spec.get('coarse') and isinstance(reply,str):
            stripped=reply.strip()
            if stripped.startswith(('```json\n','```\n')) and stripped.endswith('```'):
                reply=stripped.split('\n',1)[1][:-3].strip()
        try:values=(coarse_results if spec.get("coarse") else validate_assessments)(json.loads(reply),spec["versionIds"])
        except ValueError as exc:raise Rejected('图片评估 reply 须为 JSON 数组') from exc
        record_kind = 'observation' if spec.get('observationOnly') else 'independent_review' if spec.get('coarse') else None
        if record_kind:
            values = [dict(item, recordKind=record_kind) for item in values]
        with transaction(self.db_path) as db:
            if not db.execute('SELECT 1 FROM image_assessment WHERE run_id=?',(run_id,)).fetchone():
                for item in values:
                    needs=[p['needId'] for p in spec.get('purposes',[]) if p['versionId']==item['versionId']] or [spec['needId']]
                    for need in needs:
                        db.execute('INSERT INTO image_assessment VALUES(?,?,?,?,?,?,?,?)',(new_id(),manifest['episodeId'],need,item['versionId'],run_id,item['verdict'],dumps(item),self.clock()))
        return values

    def assess_images(self,episode_id,version_ids,command_id,need_key=None,context=None,continuous_id=None):
        for attempt in range(2):
            job=self.prepare_image_assess(episode_id,version_ids,f'{command_id}:format:{attempt}',need_key,context)
            rid=job['runId']
            from .continuous_images import parallel_worker
            if continuous_id and parallel_worker():
                rid=self._continuous_run_direct(continuous_id,rid)
            elif continuous_id:
                self._continuous_execute(continuous_id,rid)
                rid=self._continuous_record(continuous_id)["cursor"]["runId"]
            else:self.execute_run(rid)
            try:return self.finish_image_assess(rid)
            except Rejected as exc:
                with connect(self.db_path) as db:status=row(db,'SELECT status FROM run WHERE id=?',rid)['status']
                if status!='succeeded':raise
                if attempt:
                    if continuous_id and not parallel_worker():self._continuous_problem(continuous_id,'图片评估格式重试后仍不合格：'+str(exc))
                    raise Rejected('图片评估格式重试后仍不合格：'+str(exc)) from exc
                context=dict(context or {},formatRetry=str(exc))
