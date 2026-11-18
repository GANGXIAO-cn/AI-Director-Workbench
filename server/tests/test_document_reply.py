"""Direct manuscript replies use the real service's existing content gates."""
import json

import pytest

from helpers import Env
from workbench import contracts, prompts
from workbench.executors.base import ExecOutcome
from workbench.replyformat import document_to_internal


def setup_plan(tmp_path):
    env = Env(tmp_path)
    _, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    env.ag.strict_schema = False
    return env, eid


def run_row(env, rid):
    with env.db() as db:
        return dict(db.execute('SELECT * FROM run WHERE id=?', (rid,)).fetchone())


def create_plan(env, eid):
    return env.wb.create_task(eid, 'plan', '完成镜头稿', executor={'create': {'provider': 'antigravity'}})


def test_plan_document_is_saved_as_internal_result(tmp_path):
    env, eid = setup_plan(tmp_path)
    content = dict(env.plan86(), notes='按动作组织镜头。')
    env.ag.script = [lambda req: dict(kind='draft', fields=content)]
    task = create_plan(env, eid)
    env.drain(task['runId'])
    row = run_row(env, task['runId'])
    assert row['status'] == 'succeeded', row
    request = env.ag.calls[-1]
    assert request.schema is None
    assert 'changes' not in request.prompt
    assert '回包原样填写' not in request.prompt
    result = json.loads((env.wb.store.run_dir(row['id']) / 'result.json').read_text())
    assert not contracts.validate(result)
    with env.db() as db:
        stored = json.loads(db.execute('SELECT content FROM version WHERE source_run_id=?', (row['id'],)).fetchone()[0])
    assert stored['shots'] == content['shots'] and stored['segments'] == content['segments']


def test_characters_document_and_frozen_legacy_instruction(tmp_path):
    env = Env(tmp_path)
    _, eid = env.new_episode()
    env.adopt_story(eid)
    env.ag.strict_schema = False
    art = env.art(eid, 'characters')
    with env.db() as db:
        content = json.loads(db.execute('SELECT content FROM version WHERE id=?', (art['adopted_version_id'],)).fetchone()[0])
    env.ag.script = [lambda req: dict(kind='draft', fields=content, reply='', directorRequest='完善角色')]
    task = env.wb.create_task(eid, 'characters', '完善角色', executor={'create': {'provider': 'antigravity'}})
    env.drain(task['runId'])
    row = run_row(env, task['runId'])
    assert row['status'] == 'succeeded', row
    prompt = env.ag.calls[-1].prompt
    assert '必须提交 characters 字段，内容为完整角色数组' in prompt
    assert 'changes' not in prompt and 'value 为' not in prompt
    entry = dict(role='stage_contract', text='必须在 changes 中提交 field=characters，其 value 为完整角色数组的 JSON 文本。')
    assert 'changes' not in prompts.document_entries([entry])[0]['text']
    assert 'changes' in entry['text']


@pytest.mark.parametrize('failure', ['schema', 'conversion', 'precheck'])
def test_one_format_retry_with_snapshot(tmp_path, failure):
    env, eid = setup_plan(tmp_path)
    snapshots = []
    def first(req):
        snapshots.append((req.prompt, req.attempt))
        if failure == 'schema':
            return ExecOutcome(ok=False, error_class='schema', diagnostic='第 3 行第 7 列，Expecting delimiter')
        if failure == 'conversion':
            return dict(kind='draft', fields=[])
        return dict(kind='draft', fields={'notWritable': []})
    def second(req):
        snapshots.append((req.prompt, req.attempt))
        return dict(kind='draft', fields=dict(env.plan86(), notes='按动作组织镜头。'))
    env.ag.script = [first, second]
    task = create_plan(env, eid)
    env.drain(task['runId'])
    row = run_row(env, task['runId'])
    assert row['status'] == 'succeeded', row
    assert row['format_attempts'] == 1 and len(snapshots) == 2
    expected = {'schema': '第 3 行第 7 列', 'conversion': '回复须为 JSON 对象', 'precheck': '越界修改'}[failure]
    assert expected not in snapshots[0][0] and expected in snapshots[1][0]
    assert [n for _, n in snapshots] == [1, 2]


@pytest.mark.parametrize('second', ['schema', 'conversion', 'precheck'])
def test_failed_retry_does_not_trigger_third_call(tmp_path, second):
    env, eid = setup_plan(tmp_path)
    bad = ExecOutcome(ok=False, error_class='schema', diagnostic='第 1 行第 1 列')
    follow = {'schema': bad, 'conversion': {'fields': []}, 'precheck': {'fields': {'unknown': []}}}[second]
    env.ag.script = [lambda req: bad, lambda req: follow]
    task = create_plan(env, eid)
    env.drain(task['runId'])
    row = run_row(env, task['runId'])
    assert row['status'] == 'failed' and row['format_attempts'] == 1
    assert len(env.ag.calls) == 2


