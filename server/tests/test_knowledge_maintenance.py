"""Mounted-directory maintenance must update the same native FTS, catalog and exact source files."""
import csv
import json
import subprocess
from unittest.mock import patch

import pytest

from helpers import Env
from workbench.errors import Conflict, Rejected


def library(root):
    root.mkdir()
    subprocess.run(["sqlite3",str(root/"knowledgeIndex.sqlite")],input="""
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        INSERT INTO metadata VALUES ('schema','aistudio-md-2'),('kind_version','2');
        CREATE TABLE documents(id INTEGER PRIMARY KEY,path TEXT UNIQUE,title TEXT,category TEXT,kind TEXT,size INTEGER,modified_ns INTEGER,sha256 TEXT,indexed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE excluded_sources(path TEXT PRIMARY KEY,reason TEXT);
        CREATE VIRTUAL TABLE search USING fts5(terms,content='',contentless_delete=1,detail='none');
    """, text=True,check=True)


def test_library_incremental_refresh_and_frozen_citations(tmp_path):
    env = Env(tmp_path/"app")
    wb = env.wb
    _, eid = env.new_episode()
    root = tmp_path/"library"
    library(root)
    wb.mount_resource("knowledge",str(root))
    args = dict(expected_path=str(root),category="07-用户授权作品",title="夜行人",content="门外，只剩一串蓝焰。")
    saved = wb.add_script_document(**args)
    assert (saved["added"],saved["indexedDocuments"]) == (1,1)
    assert wb.add_script_document(**args)["added"] == 0
    with pytest.raises(Rejected,match="同名"):
        wb.add_script_document(**dict(args,content="不同内容"))
    with pytest.raises(Rejected,match="正式资料分类"):
        wb.add_script_document(**dict(args,category="../library"))
    assert wb.browse_scripts("蓝焰")["rows"][0]["path"] == saved["path"]
    source = wb.script_excerpt(saved["path"])
    recommended = wb.browse_scripts("蓝焰",recommended=True,episode_id=eid,stage="story")["rows"][0]
    assert recommended["match"] == {"offset":args["content"].index("蓝焰"),"text":"蓝焰"}
    source["match"] = recommended["match"]
    with pytest.raises(Rejected,match="正文命中"):
        wb.save_step_config(eid,"story",mode="match",references=[dict(source,match={"offset":0,"text":"蓝焰"})])
    wb.save_step_config(eid,"story",mode="match",references=[source])
    run = wb.create_task(eid,"story","写故事")["runId"]
    frozen = wb.run_manifest(run)["manifest"]
    ref = next(i for i in wb.store.manifest_texts(frozen) if i["role"]=="citation")
    f = root/saved["path"]
    f.write_text("雨停了，门外留下缝纫机。")
    with pytest.raises(Rejected,match="指纹"):
        wb.script_excerpt(saved["path"])
    (root/"90-隔离与待审").mkdir()
    (root/"90-隔离与待审"/"忽略.md").write_text("缝纫机")
    (f.parent/"外部原稿.md").write_text("一枝紫藤。")
    result = wb.refresh_script_index(str(root))
    assert (result["added"],result["changed"],result["removed"]) == (1,1,0)
    assert wb.browse_scripts("蓝焰")["total"] == 0 and wb.browse_scripts("缝纫")["total"] == 1
    with (root/"knowledgeCatalog.csv").open(encoding="utf-8-sig") as handle:
        assert {row["书名"] for row in csv.DictReader(handle)} == {"外部原稿","夜行人"}
    f.rename(root/"90-隔离与待审"/f.name)
    assert wb.refresh_script_index(str(root))["removed"] == 1
    assert wb.browse_scripts("缝纫")["total"] == 0
    assert (root/"90-隔离与待审"/f.name).is_file()
    assert json.loads((root/"knowledgeIndex.json").read_text())["documents"] == 1
    assert json.loads(ref["text"])["text"] == source["text"]
    assert wb.store.read_blob(ref["sha256"]).decode() == ref["text"]
    off = wb.save_step_config(eid,"story",mode="off",expected_revision=1)
    assert off["revision"] == 2
    with pytest.raises(Conflict):
        wb.save_step_config(eid,"story",goal="过期修改",expected_revision=1)
    restored = wb.restore_step_revision(eid,"story",1,2)
    assert restored["revision"] == 3 and restored["references"][0]["text"] == source["text"]
    assert [v["revision"] for v in wb.step_history(eid,"story")] == [3,2,1]


