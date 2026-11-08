"""Offline source-to-delivery audit. No external calls. Reviews are explicitly simulated."""
import json, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"server"),str(ROOT/"server/tests")]
from helpers import Env
from workbench.storage import sha256_bytes
from workbench.db import connect
def run(parent):
    env=Env(parent); wb=env.wb
    original=Path(json.loads((ROOT/"configs/projects/puggy.json").read_text())["production"]["assetRoot"])
    def load(rel): return json.loads((original/rel).read_bytes())
    card=next(c for c in load("选题池/candidates.json")["candidates"] if c["id"]=="N42")
    role=load("制作基准/角色外观标准.json")["roles"]["milo"]
    image=(original/"角色/MILO.png").read_bytes(); assert sha256_bytes(image)==role["sha256"]
    spec=load("制作基准/视频提示词标准.json")["blocks"]
    exe={r:dict(provider="codex") for r in ["create","review"]}
    result={}
    for label,style in [("A","正午，红白条纹遮阳伞"),("B","雨后，水中的蓝色倒影")]:
        pid=wb.create_project("puggy","原始资料验收")["projectId"]
        catalog=next(c for c in wb.source_topics(pid) if c["code"]==card["id"])
        tid=wb.take_source_topic(pid,card["id"],catalog["sha256"])["topicId"]
        eid=wb.create_episode(pid,"E01")["episodeId"]
        wb.start_episode(eid,"topic",exe,topic_id=tid,spec_overrides={"style":style, "targetDuration":"story_driven"})
        task=wb.create_task(eid,"story","以原卡重写完整短片，使本集画面风格在可见动作中发挥作用。")
        def entries(run):
            return wb.store.manifest_texts(wb.store.load_manifest(f"runs/{run}/input-manifest.json")[0])
        kick=next(v["text"] for v in entries(task["runId"]) if v["role"]=="kickoff")
        assert json.loads(kick.split("原卡全文：\n")[1])==card
        art=env.art(eid,"story")
        story=dict(synopsis=card["summary"],cast="|角色ID|身份与作用|独立身份基准|\n|---|---|---|\n|milo|操作投球器|需要|",handoff=card["hook"])
        sv=wb.save_manual(art["id"],None,story,"save"+eid)["versionId"]
        env.drain(wb.submit_review(sv,"review"+eid)["runId"])
        wb.adopt(art["id"],sv,None,"adopt"+eid,and_continue=True)
        shottext=card["hook"]
        env.save_adopt(eid,"script",dict(scenes=[dict(id="场01",heading="外景 后院",action=shottext)]))
        # Deliberately register a decoy before binding the correct asset.
        decoy=env.image(pid,eid,name="A未选道具"); ref=wb.register_asset(pid,"MILO原始身份图",image,"identity",episode_id=eid)
        product = wb.use_project_product(eid, "15", "choose-product-"+eid)
        yard = original/"episodes/2026-11-02-dog-ball/资产/空景_R2.png"
        scene = wb.register_asset(pid, "历史后院椅背与抱枕", yard.read_bytes(), "scene", episode_id=eid)
        plan=env.plan86({"G01":[8000]})
        plan["shots"][0].update(action=shottext,origin=dict(scene="场01",line=shottext),startAssetId=scene["versionId"])
        env.save_adopt(eid,"plan",plan)
        shot = plan["shots"][0]["id"]
        wb.mark_image(eid, scene["versionId"], "required", [shot])
        wb.save_product_placement(eid,"15",shot,"躺椅靠垫朝向草坪，保留白底和黄衫橘猫肚图案",scene["versionId"],
                                  "对照原主图，后院躺椅椅背可见白底方枕、橘猫条纹和黄色上衣，靠垫在普通陈设尺度下可辨。")
        wb.set_cast(eid,"G01","milo","ok",ref["versionId"])
        pt=wb.create_task(eid,"prompts","按方案写本段提示词")
        material=entries(pt["runId"])
        sources=wb.episode_view(eid)["sourceFiles"]
        for src in sources:
            dig=sha256_bytes((original/src["relpath"]).read_bytes()); assert dig==src["sha256"]
            assert any(v["role"]==src["kind"] and v["sha256"]==dig for v in material)
        content=wb.draft_prompts(eid); txt=content["segments"]["G01"]["txt"]
        assert style in txt and shottext in txt and role["video_zh"] in txt
        for k,v in spec.items(): assert txt.count(v["zh"])==(0 if k=="grid_reference" else 1)
        pv,_=env.save_adopt(eid,"prompts",content)
        for which in ["cold_read","comparison"]:
            rt=wb.start_prompt_review(pv,"G01",which,eid+which)
            mat=entries(rt["runId"])
            assert any(v["role"]=="image" and v["sha256"]==role["sha256"] for v in mat)
            assert not any(v["sha256"]==decoy["sha256"] for v in mat)
            assert {v["role"] for v in mat}.issuperset({"episode_spec","soundStandard","characterStandard","promptStandard"}) if which=="comparison" else not any(v["role"] in {"episode_spec","soundStandard","characterStandard","promptStandard","adopted_story"} for v in mat)
            env.drain(rt["runId"])
        env.save_adopt(eid,"publish",dict(title="One Throw",body="MILO pulls the launcher.",tags=[],suno_title="Throw",suno_style="quiet, 92 BPM",suno_exclude="vocals",cues=[]))
        pub=wb.compose_publish(eid); settings=load("发布设置.json")
        deliver=wb.deliver(eid,"full",eid+"delivery")
        dv=wb.delivery_view(deliver["deliveryId"]); dest=wb.store.root/dv["relpath"]
        assert wb.verify_delivery(deliver["deliveryId"])["ok"]
        packaged=json.loads((dest/"episode.json").read_text())
        assert (dest/"prompts/G01.txt").read_text()==txt
        from historical_baseline import PIN, SKILL
        import subprocess
        for file in ("shot_rhythm", "flat_timeline"):
            rel = "scripts/" + file + ".py"
            assert (SKILL / rel).read_bytes() == subprocess.check_output(["git", "-C", SKILL, "show", PIN + ":" + rel])
        sys.path.insert(0, str(SKILL / "scripts"))
        import shot_rhythm as source_rhythm
        original_report = source_rhythm.analyse(dest, packaged)
        assert original_report == json.loads((dest / "镜头节奏核对.json").read_text())
        assert {sha256_bytes((dest/a["path"]).read_bytes()) for a in packaged["assets"]} == {role["sha256"],scene["sha256"]}
        assert wb.product_placements(eid)["verified"] == 1
        assert "商品入景必留" in (dest/"03_精剪交接.md").read_text() and "not_tested" in (dest/"review.md").read_text()
        assert product["originalSha256"] not in {a["sha256"] for a in packaged["assets"]}
        publish_files=(dest/"01_发布包.md").read_text()
        assert pub["fields"]["title"]==settings["title_prefix"]+"One Throw" and pub["fields"]["title"] in publish_files
        # The frozen format reader expects its historical subfolder layout.
        # Check an exact copy of the bytes the workbench delivered there.
        import publish_delivery as original_publish
        mirror = parent / ("发布格式对照-" + label)
        mirror_path = mirror / "正式交付/01_发布包.md"
        mirror_path.parent.mkdir(parents=True)
        mirror_path.write_bytes((dest / "01_发布包.md").read_bytes())
        parsed_pack = original_publish.load_publish_pack(mirror, required=True)
        snapshot = packaged["workbench"]["page"]
        assert [list(item) for item in parsed_pack["fields"]] == snapshot["publish"]["fields"]
        assert parsed_pack["notes"] == snapshot["publish"]["notes"] and parsed_pack["table"] == snapshot["publish"]["table"]
        assert any(cell["role"] == "milo" and cell["uploaded"] for cell in snapshot["bindings"])
        assert packaged["segments"][0]["references"][0]["shots"] == [shot]
        assert all(line in publish_files for line in settings["body_fixed_lines"])
        assert ", ".join(settings["fixed_tags"]) in publish_files
        assert all("#"+tag in pub["platforms"]["instagram"]["caption"] for tag in settings["fixed_tags"])
        result[label]=dict(project=pid,episode=eid,sourceCard=catalog,storyInput=task["runId"],promptInput=pt["runId"],sources=sources,
                           style=style,txt=txt,imageHash=role["sha256"],delivery=str(dest),reviewMode="模拟；未送外部服务")
    assert result["A"]["txt"].replace(result["A"]["style"],"STYLE")==result["B"]["txt"].replace(result["B"]["style"],"STYLE")
    blank=wb.create_project("_template","项目隔离对照")["projectId"]
    assert wb.source_topics(blank)==[]
    (parent/"evidence.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result
if __name__=="__main__":
    parent=Path(tempfile.mkdtemp(prefix="原始资料本地对照-",dir=ROOT/"data/接手验收"))
    print(parent,flush=True); run(parent); print("PASS: original card, immutable sources, controlled draft effects, review boundaries and packaged bytes")
