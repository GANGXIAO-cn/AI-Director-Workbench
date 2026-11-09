import json
import re

from workbench.db import transaction

def prepare(env):
    pid,eid=env.new_episode(name="站台选题", code="旧01")
    root=env.wb.store.root.parent
    catalog=dict(candidates=[dict(id="原01",title="夜班旧卡",cn="把遗失钥匙送回柜台",mechanisms=["误认"],
                                  status="候选",oldref="夹具旧题对照",payoff="钥匙归还")])
    (root/"选题.json").write_text(json.dumps(catalog,ensure_ascii=False))
    with transaction(env.wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?",(json.dumps(dict(assetRoot=str(root),topicCatalog="选题.json")),pid))
        db.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?)",
                   ("台账01",pid,eid,None,"旧集","旧集以归还钥匙收尾","确认交付","旧01",1))
    env.wb.import_knowledge(pid,[dict(name="交接规定",kind="case",data="交班单有两联，请核对编号。".encode())])
    def research(req):
        assert req.kind=="topic_research"
        for text in ["把遗失钥匙送回柜台","旧集以归还钥匙收尾","交班单有两联","找一个能拍出夜班交接动作的新故事"]:
            assert text in req.prompt, text
        assert re.search(r"^===== method · ",req.prompt,re.M)
        source=re.search(r"^===== research_source · (知识库 · 交接规定.*?) =====$",req.prompt,re.M).group(1)
        card=dict(title="交班的编号",premise="站务员按交班单的编号寻找第二联。",motivation="在发车前交班",
                  mechanism="两联编号相同",difference="旧集归还钥匙，这次补签第二联。",source=source,quote="交班单有两联")
        return dict(candidates=[dict(card,title="提前交班",premise="接班人早到了一站。"),card])
    env.codex.script.append(research)
