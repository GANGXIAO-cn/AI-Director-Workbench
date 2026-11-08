import json

from helpers import Env, PUBLISH
from workbench.db import transaction
from workbench.storage import sha256_bytes


def test_baseline_inputs_follow_project_and_keep_kickoff_bytes(tmp_path):
    env = Env(tmp_path / "workbench")
    wb = env.wb
    project = wb.create_project("_template", "站台")["projectId"]
    other = wb.create_project("_template", "其他项目")["projectId"]
    assert wb.project_baselines(project) == []
    root = tmp_path / "sources"
    root.mkdir()
    sound = root / "声音.md"
    original = "# 声音\n车门合拢。\n".encode()
    sound.write_bytes(original)
    visual = {"blocks": {"visual_style": {"version": "v3", "zh": "固定机位。"}}}
    (root / "镜头.json").write_text(json.dumps(visual))
    config = dict(assetRoot=str(root), soundStandard=sound.name, promptStandard="镜头.json")
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?", (json.dumps(config), project))
    env.new_episode(project_id=project)
    sound.write_text("# 声音\n雨点落在车顶。\n")
    (root / "镜头.json").write_text('{"blocks": []}')
    docs = {d["kind"]: d for d in wb.project_baselines(project)}
    assert docs["soundStandard"]["blocks"][0]["text"] == sound.read_text()
    assert docs["soundStandard"]["sha256"] == sha256_bytes(sound.read_bytes())
    copy, = docs["soundStandard"]["copies"]
    assert (copy["code"],copy["path"],copy["sha256"],copy["effectiveSha256"]) == ("E01",sound.name,sha256_bytes(original),sha256_bytes(original))
    assert wb.store.read_blob(copy["sha256"]) == original
    assert "格式错误" in docs["promptStandard"]["error"]
    assert docs["promptStandard"]["stages"] == ("prompts",)
    assert wb.project_baselines(other) == []


def test_revise_follow_and_freeze_project_baselines(tmp_path):
    import pytest
    from workbench.db import connect
    from workbench.errors import Conflict, Rejected

    env = Env(tmp_path / "service")
    wb = env.wb
    pid = wb.create_project("_template","试制")["projectId"]
    wb.set_publish_settings(pid,PUBLISH)
    other = wb.create_project("_template","另一项目")["projectId"]
    # A flex segment that quotes a grid-only block still needs review. Its neighbour does not.
    spec = {"blocks":{"grid_reference":{"zh":"请按格位跟拍。","version":"v6"},"visual_style":{"zh":"静态镜头。","version":"v2"}}}
    path = tmp_path / "基准.json"
    path.write_text(json.dumps(spec,ensure_ascii=False))
    original = path.read_bytes()
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?", (json.dumps(dict(assetRoot=str(tmp_path),promptStandard=path.name)),pid))
    seed = env.full_episode(project_id=pid,segments={"G01":[8000],"G02":[8000]},deliver=False)
    eid = seed["eid"]
    prompts = env.prompts_for(seed["plan"], seed["img"]["versionId"])
    prompts["segments"]["G01"]["txt"] += "请按格位跟拍。"
    pv,_ = env.save_adopt(eid,"prompts",prompts,"using-grid-text")
    env.review_segments(pv,["G01","G02"],"baseline")
    def hashes():
        with connect(wb.db_path) as db:
            _,cold,comp = wb._segment_hashes(db,pv)
            return cold, comp
    cold, comp = hashes()
    assert comp["G01"] and comp["G02"]
    args = dict(project_id=pid,kind="promptStandard",block_key="blocks.grid_reference",text="按九宫格逐格核对。",expected_sha=sha256_bytes(original),expected_revision=0)
    old_run = wb.create_task(eid,"prompts","旧基准排队任务")["runId"]
    preview = wb.revise_baseline(**args)
    assert preview["episodes"] == [dict(episodeId=eid,code="E01",segments=[dict(segment="G01",reason="提示词逐字引用",hasPrompts=True)])]
    with pytest.raises(Conflict):
        wb.revise_baseline(**dict(args,text="另一种修改"),preview_hash=preview["previewHash"])
    saved = wb.revise_baseline(**args,preview_hash=preview["previewHash"],command_id="project-rule")
    assert wb.revise_baseline(**args,preview_hash=preview["previewHash"],command_id="project-rule") == saved
    with pytest.raises(Conflict):
        wb.revise_baseline(**args,preview_hash=preview["previewHash"])
    assert path.read_bytes() == original and hashes() == (cold,comp)
    follow = dict(project_id=pid,episode_id=eid,revision_id=saved["revisionId"])
    scoped = wb.follow_baseline(**follow)
    with pytest.raises(Rejected):
        wb.follow_baseline(**dict(follow,project_id=other))
    applied = wb.follow_baseline(**follow,preview_hash=scoped["previewHash"],command_id="episode-rule")
    assert wb.follow_baseline(**follow,preview_hash=scoped["previewHash"],command_id="episode-rule") == applied
    cold2,comp2=hashes()
    assert cold2==cold and comp2["G02"]==comp["G02"] and comp2["G01"]!=comp["G01"]
    new_run=wb.create_task(eid,"prompts","跟进后排队任务")["runId"]
    for rid, wanted in [(old_run,sha256_bytes(original)),(new_run,saved["sha256"])]:
        frozen=wb.run_manifest(rid)["manifest"]
        assert next(i["sha256"] for i in frozen["inputs"] if i["role"]=="promptStandard")==wanted
    _, later = env.new_episode(project_id=pid,code="E02")
    with connect(wb.db_path) as db:
        assert db.execute("SELECT sha256 FROM episode_source WHERE episode_id=?",(later,)).fetchone()[0]==saved["sha256"]
        assert wb._prompt_version_sources(db,pv,eid)["promptStandard"]==sha256_bytes(original)
    assert json.loads(wb.store.read_blob(saved["sha256"]))["blocks"]["grid_reference"]["version"]=="v7"
    style = wb.revise_baseline(**dict(args,block_key="blocks.visual_style",text="雨中逆光。",expected_revision=1,expected_sha=saved["sha256"]))
    wb.revise_baseline(**dict(args,block_key="blocks.visual_style",text="雨中逆光。",expected_revision=1,expected_sha=saved["sha256"]),preview_hash=style["previewHash"])
    assert wb.source_styles(pid)[0]["text"]=="雨中逆光。"
    folder=tmp_path/"portable"
    wb.export_project_folder(pid,str(folder))
    with connect(folder/"workbench.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM baseline_revision").fetchone()[0]==2
        assert db.execute("SELECT COUNT(*) FROM episode_baseline_change").fetchone()[0]==1


