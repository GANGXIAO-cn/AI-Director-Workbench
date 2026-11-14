import pytest

from helpers import Env


@pytest.mark.parametrize("source", ["idea", "topic", "import"])
def test_projects_share_story_method_and_only_receive_their_own_materials(tmp_path, source):
    env = Env(tmp_path)
    wb = env.wb
    versions = []
    for config, name, cue in (("_template", "雨城悬疑", "夜班修表师收到一只倒走的钟"),
                              ("puggy", "PUGGY ROOM", "MILO发现最后一辆车要开走了")):
        pid = wb.create_project(config, name)["projectId"]
        if config == "puggy":
            wb.sync_project_methods(pid,"load-project-methods")
        eid = wb.create_episode(pid, "E01")["episodeId"]
        if source == "import":
            imp = wb.import_file(pid, "来稿.md", cue.encode(), "story_source", episode_id=eid)
            args = {"source_import_id":imp["importId"]}
        elif source == "topic":
            args = {"topic_id":wb.add_topic(pid, "T01", cue)["topicId"]}
        else:
            args = {"idea_text":cue}
        wb.start_episode(eid, source, {"create":{"provider":"codex"}, "review":{"provider":"claude"}}, **args)
        task = wb.create_task(eid, "story", "写本集故事")
        env.drain(task["runId"])
        req = env.codex.calls[-1]
        assert cue in req.prompt
        manifest = wb.run_manifest(task["runId"])["manifest"]
        files = wb.store.manifest_texts(manifest)
        assert manifest["methodPack"]["id"] == "unified-film@draft"
        assert "storySkillCommit" not in manifest
        assert list(req.workdir.iterdir()) == []
        versions.append([(f["name"], f["sha256"]) for f in files if f["role"] == "method"])
        if config == "_template":
            assert all(term not in req.prompt for term in ("MILO","LEO","PUGGY","女主人","Fourthwall","Suno"))
            assert not any(f["role"] == "project_method" for f in files)
        else:
            assert "MILO" in req.prompt and "LEO" in req.prompt
            method, = [f for f in files if f["role"] == "project_method"]
            assert "机制看事件，不看标签" in method["text"]
            assert any(f["role"] == "project_settings" for f in files)
        assert env.art(eid, "story")["versions"][0]["source_run_id"] == task["runId"]
    assert versions[0] == versions[1]