@pytest.mark.parametrize('auto', [False, True])
@pytest.mark.parametrize('kind', ['discussion', 'draft'])
def test_dialogue_modes(tmp_path, auto, kind):
    env, eid = setup_plan(tmp_path)
    goal = '请完善镜头稿' if kind == 'draft' else '请解释镜头设计'
    env.ag.script = [lambda req: dict(kind=kind, fields=dict(env.plan86(), notes='按动作组织镜头。') if kind == 'draft' else {},
                                     reply='镜头按动作展开。' if kind == 'discussion' else '',
                                     directorRequest=goal if kind == 'draft' else '')]
    art = env.art(eid, 'plan')
    # The protected base supplies the ordinary non-auto dialogue branch.
    if not auto:
        vid, _ = env.save_adopt(eid, 'plan', dict(env.plan86(), notes='按动作组织镜头。'))
        art = env.art(eid, 'plan')
    options = dict(auto_dialogue=auto, workspace_revision=art['revision'], base_version_id=art['adopted_version_id'])
    task = env.wb.create_task(eid, 'plan', goal, executor={'create': {'provider': 'antigravity'}}, **options)
    env.drain(task['runId'])
    row = run_row(env, task['runId'])
    assert row['status'] == 'succeeded', row
    result = json.loads((env.wb.store.run_dir(row['id']) / 'result.json').read_text())
    assert result['kind'] == kind and ('removedIds' in result) is auto
    assert 'changes' not in env.ag.calls[-1].prompt


@pytest.mark.parametrize('flag,kind,director_request', [('forceDraft','discussion',''), ('readOnly','draft','改稿'), ('auto','draft','错误原文')])
def test_dialogue_rules_remain_enforced(tmp_path, flag, kind, director_request):
    env, eid = setup_plan(tmp_path)
    task = create_plan(env, eid)
    run = run_row(env, task['runId'])
    with env.db() as db:
        t = dict(db.execute('SELECT * FROM task WHERE id=?', (run['task_id'],)).fetchone())
    dialogue = {flag: True, 'request': '改稿'}
    result = document_to_internal(run, t, dict(kind=kind, fields=dict(env.plan86(), notes='按动作组织镜头。') if kind == 'draft' else {},
                                             reply='' if kind == 'draft' else '解释', directorRequest=director_request), dialogue)
    _, errors = env.wb._check_stage_dialogue(run, t, dialogue, result)
    assert errors


def test_scope_and_unchanged_prompt(tmp_path):
    env, eid = setup_plan(tmp_path)
    env.ag.strict_schema = False
    story = env.wb.create_task(eid, 'story', '完善梗概', executor={'create': {'provider': 'antigravity'}})
    env.drain(story['runId'])
    assert env.ag.calls[-1].schema is not None
    plan = env.wb.create_task(eid, 'plan', '完成镜头稿')
    env.drain(plan['runId'])
    req = env.codex.calls[-1]
    assert req.schema is not None and 'changes' in req.prompt and 'targetId' not in req.prompt
    assert env.wb._document_mode({'purpose':'create','provider':'antigravity'}, {'stage':'publish'})
    for stage in ('story', 'stress'):
        assert not env.wb._document_mode({'purpose':'create','provider':'antigravity'}, {'stage':stage})
    for purpose in ('review','cold_read','comparison','parallel'):
        assert not env.wb._document_mode({'purpose':purpose,'provider':'antigravity'}, {'stage':'plan'})


@pytest.mark.parametrize('stage,field,value', [
    ('characters','characters',[{'id':'C01','name':'猫'}]),
    ('world','rules',[{'id':'R01','rule':'规则'}]),
    ('treatment','scenes',[{'id':'SC01','event':'行动'}]),
    ('script','scenes',[{'id':'SC01','action':'他说："走吧"。\n猫推门。'}]),
    ('asset_design','assets',[{'id':'A01','description':'木箱'}]),
    ('acting','scenes',[{'sceneId':'SC01','action':'观察'}]),
    ('plan','shots',[{'id':'S01','action':'推门'}]),
    ('prompts','segments',{'G01':{'txt':'00:00.000-00:05.000\n起镜，猫推门。'}}),
])
def test_all_document_stages_preserve_native_field_values(stage, field, value):
    from workbench.stages import DOCUMENT_STAGES, normalize
    assert stage in DOCUMENT_STAGES
    run = dict(id='r',input_hash='a'*64,base_version_id=None)
    result = document_to_internal(run, {'artifact_id':'a'}, {'fields':{field:value}})
    assert not contracts.validate(result)
    assert normalize(stage, field, result['changes'][0]['value']) == value


