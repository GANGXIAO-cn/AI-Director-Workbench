import re


def prepare(env):
    project = env.wb.create_project("_template", "站台选题")["projectId"]
    env.wb.add_topic(project, "原01", "把遗失钥匙送回柜台")

    def research(req):
        assert req.kind == "topic_research"
        for text in ["把遗失钥匙送回柜台", "做两个长篇选题，每条4分钟", "不得为填满时长硬凑剧情"]:
            assert text in req.prompt
        ref = re.search(r"^===== research_source · (R\d+) · 导演的研究方向", req.prompt, re.M).group(1)
        card = dict(title="交班的编号", premise="站务员按交班单的编号寻找第二联。", motivation="在发车前交班",
                    mechanism="两联编号相同", difference="旧题归还钥匙，这次补签第二联。", reference_id=ref,
                    runtime=dict(form="长篇", minSeconds=240, maxSeconds=240))
        assert req.schema["properties"]["candidates"]["minItems"] == 5
        cards = [dict(card, title="提前交班", premise="接班人早到了一站。"), card]
        cards.extend(dict(card, title=f"夜班 {i}", premise=f"第 {i} 班车临时换站。") for i in range(3))
        return dict(candidates=cards)

    env.codex.script.append(research)
