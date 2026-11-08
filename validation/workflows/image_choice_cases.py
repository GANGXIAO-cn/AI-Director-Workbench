"""Replay the pinned image tests through user-facing workbench services with their fixture bytes."""
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from historical_baseline import ROOT
sys.path.insert(0, str(ROOT / "server/tests"))
from helpers import Env
from workbench import contactsheet
from workbench.archivefiles import ArchiveFiles, capture


class DetailLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []
        self.links = []
        self.zoom = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.sources.append(attrs["src"])
        if tag == "a":
            self.links.append(attrs["href"])
        if attrs.get("id") == "zoom":
            self.zoom = attrs


def run_original(case):
    result = unittest.TestResult()
    case.run(result)
    assert result.wasSuccessful(), (result.failures, result.errors)


def detail_case(directory, entry, module):
    # The source test creates the path containing spaces and '&' itself. Keep it
    # for the workbench replay to ensure both inspect the very same image.
    inputs = []
    source_build = module.cs.build
    def observe(manifest, output):
        inputs.append(manifest)
        return source_build(manifest, output)
    with patch.object(module.cs, "build", observe):
        run_original(module.DetailPageTests(entry["name"].split(".")[1]))
    manifest, = inputs
    data = json.loads(manifest.read_bytes())
    original = manifest.parent / data["assets"][0]["path"]
    before = original.read_bytes()
    env = Env(directory)
    files, meta = capture(manifest.parent, {"episode.json": manifest.read_bytes()}, env.wb.store, [],
                          manifest.parent, {})
    report = contactsheet.build(data, ArchiveFiles(files, meta, env.wb.store), meta["contactRules"])
    out = directory / "offline"
    contactsheet.export(report, env.wb.store, out)
    index = DetailLinks()
    index.feed((out / "index.html").read_text())
    path, = [v for v in index.links if v.startswith("detail-")]
    detail = DetailLinks()
    detail.feed((out / path).read_text())
    image, = detail.sources
    assert not Path(image).is_absolute() and (out / image).resolve().is_relative_to(out.resolve())
    assert image in detail.links and detail.zoom["min"] == "100" and detail.zoom["max"] == "400"
    assert original.read_bytes() == before == (out / image).read_bytes()
    assert report["review_status"] == "not_tested"
    entry.update(status="原用例与工作台等义断言分别通过", sourceCalls=["cs.build"],
                 workbenchCalls=["contactsheet.build", "contactsheet.export"],
                 scope="原技能链接到本机源文件。工作台链接到可随目录移位的等字节原件；两边均从索引进入细看页。",
                 evidence=dict(sha256=sha256(before).hexdigest(), byteCount=len(before), zoom=[100,400],
                               sourceFile=original.name, packagedFile=image, reviewStatus=report["review_status"]))