def test_index_rollback_on_bad_document(tmp_path):
    root = tmp_path/"library"
    library(root)
    wb = Env(tmp_path/"app").wb
    wb.mount_resource("knowledge",str(root))
    wb.add_script_document(str(root),"07-用户授权作品","光照","窗台")
    folder=root/"07-用户授权作品"
    (folder/"光照.md").write_text("新字")
    (folder/"编码错误.md").write_bytes(b"\xff")
    with pytest.raises(Rejected,match="UTF-8"):
        wb.refresh_script_index(str(root))
    assert wb.browse_scripts("窗台")["total"]==1 and wb.browse_scripts("新字")["total"]==0
    with pytest.raises(Rejected,match="挂载目录已改变"):
        wb.refresh_script_index(str(tmp_path))


def test_asset_recommendations_and_batch_citation(tmp_path):
    env = Env(tmp_path / "app")
    wb = env.wb
    _, eid = env.new_episode(idea="雨停之后两个人在站台告别")
    root = tmp_path / "library"
    library(root)
    wb.mount_resource("knowledge", str(root))
    # Two teaching excerpts have different applications. Titles give no ranking hints.
    samples = {
        "08-方法/片段甲.md": "逆光剪影要保留人物肩线与背景之间的明暗分界。",
        "08-方法/片段乙.md": "对白停顿时，让对手的表演交代没有说出口的话。",
        "07-用户授权作品/场次丙.md": "她在逆光剪影中收起雨伞。",
        "10-小说/故事丁.md": "他第一次在墙上看见自己的逆光剪影。",
    }
    for name, body in samples.items():
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(body)
    wb.refresh_script_index(str(root))
    asset_rows = wb.browse_scripts("逆光剪影", recommended=True, episode_id=eid, stage="assets")["rows"]
    assert {r["title"] for r in asset_rows} == {"片段甲", "场次丙"}
    selected, = wb.browse_scripts("逆光剪影", kind="method", recommended=True, episode_id=eid, stage="assets")["rows"]
    assert selected["title"] == "片段甲" and selected["match"]["text"] == "逆光剪影"
    script = wb.browse_scripts("", recommended=True, episode_id=eid, stage="script")["rows"]
    assert script[0]["title"] == "片段乙"
    ref = wb.script_excerpt(selected["path"], selected["matchOffset"], expected_sha256=selected["sha256"])
    ref.update(reason="借鉴人物轮廓的用光", match=selected["match"])
    wb.save_step_config(eid, "assets", mode="method", references=[ref], enhancements=["07-A"])
    args = dict(episode_id=eid, provider="fake-image", operation="generate", size="64x64",
                budget_cents=4, items=[dict(prompt="站台上的背影", inputs=[])])
    batch = wb.create_batch(**args)
    wb.save_step_config(eid, "assets", mode="off", enhancements=[])
    later = wb.create_batch(**args)
    assert "citations" not in wb.batch_view(later["batchId"])["spec"]
    spec = wb.batch_view(batch["batchId"])["spec"]
    citation, = spec["citations"]
    for key in ("title", "path", "offset", "text", "sha256", "reason", "match"):
        assert citation[key] == ref[key]
    assert spec["enhancements"]
    wb.authorize_batch(batch["batchId"], batch["summaryHash"], "authorize-fixture")
    with patch.object(env.images, "submit", wraps=env.images.submit) as sent:
        wb.execute_batch(batch["batchId"])
    prompt = sent.call_args.args[0]["prompt"]
    assert ref["text"] in prompt and ref["reason"] in prompt and samples["08-方法/片段乙.md"] not in prompt


