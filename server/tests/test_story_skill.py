import subprocess

from helpers import Env
from workbench import story_skill


def test_old_episode_reads_original_commit_after_skill_upgrade(tmp_path, monkeypatch):
    repo = tmp_path / "story-skill"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name in ("references/story-standard.md", "templates/story-draft.md", "config.json"):
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("{}" if name.endswith("json") else "测试正文")
    monkeypatch.setattr(story_skill, "SKILL_DIR", repo)
    env = Env(tmp_path)
    wb = env.wb
    pid = wb.create_project("puggy", "技能版本验收")["projectId"]
    ids = []
    commits = []
    for code in ("A", "B"):
        (repo / "SKILL.md").write_text("版本 " + code)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", code], check=True)
        commits.append(subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip())
        topic = wb.add_topic(pid, code, "测试故事")
        kickoff = wb.start_episode(None, "topic", {"create":{"provider":"claude"}, "review":{"provider":"codex"}},
                                   project_id=pid, display_code=code, topic_id=topic["topicId"], use_story_skill=True)
        ids.append(kickoff["episodeId"])
    for eid, code, commit in zip(ids, ("A", "B"), commits):
        run = wb.create_task(eid, "story", "写本集故事稿")["runId"]
        env.drain(run)
        req = env.claude.calls[-1]
        assert req.story_skill and req.effort == "medium" and req.model == "sonnet"
        assert (req.workdir / "skill/SKILL.md").read_text() == "版本 " + code
        assert req.workdir.is_relative_to(wb.store.root / "projects" / pid / "episodes" / eid)
        method = next(s for s in wb.stage_methods(eid, "story")["sections"] if s["name"] == "skill/SKILL.md")
        assert method["text"] == "版本 " + code
        assert wb.run_methods(run)["sections"][0]["source"]["commit"] == commit
        assert wb.run_manifest(run)["manifest"]["storySkillCommit"] == commit
        assert env.art(eid, "story")["versions"][0]["source_run_id"] == run