def test_role_revision_uses_bound_cast_and_keeps_other_fields(tmp_path):
    import pytest
    from workbench.errors import Conflict

    env = Env(tmp_path / "service")
    wb = env.wb
    pid = wb.create_project("_template","双人镜头")["projectId"]
    wb.set_publish_settings(pid,PUBLISH)
    path = tmp_path/"角色.json"
    role = {"name":"售票员","role_version":"img9","sha256":"0"*64,"zh":"长外观","en":"uniform","video_zh":"蓝袖口。","video_zh_version":"v3"}
    path.write_text(json.dumps({"roles":{"clerk":role}}))
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?",(json.dumps({"assetRoot":str(tmp_path),"characterStandard":path.name}),pid))
    seed=env.full_episode(project_id=pid,segments={"G01":[8000],"G02":[8000]},deliver=False)
    vid=seed["img"]["versionId"]
    wb.set_cast(seed["eid"],"G01","售票员","ok",vid)
    base=sha256_bytes(path.read_bytes())
    args=dict(project_id=pid,kind="characterStandard",block_key="roles.clerk",text="藏蓝袖口。",expected_revision=0,expected_sha=base)
    first=wb.revise_baseline(**args)
    assert [s["segment"] for s in first["episodes"][0]["segments"]]==["G01"]
    wb.set_cast(seed["eid"],"G02","售票员","ok",vid)
    with pytest.raises(Conflict):
        wb.revise_baseline(**args,preview_hash=first["previewHash"])
    actual=wb.revise_baseline(**args)
    result=wb.revise_baseline(**args,preview_hash=actual["previewHash"])
    record=json.loads(wb.store.read_blob(result["sha256"]))["roles"]["clerk"]
    assert record==dict(role,video_zh=args["text"],video_zh_version="v4")
    with pytest.raises(Conflict):
        wb.send_free_chat(pid,"换袖口","codex",baseline=dict(kind=args["kind"],key=args["block_key"],revision=0,sha256=base))
    assert wb.free_chat(pid)["threads"]==[]


def test_portable_folder_from_before_baseline_revisions(tmp_path):
    import sqlite3
    from workbench.db import SCHEMA, connect
    from workbench.projectfolder import copy_records, MARKER
    from workbench.folderserver import inspect_folder, FolderWorkbench

    env=Env(tmp_path/"work")
    pid=env.wb.create_project("_template","旧项目")["projectId"]
    folder=tmp_path/"old-format"
    folder.mkdir()
    dbfile=folder/"workbench.sqlite"
    additions=("baseline_revision","episode_baseline_change","version_baseline_source")
    with sqlite3.connect(dbfile) as dest, connect(env.wb.db_path) as source:
        dest.row_factory=sqlite3.Row
        for statement in SCHEMA.split(";"):
            if statement.strip() and not any("CREATE TABLE IF NOT EXISTS "+name+"(" in statement for name in additions):
                dest.execute(statement)
        copy_records(source,dest,pid)
    (folder/MARKER).write_text(json.dumps(dict(format="ai-director-project", version=1, projectId=pid,
                                               initialDatabaseSha256=sha256_bytes(dbfile.read_bytes()))))
    assert inspect_folder(folder)==pid
    wb=FolderWorkbench(folder,env.wb)
    assert wb.project_baselines(pid)==[]