def choice_cases(directory, entries, module):
    names = ["test_auto_inventory_no_registration_or_unused_evaluation",
             "test_required_choice_persists_only_selection_and_enforces_use",
             "test_required_bytes_cannot_be_replaced_and_ids_do_not_shift"]
    # Replaying the source tests first also verifies that the original contract is still executable.
    for name in names:
        run_original(module.ImageChoiceTests(name))
    fixture = module.ImageChoiceTests(names[0])
    fixture.setUp()
    env = Env(directory)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    plan = env.plan86({"01":[10000]})
    env.save_adopt(eid, "plan", plan)
    def register(name, raw):
        return wb.register_asset(pid, name, raw, "scene", episode_id=eid)
    used, unused = [register(name, (fixture.root/name).read_bytes()) for name in ("used.png","unused.png")]
    def adopt_ref(image, key):
        pv, _ = env.save_adopt(eid, "prompts", env.prompts_for(plan, image["versionId"]), key)
        env.review_segments(pv, ["01"], key)
    adopt_ref(used, "red")
    env.save_adopt(eid, "publish", dict(title="Image choices", body="The scene.", tags=["scene"],
                                        suno_title="Room", suno_style="Instrumental", suno_exclude="vocals"))
    initial = wb.deliver(eid,"full","no-selection")
    assert wb.verify_delivery(initial["deliveryId"])["ok"]
    # Equivalent to the original CLI's rejection of its obsolete option: invoke
    # the workbench HTTP delivery check as a client, with and without the option.
    from fastapi.testclient import TestClient
    from workbench.app import create_app
    flag_case = "test_removed_review_flag_is_rejected"
    run_original(module.ImageChoiceTests(flag_case))
    with TestClient(create_app(wb,inline_worker=True)) as client:
        args = {"episode_id":eid}
        normal = client.get("/api/q/delivery_problems",params={"args":json.dumps(args)})
        assert normal.status_code == 200 and normal.json()[0] == []
        invalid = client.get("/api/q/delivery_problems",params={"args":json.dumps(dict(args,require_reviewed=True))})
        assert invalid.status_code == 422 and "require_reviewed" in invalid.json()["error"]
    flag_entry = next(c for c in entries if c["name"].endswith("."+flag_case))
    flag_entry.update(status="原用例与工作台等义断言分别通过",sourceCaseExecuted=True,
        workbenchCalls=["GET /api/q/delivery_problems"], scope="源 CLI 拒绝 --require-reviewed；工作台交付检查拒绝相应的旧参数 require_reviewed。",
        evidence=dict(withoutFlag=normal.status_code,withRemovedFlag=invalid.status_code,error=invalid.json()["error"]))
    def gallery(package):
        return json.loads((Path(package["path"])/"episode.json").read_bytes())["workbench"]["page"]
    page = gallery(initial)
    assert not page["requiredImages"] and len(page["images"]) == 2
    blue, = [i for i in page["images"] if i["id"] == unused["versionId"]]
    assert not blue["marks"] and not blue["segments"]
    assert (Path(initial["path"])/blue["path"]).read_bytes() == (fixture.root/"unused.png").read_bytes()
    html = Path(initial["html"]).read_text()
    assert 'data-pick="'+blue["id"]+'"' in html and "unused.png" in html and "尚未记录" not in html
    details = [dict(candidates=len(page["images"]), required=0, unselected=unused["sha256"], offlineVerified=True)]
    wb.mark_image(eid,unused["versionId"],"required",requirement=dict(reason="用户明确选择", targetSegments=["01"]))
    def problems():
        return [p["problem"] for p in wb.delivery_problems(eid)[0]]
    assert any("必用图未落实" in p for p in problems())
    adopt_ref(unused, "blue")
    assert not problems()
    selected = wb.deliver(eid,"full","one-selection")
    rule, = gallery(selected)["requiredImages"]
    assert rule["sha256"] == unused["sha256"] and rule["usedHere"] == rule["requiredHere"] == ["01"]
    assert rule["reason"] == "用户明确选择" and rule["selectedBy"] == "user"
    details.append(dict(required=1, missingUseRejected=True, acceptedAfterUse=rule["sha256"], segments=rule["usedHere"]))
    # Apply the third source case to the two original colours. A newly registered
    # version can coexist with the required one. Damage only the isolated fixture blob.
    wb.mark_image(eid,unused["versionId"],"required",enabled=False)
    wb.mark_image(eid,used["versionId"],"required",requirement=dict(reason="保留原图", targetSegments=["01"]))
    stable = unused["versionId"]
    env.image(pid,eid,"aaa.png",size=(9,16),rgb=(0,128,0))
    white = env.image(pid,eid,"used.png",size=(9,16),rgb=(255,255,255))
    assert next(i["id"] for i in wb.episode_assets(eid) if i["name"]=="unused.png") == stable
    adopt_ref(white, "replacement")
    assert any("必用图未落实" in p for p in problems())
    wb.store.blob_path(used["sha256"]).write_bytes(wb.asset_bytes(white["versionId"]))
    assert any("必用原版本不存在" in p for p in problems())
    details.append(dict(stableIdAfterInsert=True, original=used["sha256"], replacement=white["sha256"],
                        replacementRejected=True, damagedOriginalDetected=True))
    operations = [["episode_assets", "deliver", "verify_delivery"], ["mark_image", "delivery_problems", "adopt", "deliver"],
                  ["mark_image", "register_asset", "episode_assets", "adopt", "delivery_problems"]]
    for name, native, proof in zip(names, operations, details, strict=True):
        entry = next(c for c in entries if c["name"].endswith("."+name))
        entry.update(status="原用例与工作台等义断言分别通过", sourceCaseExecuted=True, workbenchCalls=native,
                     scope="原技能从集目录盘点，工作台从本集已登记素材盘点。使用原用例的两张图片字节，核对选择、正式输入与交付。",
                     evidence=proof)


