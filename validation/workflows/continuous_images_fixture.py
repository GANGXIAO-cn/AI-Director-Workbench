"""07 图片选择卡与并排比较页的界面夹具。

专家 AI 第 1 步起，连续制作不再新弹“关键视觉”和“图片三轮仍有问题”选择卡：
关键视觉自动采用合格候选，修不好的图由 AI 暂用。日常库里在途的旧选择卡（如 E08）仍要能显示和选择，
所以本夹具直接构造这两类旧卡片（与旧代码写入的 choice 结构相同），只验证界面，不再依赖流程产生卡片。
"""
from test_image_assess import assessment
from workbench.db import transaction, dumps, new_id


PORTRAIT, LANDSCAPE = (864, 1536), (1536, 864)


def prepare(env):
    env.images.capabilities['sizes'] = [*env.images.capabilities['sizes'], '1536x864']
    # Card/comparison checks are read-only: reuse an existing fixture.
    for project in env.wb.list_projects():
        if project['name'] == '07 图片选择验收':
            episodes = env.wb.project_view(project['id'])['episodes']
            if {e['display_code'] for e in episodes} >= {'E01', 'E02', 'E03', 'E04', 'E05'}:
                return
    pid = env.wb.create_project('_template', '07 图片选择验收')['projectId']
    issue = dict(type='usage', where='支撑', text='门框支撑关系不对')
    for code, mode in [('E01', 'visual'), ('E02', 'single'), ('E03', 'failed'), ('E04', 'visual'), ('E05', 'failed')]:
        _, eid = env.new_episode(project_id=pid, code=code)
        landscape = code in ('E04', 'E05')
        if landscape:
            env.wb.episode_spec_change(eid, 'aspectRatio', '16:9', code + '-landscape', confirm=True)
        env.adopt_story(eid); env.script(eid)
        design = dict(strategy='lightweight', assets=[dict(id='room', kind='scene', title='门厅场景', description='门厅空景',
                                                          imagePrompt='门厅空景', sceneIds=['场01'], keyVisual=mode != 'failed')])
        env.save_adopt(eid, 'asset_design', design, code + '-design')
        art = next(a for a in env.wb.episode_view(eid)['artifacts'] if a['stage'] == 'asset_design')
        env.wb.initialize_asset_worksheet(eid, art['adopted_version_id'], code + '-worksheet')
        # 在途的旧连续制作：不推进，只挂上旧选择卡。
        original = env.wb._advance_continuous
        env.wb._advance_continuous = env.wb._continuous_record
        try:
            run = env.wb.start_continuous(eid, target_stage='delivery', command_id=code + '-start')
        finally:
            env.wb._advance_continuous = original
        need = env.wb._need(eid, 'room')
        count = 1 if mode == 'single' else 2
        candidates = []
        for n in range(count):
            vid = env.image(pid, eid, f'门厅候选{n + 1}', 'scene', size=LANDSCAPE if landscape else PORTRAIT,
                            rgb=(40 + 60 * n, 80, 120))['versionId']
            body = assessment(vid, verdict='blocked', critical=[issue], reason='门框支撑关系不对') if mode == 'failed' \
                else assessment(vid, reason='门厅空间清楚，可作主场景')
            with transaction(env.wb.db_path) as db:
                db.execute('INSERT OR IGNORE INTO asset_need_candidate VALUES(?,?)', (need['id'], vid))
                db.execute('INSERT INTO image_assessment VALUES(?,?,?,?,?,?,?,?)',
                           (new_id(), eid, need['id'], vid, code + '-assess', body['verdict'], dumps(body), env.wb.clock()))
            candidates.append(dict(key=chr(65 + n), **body))
        ratio = '16:9' if landscape else '9:16'
        if mode == 'failed':
            choice = dict(aspectRatio=ratio, kind='image_failed', stage='assets', title='门厅场景三轮后仍有这些问题',
                          needKey='room', needRevision=need['revision'], candidates=candidates)
        else:
            choice = dict(aspectRatio=ratio, kind='visual', stage='assets', title='选一张你喜欢的关键视觉',
                          needKey='room', needRevision=need['revision'], candidates=candidates, regenerate=mode == 'single')
        env.wb._continuous_update(run['id'], state='waiting_choice', choice=choice, reason=choice['title'])
