"""Offline fixture for continuous UI acceptance; uses the real orchestration."""
from workbench.executors.fake import directed,review,draft
import json


def prepare(env):
    pid,first=env.new_episode(name='连续制作验收',code='E01')
    _,second=env.new_episode(name='连续制作候选验收',code='E02')
    _,third=env.new_episode(name='连续制作单候选验收',code='E03')
    from workbench.workflow import STANDARD_GOALS
    for target in STANDARD_GOALS:
        _,eid=env.new_episode(project_id=pid,code='G_'+target)
        for stage in STANDARD_GOALS:
            if stage==target:
                env.wb.open_stage(eid,stage)
                break
            job=env.wb.create_task(eid,stage,STANDARD_GOALS[stage])
            env.drain(job['runId'])
            art=env.art(eid,stage);vid=art['versions'][-1]['id']
            if stage=='story':env.drain(env.wb.submit_review(vid,'review-'+eid)['runId'])
            env.wb.adopt(art['id'],vid,None,'adopt-'+eid+stage)
    def run(req):
        manifest=env.wb.run_manifest(req.run_id)['manifest']
        if manifest['episodeId'] not in (second,third): return directed(req,'codex')
        if req.kind=='review':
            with env.db() as db:goal=db.execute('SELECT goal FROM task WHERE id=?',(manifest['taskId'],)).fetchone()[0]
            if '允许调整引发问题' in goal or ('第三种表现方式' in goal and manifest['episodeId']==second):return review(req)
            return review(req,'return',[dict(category='source',reason='钥匙的交接需要明确呈现')])
        if manifest.get('stageDialogue'):
            return dict(draft(req),kind='discussion',changes=[],directorRequest='',reply='保留值班员完成交接的目标，用看得见的钥匙动作交代来源。')
        value=directed(req,'codex')
        if manifest['stage']=='story':
            with env.db() as db:goal=db.execute('SELECT goal FROM task WHERE id=?',(manifest['taskId'],)).fetchone()[0]
            if '第三种表现方式' in goal or '允许调整引发问题' in goal:
                tail='值班员先展示钥匙，再完成交接。' if '第三种表现方式' in goal else '值班员从交接盒中取出钥匙，并当面登记。'
                for change in value['changes']:
                    if change['field']=='synopsis':change['value']=('\n\n'.join([change['value']+tail]*8))
        return value
    env.codex.script=[run]*200;env.claude.script=[run]*200
    return first,second
