"""Replay frozen role / shot field changes through the historical import and recheck APIs."""
from pathlib import Path
import json
import shutil
import sys

from historical_baseline import ROOT
sys.path.insert(0,str(ROOT/"server/tests"))
from helpers import Env
from image_choice_cases import run_original
from workbench.db import transaction
from workbench import shotrefs


def receipt_cases(directory, by_file):
    import test_character_integration as character
    import test_shot_references as shots
    from test_support import ASSETS

    env = Env(directory/"service")
    wb = env.wb
    base = directory/"series"
    shutil.copytree(ASSETS,base)
    episodes = base/"episodes"
    episodes.mkdir()
    pid = wb.create_project("_template","单集角色与分镜核验")["projectId"]
    with transaction(wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?",(json.dumps(dict(assetRoot=str(base),
                   promptStandard="制作基准/视频提示词标准.json",characterStandard="制作基准/角色外观标准.json")),pid))
    def source_case(cls, name):
        run_original(cls(name))
        fixture = cls(name)
        fixture.setUp()
        fixture.three()
        return fixture

    def record(module,method,checks):
        case = next(c for c in by_file["tests/"+module+".py"] if c["name"].endswith("."+method))
        case.update(status="原用例与工作台等义断言分别通过", sourceCalls=["原清单验证、HTML 生成与检查入口",method],
                    workbenchCalls=["import_historical_episode","historical_receipt"],
                    scope="在原技能执行完整用例，再以相同夹具通过工作台历史导入与重算，核对原页、清单、原图、逐段引用和故障结论。",
                    evidence=checks)

    def ingest(name):
        imported = wb.import_historical_episode(pid,name)
        checked = wb.historical_receipt(pid,imported["receipt"])
        assert checked["integrity"]["ok"] and checked["materialRechecked"]==imported["materialChecks"]
        assert checked["rechecked"]==imported["checks"]
        return imported,imported["materialChecks"]

    method = "test_page_cannot_drop_or_swap_role_image"
    fixture = source_case(character.CharacterIntegration,method)
    name="角色"
    folder=episodes/name
    shutil.copytree(fixture.root,folder)
    page=folder/"正式交付/00_图文对照.html"
    html=page.read_text()
    positive, material=ingest(name)
    assert material["pageContent"]["state"]=="passed",material["pageContent"]
    assert material["cast"]["state"]=="passed"
    from render_delivery import render_cast
    import os
    from urllib.parse import quote
    fragment=render_cast(fixture.data,lambda path:quote(os.path.relpath(folder/path,page.parent),safe="/"))
    assert html.count(fragment)==1
    faults=[]
    for bad in [fragment.replace("<img ","<span "),fragment.replace("女孩","错误角色"),fragment.replace("出镜段：01、02","出镜段：01")]:
        page.write_text(html.replace(fragment,bad))
        receipt,material=ingest(name)
        rule=material["pageContent"]
        assert rule["state"]=="failed" and "页面角色设定" in rule["message"]
        assert all(s["state"]=="passed" for s in rule["details"]["segments"])
        assert receipt["checks"]["delivery"]["state"]=="failed",receipt["checks"]
        faults.append(rule["details"]["issues"])
    assert wb.historical_receipt(pid,positive["receipt"])["materialRechecked"]["pageContent"]["state"]=="passed"
    record("test_character_integration",method,dict(positive=positive["materialChecks"]["pageContent"]["details"],faults=faults))

    method="test_three_entrypoints_and_visible_html_scope"
    fixture=source_case(shots.ShotReferences,method)
    name="镜头";folder=episodes/name;shutil.copytree(fixture.root,folder)
    positive,material=ingest(name)
    assert material["pageContent"]["state"]==material["shotReferences"]["state"]=="passed",material
    page=folder/"正式交付/00_图文对照.html"
    html=page.read_text();old="作用镜头：S012</p>"
    assert html.count(old)==1
    page.write_text(html.replace(old,"作用镜头：S011、S012</p>"))
    changed,material=ingest(name); detail=material["pageContent"]["details"]
    assert material["shotReferences"]["state"]=="passed" and changed["checks"]["delivery"]["state"]=="failed"
    assert [(s["segment"],s["textState"],s["state"]) for s in detail["segments"]]==[("01","passed","failed"),("02","passed","passed")]
    assert "页面作用镜头" in detail["issues"][0]["message"]
    record("test_shot_references",method,dict(positive=positive["materialChecks"]["pageContent"]["details"],faults=detail["issues"]))

    def save_manifest(data):
        (folder/"episode.json").write_text(json.dumps(data))
    page.write_text(html)
    method="test_girl_only_appears_in_one_shot"
    run_original(shots.ShotReferences(method))
    data=json.loads((folder/"episode.json").read_bytes())
    data["segments"][0]["references"][1]["shots"]=["S011","S012","S013"]
    save_manifest(data)
    changed,material=ingest(name)
    assert "身份图作用镜头" in material["shotReferences"]["message"]
    assert changed["checks"]["manifest"]["state"]==changed["checks"]["delivery"]["state"]=="failed"
    archive=wb._historical_files(pid,changed["receipt"])
    suggested=shotrefs.suggestions(archive.text,data)["01"]["girl"]
    assert suggested==["S012"]
    record("test_shot_references",method,dict(suggested=suggested,failed=material["shotReferences"],delivery=changed["checks"]["delivery"]))
    method="test_missing_marker_rejected"
    run_original(shots.ShotReferences(method))
    data=json.loads(fixture.manifest.read_bytes())
    del data["shot_reference_version"]
    save_manifest(data)
    changed,material=ingest(name)
    assert material["shotReferences"]["state"]=="failed" and "shot_reference_version" in material["shotReferences"]["message"]
    assert changed["checks"]["manifest"]["state"]==changed["checks"]["delivery"]["state"]=="failed"
    record("test_shot_references",method,dict(failed=material["shotReferences"],manifest=changed["checks"]["manifest"]))

    method="test_evidence_invalidated_on_board_image_or_review_change"
    run_original(shots.ShotReferences(method))
    save_manifest(fixture.data)
    _,before=ingest(name)
    sig=before["promptReview"]["details"]
    fixture.rows[1][shots.PROPS]="crate=装满"
    fixture.board()
    shutil.copyfile(fixture.root/"场面与制作方案.md",folder/"场面与制作方案.md")
    _,board=ingest(name)
    assert before["reviews"]==board["reviews"] and sig==board["promptReview"]["details"]
    fixture.capture()
    for path in ("review.md","evidence.json"):
        shutil.copyfile(fixture.root/path,folder/path)
    (folder/"看图记录.md").write_text("合成夹具，身份和场景分开。\n换图意见")
    _,after=ingest(name)
    assert after["shotReferences"]["state"]==after["pageContent"]["state"]==after["promptReview"]["state"]=="passed",after
    fixture.data["segments"][0]["references"][0]["visual_review"]["sha256"]="stale"
    save_manifest(fixture.data)
    changed,after=ingest(name)
    assert after["shotReferences"]["state"]=="failed" and "图片版本" in after["shotReferences"]["message"]
    record("test_shot_references",method,dict(sameReviewInputs=sig,failed=after["shotReferences"]))

    method="test_role_metadata_does_not_propagate_but_changed_input_fails"
    fixture=source_case(character.CharacterIntegration,method)
    name="角色改名";folder=episodes/name;shutil.copytree(fixture.root,folder)
    positive,original=ingest(name)
    fixture.data["cast"]["girl"]["name"]="另名"
    fixture.save()
    fixture.three()  # Regenerate the changed visible name using the source fixture.
    save_manifest(fixture.data)
    shutil.copyfile(fixture.page,folder/"正式交付/00_图文对照.html")
    renamed,clean=ingest(name)
    assert clean["reviews"]==original["reviews"] and clean["promptReview"]==original["promptReview"]
    assert clean["pageContent"]["details"]["roles"][0]["name"]=="另名"
    from PIL import Image
    from hashlib import sha256
    Image.new("RGB",(9,16),"green").save(folder/"girl.png")
    digest=sha256((folder/"girl.png").read_bytes()).hexdigest()
    fixture.data["assets"][-1]["sha256"]=digest
    fixture.data["segments"][0]["references"][-1]["visual_review"]["sha256"]=digest
    save_manifest(fixture.data)
    changed,after=ingest(name)
    assert after["promptReview"]["state"]=="failed" and "01" in after["promptReview"]["message"]
    assert changed["checks"]["manifest"]["state"]==changed["checks"]["delivery"]["state"]=="failed"
    record("test_character_integration",method,dict(nameAfter="另名",originalInputs=original["promptReview"]["details"],
           changedImage=digest,failed=after["promptReview"]))

    method="test_three_entrypoints_and_missing_identity"
    run_original(character.CharacterIntegration(method))
    data=json.loads(fixture.manifest.read_bytes())
    shutil.copyfile(fixture.root/"girl.png",folder/"girl.png")
    data["segments"][0]["identity_bindings"]={}
    save_manifest(data)
    bad,after=ingest(name)
    assert "身份绑定" in after["cast"]["message"] and after["cast"]["state"]=="failed"
    assert bad["checks"]["manifest"]["state"]==bad["checks"]["delivery"]["state"]=="failed"
    record("test_character_integration",method,dict(before=positive["materialChecks"]["cast"],
           failed=after["cast"],delivery=bad["checks"]["delivery"]))

    from test_h3_rules import H3Tests
    from test_flat_timeline import FlatTests
    method="test_page_and_second_segment_without_cli_flag"
    fixture=source_case(H3Tests,method)
    name="声音";folder=episodes/name;shutil.copytree(fixture.root,folder)
    spoken=fixture.texts[0].replace("桌上的杯子。","桌上的杯子。画外两人 (S1,S2) 齐声 <d>[English] Stop!</d>")
    (fixture.root/"flex.txt").write_text(spoken)
    fixture.capture()
    fixture.three()
    def copy_fixtures():
        for path in ["episode.json","review.md","evidence.json","flex.txt","prompt.txt","正式交付/00_图文对照.html"]:
            shutil.copyfile(fixture.root/path,folder/path)
    copy_fixtures()
    good,material=ingest(name)
    assert material["pageContent"]["state"]==material["h3Sound"]["state"]==material["prompts"]["state"]=="passed"
    old_page=good["files"]["正式交付/00_图文对照.html"]
    (folder/"prompt.txt").write_text(fixture.texts[1].replace("non_diegetic_music: N/A",""))
    bad,failed=ingest(name)
    assert "漏写" in failed["h3Sound"]["details"]["segments"][1]["message"]
    assert failed["pageContent"]["details"]["segments"][1]["textState"]=="failed" and bad["files"]["正式交付/00_图文对照.html"]==old_page
    (folder/"prompt.txt").write_text(fixture.texts[1])
    (folder/"flex.txt").write_text(spoken.replace("起镜，","起镜，S3 "))
    _,illegal=ingest(name)
    assert "内部镜头编号" in illegal["prompts"]["message"]
    record("test_h3_rules",method,dict(originalPage=old_page,second=failed["h3Sound"],internalShot=illegal["prompts"]))

    method="test_marker_required_and_txt_direct_comparison"
    run_original(H3Tests(method))
    copy_fixtures()
    data=json.loads(fixture.manifest.read_bytes())
    diagnostics=[]
    for marker in [None,"v2"]:
        if marker is None:
            del data["h3_rules"]
        else:
            data["h3_rules"]=marker
        save_manifest(data)
        bad,failed=ingest(name)
        assert bad["checks"]["manifest"]["rule"]==bad["checks"]["delivery"]["rule"]=="h3_rules"
        diagnostics.append(bad["checks"]["manifest"])
    data["h3_rules"]="v1"
    data["segments"][1]["native_bgm"]="generate"
    save_manifest(data)
    _,failed=ingest(name)
    assert "native_bgm=generate" in failed["h3Sound"]["details"]["segments"][1]["message"]
    fixture.data=data
    (fixture.root/"prompt.txt").write_text(fixture.texts[1].replace("N/A","低音弦乐匀速渐入"))
    shutil.copyfile(fixture.root/"prompt.txt",folder/"prompt.txt")
    _,stale=ingest(name)
    assert stale["h3Sound"]["state"]=="passed" and stale["promptReview"]["state"]=="failed"
    fixture.capture()
    fixture.three()
    copy_fixtures()
    _,updated=ingest(name)
    assert updated["h3Sound"]["state"]==updated["promptReview"]["state"]==updated["pageContent"]["state"]=="passed"
    record("test_h3_rules",method,dict(marker=diagnostics,changedWithoutReview=stale["promptReview"],reviewed=updated["h3Sound"]))

    method="test_sound_field_matrix_at_every_entry"
    run_original(H3Tests(method))
    fixture=H3Tests()
    fixture.setUp()
    fixture.three()
    copy_fixtures()
    field="non_diegetic_music: N/A"
    matrix=[]
    for seg,text in zip(fixture.data["segments"],fixture.texts):
        path=folder/seg["prompt"]
        for value,message in [(field+"\n"+field,"重复"),("non_diegetic_music:\nN/A","同行"),("non_diegetic_music:","同行"),
                              ("","漏写"),("non_diegetic_music: 弦乐","要求"),(field+"\noverall_soundscape:\nN/A","full_silence"),
                              (None,"错栏")]:
            path.write_text(text.replace(field,value) if value is not None else field+"\n"+text.replace(field,""))
            bad,failed=ingest(name)
            result=next(r for r in failed["h3Sound"]["details"]["segments"] if r["segment"]==seg["id"])
            assert result["state"]=="failed" and message in result["message"] and bad["checks"]["delivery"]["state"]=="failed"
            matrix.append(dict(segment=seg["id"],error=result["message"]))
        path.write_text(text)
    record("test_h3_rules",method,dict(mutations=matrix))

    method="test_normal_delivery_and_plan_edit_has_no_review_expiry"
    run_original(FlatTests(method))
    copy_fixtures()
    _,before=ingest(name)
    board=folder/"场面与制作方案.md"
    board.write_text(board.read_text()+"\n无关排版说明。")
    _,after=ingest(name)
    assert before["reviews"]==after["reviews"] and before["promptReview"]==after["promptReview"]
    assert after["pageContent"]["state"]==after["rhythm"]["state"]=="passed"
    data=json.loads((folder/"episode.json").read_bytes())
    del data["shot_rhythm"]
    save_manifest(data)
    bad,failed=ingest(name)
    assert bad["checks"]["manifest"]["state"]==bad["checks"]["delivery"]["state"]=="failed"
    assert failed["rhythm"]["state"]=="failed" and "shot_rhythm" in failed["rhythm"]["message"]
    record("test_flat_timeline",method,dict(reviewed=after["promptReview"],missingRhythm=failed["rhythm"]))
