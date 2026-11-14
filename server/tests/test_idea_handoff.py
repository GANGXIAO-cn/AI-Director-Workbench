from helpers import Env


def kickoff(env, idea):
    pid = env.wb.create_project("_template", "渡轮")["projectId"]
    args = dict(episode_id=None, project_id=pid, display_code="E01", source_type="idea", idea_text=idea,
                start_story=True, command_id="homepage-submit",
                executor_plan={"create":{"provider":"codex"},"review":{"provider":"claude"}})
    return pid, args


def test_one_submit_then_discuss_without_revision(tmp_path):
    env = Env(tmp_path)
    idea = "末班船就要离岸，学徒必须找到订了蛋糕的寿星。"
    _, args = kickoff(env, idea)
    result = env.wb.start_episode(**args)
    assert result == env.wb.start_episode(**args)
    eid = result["episodeId"]
    env.drain(result["runId"])
    art = env.art(eid,"story")
    assert len(art["tasks"]) == len(art["versions"]) == 1,art
    assert art["tasks"][0]["messages"][0]["text"] == idea
    assert not art["adopted_version_id"]
    vid = art["versions"][0]["id"]
    follow = env.wb.post_message(result["taskId"],"现在什么状态？","ask",base_version_id=vid,
                                 workspace_revision=art["revision"],discussion_only=True)
    env.drain(follow["runId"])
    assert "stage_status" in env.codex.calls[-1].prompt
    art = env.art(eid,"story")
    assert len(art["versions"])==1 and art["tasks"][0]["messages"][-1]["kind"]=="text"
    assert art["tasks"][0]["messages"][-1]["role"]=="assistant"
    reply = art["tasks"][0]["messages"][-1]["text"]
    write = env.wb.post_message(result["taskId"], "按刚才讨论的要求改稿", "write",
                                base_version_id=vid, workspace_revision=art["revision"])
    manifest = env.wb.run_manifest(write["runId"])["manifest"]
    conversation = next(i for i in manifest["inputs"] if i["role"] == "conversation")
    import json
    turns = json.loads(env.wb.store.read_blob(conversation["sha256"]))
    assert turns[-2:] == [{"role":"user","text":"现在什么状态？"}, {"role":"assistant","text":reply}]


def test_random_choice_uses_only_this_project_and_is_idempotent(tmp_path):
    env = Env(tmp_path)
    pid,args = kickoff(env,"随机选")
    card=env.wb.add_topic(pid,"BOAT","学徒上错船")
    result=env.wb.start_episode(**args)
    assert not result.get("runId")
    art=env.art(result["episodeId"],"story")
    assert art["tasks"][0]["state"]=="clarifying" and not art["versions"]
    choice=env.wb.answer_clarification(result["taskId"],"从选题池挑","pick")
    assert choice==env.wb.answer_clarification(result["taskId"],"从选题池挑","pick")
    env.drain(choice["runId"])
    assert env.wb.episode_view(result["episodeId"])["kickoff"]["topic_id"]==card["topicId"]
    assert "学徒上错船" in env.codex.calls[-1].prompt