@pytest.mark.parametrize("dialogue,clarify", [(None,False), (None,True), ({"auto":True},False)])
def test_writing_contract_omits_tracking_fields(dialogue, clarify):
    schema = json.dumps(contracts.writing_schema(dialogue, clarify))
    for key in ("schemaVersion", "inputFingerprint", "runId", "baseVersionId", "targetId"):
        assert key not in schema
        assert key not in prompts.draft_trailer("publish")


def test_content_only_draft_is_bound_to_request(tmp_path):
    env, eid = setup_plan(tmp_path)
    content = env.plan86()
    env.codex.script = [lambda req: {"changes":[{"field":k, "value":json.dumps(v,ensure_ascii=False)}
                                                 for k,v in content.items()]}]
    rid = env.wb.create_task(eid, "plan", "完成镜头稿")["runId"]
    env.drain(rid)
    assert run_row(env,rid)["status"] == "succeeded"
    result = json.loads((env.wb.store.run_dir(rid) / "result.json").read_text())
    assert result["runId"] == rid and not contracts.validate(result)
    assert all(change["targetId"] == env.art(eid,"plan")["id"] for change in result["changes"])


def test_bare_manuscript_accepted_for_new_draft(tmp_path):
    """实测 153：Antigravity 两次 09 回复都是顶层直接写字段的“裸稿件”。新稿按这种写法要求并接受，越界键仍被拦。"""
    env, eid = setup_plan(tmp_path)
    content = dict(env.plan86(), notes='按动作组织镜头。')
    env.ag.script = [lambda req: content]
    task = create_plan(env, eid)
    env.drain(task['runId'])
    row = run_row(env, task['runId'])
    assert row['status'] == 'succeeded' and row['format_attempts'] == 0, row
    assert '{字段名: 字段值, …}' in env.ag.calls[-1].prompt and '"fields"' not in env.ag.calls[-1].prompt
    with env.db() as db:
        stored = json.loads(db.execute('SELECT content FROM version WHERE source_run_id=?', (row['id'],)).fetchone()[0])
    assert stored['shots'] == content['shots']


def test_bare_manuscript_limits():
    """裸稿件只用于新稿：对话仍须 fields；全是不可写键时报格式错误；混有越界键时交给预检报越界。"""
    run = dict(id='r', input_hash='h', base_version_id=None)
    task = dict(artifact_id='a', stage='plan')
    writable = ('shots', 'segments', 'notes')
    with pytest.raises(ValueError):
        document_to_internal(run, task, {'shots': []}, dialogue={'request': '改'}, writable=writable)
    with pytest.raises(ValueError):
        document_to_internal(run, task, {'foo': 1}, writable=writable)
    result = document_to_internal(run, task, {'shots': [], 'foo': 1}, writable=writable)
    assert {c['field'] for c in result['changes']} == {'shots', 'foo'}


@pytest.mark.parametrize("first_shape", ["markdown", "bare"])
def test_image_dialogue_repairs_only_the_envelope(tmp_path, first_shape):
    from workbench.images import png_bytes
    env, eid = setup_plan(tmp_path)
    env.ag.reads_images = True
    goal = "请修改镜头稿"
    content = dict(env.plan86(), notes="现有镜头稿")
    def first(req):
        assert req.images
        if first_shape == "bare":
            return content
        (req.workdir.parent / "raw-response-1.txt").write_text("镜头稿\n" + json.dumps(content, ensure_ascii=False))
        return ExecOutcome(False, error_class="schema", diagnostic="Expected JSON")
    def format_only(req):
        assert not req.images and req.attempt == 2
        assert "现有镜头稿" in req.prompt
        assert "fields" in req.schema["required"]
        return dict(kind="draft", reply="", directorRequest=goal, removedIds=[],
                    fields={k: v if isinstance(v, str) else json.dumps(v) for k,v in content.items()})
    env.ag.script = [first, format_only]
    job = env.wb.create_task(eid, "plan", goal, executor={"create":{"provider":"antigravity"}},
                             auto_dialogue=True, workspace_revision=env.art(eid,"plan")["revision"],
                             files=[("frame.png", png_bytes(20,20))])
    env.drain(job["runId"])
    row = run_row(env, job["runId"])
    assert row["status"] == "succeeded", row["diagnostic"]
    assert row["format_attempts"] == 1