def extra_candidate_case(directory, entry, module):
    import test_h3_rules
    inputs = []
    three = test_h3_rules.H3Tests.three
    def observe(fixture):
        three(fixture)
        inputs.append(fixture)
    with patch.object(test_h3_rules.H3Tests, "three", observe):
        run_original(module.ImageChoiceTests(entry["name"].split(".")[1]))
    source, = inputs
    env = Env(directory)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    segments = source.data["segments"]
    strategies = {s["id"]:s["reference_strategy"] for s in segments}
    plan = env.plan86({s["id"]:[1000*s["duration_s"]] for s in segments}, strategies)
    action = "固定近景看着桌上的杯子。"
    env.save_adopt(eid, "script", dict(scenes=[dict(id="场01", heading="日间 室内", action=action)]), "cup-script")
    for shot in plan["shots"]:
        shot.update(action=action, camera="固定近景", origin=dict(scene="场01",line=action))
    env.save_adopt(eid, "plan", plan)
    assets = {}
    for asset in source.data["assets"]:
        grid = asset["category"] == "grid"
        assets[asset["id"]] = wb.register_asset(pid, asset["path"], (source.root/asset["path"]).read_bytes(),
            asset["category"], episode_id=eid, grid=grid, cells=[dict(cell=i) for i in range(1,10)] if grid else None)
    extra = wb.register_asset(pid, "extra.png", (source.root/"extra.png").read_bytes(),"scene",episode_id=eid)
    content = env.prompts_for(plan, {s["id"]:assets[s["references"][0]["asset_id"]]["versionId"] for s in segments},
                              {s["id"]:(source.root/s["prompt"]).read_text() for s in segments})
    for s in segments:
        value = content["segments"][s["id"]]
        value["prompt_format"] = "readable_v3"
        value["refs"][0]["grid"] = strategies[s["id"]] == "grid"
    version, _ = env.save_adopt(eid, "prompts", content)
    env.review_segments(version, strategies)
    env.save_adopt(eid, "publish", dict(title="The cup", body="An empty room.", tags=["room"],
                                        suno_title="Room", suno_style="Instrumental", suno_exclude="vocals"))
    assert not wb.delivery_problems(eid)[0]
    delivery = wb.deliver(eid, "full", "full")
    assert wb.verify_delivery(delivery["deliveryId"])["ok"]
    root = Path(delivery["path"])
    result = json.loads((root/"episode.json").read_bytes())
    page = result["workbench"]["page"]
    assert not page["requiredImages"] and "extra.png" in Path(delivery["html"]).read_text()
    candidate, = [r for r in page["images"] if r["id"] == extra["versionId"]]
    assert not candidate["segments"] and not candidate["marks"]
    assert (root/candidate["path"]).read_bytes() == (source.root/"extra.png").read_bytes()
    for s, original in zip(result["segments"], segments, strict=True):
        assert s["strategy"] == strategies[s["id"]]
        assert (root/s["prompt"]).read_bytes() == (source.root/original["prompt"]).read_bytes()
    entry.update(status="原用例与工作台等义断言分别通过", sourceCalls=["validate_episode","render_delivery","check_delivery"],
                 workbenchCalls=["delivery_problems","deliver","verify_delivery"],
                 scope="相同两段原 TXT、九宫格、自由参考和额外未选图。两边分别验清单、渲染整集和验交付。",
                 evidence=dict(strategies=strategies, extraImage=extra["sha256"], extraIsFormalInput=False,
                               required=0, offlineVerified=True))