def test_body_search_without_fts_and_pinned_citation(tmp_path):
    import sqlite3
    from hashlib import sha256
    env = Env(tmp_path/"app")
    _, eid = env.new_episode()
    wb = env.wb
    root = tmp_path/"library"
    library(root)
    wb.mount_resource("knowledge", str(root))
    text = "🎥开头\n" + "雨"*2600 + "伞架倒了。"
    with sqlite3.connect(root/"knowledgeIndex.sqlite") as db:
        for number, (category, kind) in enumerate([("08-方法", "method"), ("10-小说", "novel"), ("90-待审", "method")]):
            path = f"{category}/原稿{number}.md"
            (root/category).mkdir()
            data = text.encode("utf-8-sig")
            (root/path).write_bytes(data)
            db.execute("INSERT INTO documents(path,title,category,kind,sha256,indexed) VALUES(?,?,?,?,?,0)",
                       (path, f"原稿{number}", category, kind, ""))
        db.execute("INSERT INTO documents(path,title,category,kind,sha256,indexed) VALUES('08-方法/缺卷.md','缺卷','08-方法','method','',0)")
    assert wb.browse_scripts("伞架")["total"] == 0
    first = wb.search_script_bodies("伞架", limit=1)
    hit = first["rows"][0]
    assert first["total"] == 3 and hit["match"] == dict(offset=text.index("伞架"), text="伞架")
    second = wb.search_script_bodies("伞架", cursor=first["cursor"])
    assert [r["kind"] for r in second["rows"]] == ["novel"] and second["cursor"] is None
    assert second["issues"][0]["path"].endswith("缺卷.md")
    for filters in [dict(kind="method"),dict(recommended=True),dict(category="08-方法")]:
        assert [r["path"] for r in wb.search_script_bodies("伞架", **filters)["rows"]] == [hit["path"]]
    with pytest.raises(Conflict):
        wb.search_script_bodies("另一词", cursor=first["cursor"])
    part = wb.script_excerpt(hit["path"], hit["matchOffset"], expected_sha256=hit["sha256"])
    assert hit["sha256"] == sha256(data).hexdigest() and part["text"] == text[hit["matchOffset"]:]
    chosen = dict(part, match=hit["match"], reason="以物件交代表演")
    saved = wb.save_step_config(eid,"story",mode="method",references=[chosen],expected_revision=0)
    (root/hit["path"]).write_text("伞架换到了门外")
    with pytest.raises(Conflict, match="已变化"):
        wb.script_excerpt(hit["path"], hit["matchOffset"], expected_sha256=hit["sha256"])
    root.rename(tmp_path/"library-moved")
    modified = wb.save_step_config(eid, "story", mode="method", references=[dict(chosen, text="伪造",reason="保留表演节奏")],
                                   goal="重写结尾",expected_revision=saved["revision"])
    assert modified["references"][0]["text"] == part["text"]
    assert modified["references"][0]["reason"] == "保留表演节奏"
    rid=wb.create_task(eid,"story","参考选定表演")["runId"]
    entry=next(f for f in wb.run_manifest(rid)["manifest"]["inputs"] if f["role"]=="citation")
    actual=json.loads(wb.store.read_blob(entry["sha256"]))
    assert actual["text"] == part["text"] and actual["match"] == hit["match"]
    assert wb.step_history(eid,"story")[-1]["references"][0]["reason"] == "以物件交代表演"
    with pytest.raises((Rejected,Conflict)):
        wb.save_step_config(eid,"script",mode="match",references=[chosen],expected_revision=0)


def test_body_search_resumes_after_a_page_without_matches_and_rejects_reindexed_cursor(tmp_path):
    import sqlite3
    env = Env(tmp_path/"app")
    root = tmp_path/"library"
    library(root)
    env.wb.mount_resource("knowledge",str(root))
    docdir = root/"08-方法"
    docdir.mkdir()
    with sqlite3.connect(root/"knowledgeIndex.sqlite") as db:
        for i in range(129):
            name = f"{i}.md"
            (docdir/name).write_text("找得到" if i==128 else "过场")
            db.execute("INSERT INTO documents(path,title,category,kind) VALUES(?,?,?,?)", (f"08-方法/{name}",name,"08-方法","method"))
    first = env.wb.search_script_bodies("找得到")
    assert not first["rows"] and first["cursor"] and first["scanned"]==128
    last = env.wb.search_script_bodies("找得到",cursor=first["cursor"])
    assert last["rows"][0]["title"] == "128.md" and last["cursor"] is None
    with sqlite3.connect(root/"knowledgeIndex.sqlite") as db:
        db.execute("UPDATE documents SET title='新名字' WHERE id=1")
    with pytest.raises(Conflict):
        env.wb.search_script_bodies("找得到",cursor=first["cursor"])
