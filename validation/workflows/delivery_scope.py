"""Compare static delivery scope with the fixed skill and inspect a real local H3 ZIP."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

from historical_baseline import ROOT, SKILL, PIN

sys.path[:0] = [str(ROOT/"server"), str(ROOT/"server/tests"), str(SKILL/"tests")]
from helpers import Env
from workbench.deliverypage import verify as verify_page
from workbench.errors import Conflict, Rejected


def audit():
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","tests","templates"],check=True)
    from test_character_integration import CharacterIntegration
    from validate_episode import validate
    fixture = CharacterIntegration()
    fixture.setUp()
    fixture.three()
    original = validate(fixture.manifest)
    assert original["video_check"] == {"status":"not_tested"}
    work = Path(tempfile.mkdtemp(prefix="adw-trial-scope-"))
    env = Env(work)
    wb = env.wb
    episode = env.full_episode(segments={"G01":[6000],"G02":[7000],"G03":[8000]}, deliver=False)
    eid = episode["eid"]
    cases = {}
    def match(name, source, native):
        messages = []
        for fn in (source, native):
            try:
                fn()
            except (ValueError, Rejected) as exc:
                messages.append(str(exc))
        assert len(messages) == 2 and messages[0] == messages[1], (name,messages)
        cases[name] = dict(matchedRule=True,sourceOK=False,workbenchOK=False,issue=messages[0])
    match("scope", lambda:validate(fixture.manifest,scope="unknown"), lambda:wb.delivery_problems(eid,"unknown"))
    handoff = fixture.root / "正式交付/03_精剪交接.md"
    handoff.write_text("  ")
    match("empty_handoff",lambda:validate(fixture.manifest), lambda:verify_page(handoff.parent,{"scope":"full"},{handoff.name:""}))
    # CRLF and a final newline must survive the bridge through the packager's text reader.
    src = env.prompts_for(episode["plan"],episode["img"]["versionId"])
    src["segments"]["G03"]["txt"] = src["segments"]["G03"]["txt"].replace("\n","\r\n") + "\r\n"
    pv,_ = env.save_adopt(eid,"prompts",src,"windows-text")
    env.review_segments(pv,["G01","G02","G03"])
    result = wb.deliver(eid,"trial","trial-noncontiguous",["G01","G03"])
    root = Path(result["path"])
    manifest=json.loads((root/"episode.json").read_text())
    budget=json.loads((root/"分段与时长预算.json").read_text())
    assert manifest["video_check"] == original["video_check"]
    assert [s["start"] for s in manifest["segments"]] == ["00:00.000","00:06.000"]
    assert [s["episode_start_ms"] for s in budget["segments"]] == [0,13000]
    assert manifest["design_duration_s"] == 14 and budget["total_ms"] == 14000
    def pack():
        return wb.package(result["deliveryId"], ["G01","G03"], "same-zip")
    with ThreadPoolExecutor(2) as pool:
        outputs = list(pool.map(lambda _:pack(), range(2)))
    assert outputs[0] == outputs[1]
    package = outputs[0]
    assert len(list(Path(package["path"]).parent.glob("*.h3project.zip"))) == 1
    with ZipFile(package["path"]) as z:
        document = json.loads(z.read("project.json"))
    target = root/manifest["segments"][1]["prompt"]
    assert document["segments"][1]["prompt"].encode() == target.read_bytes()
    for fn in (lambda:wb.package(result["deliveryId"], ["G01"], "same-zip"),
               lambda:wb.deliver(eid,"full","trial-noncontiguous")):
        try:
            fn()
        except Conflict:
            continue
        raise AssertionError("同一命令号不能指向不同范围")
    target.write_bytes(target.read_bytes().replace("阿福".encode(),"路人".encode()))
    try:
        wb.package(result["deliveryId"],["G03"],"different-command")
    except Rejected as exc:
        assert exc.detail[0]["file"] == manifest["segments"][1]["prompt"]
        edited = exc.detail[0]
    else:
        raise AssertionError("改写过的 TXT 被错误出包")
    return dict(sourceCommit=PIN, sourceVideoStatus=original["video_check"],workbenchVideoStatus=manifest["video_check"],
                cases=cases, selected=["G01","G03"],packageStart=[s["start"] for s in manifest["segments"]],
                episodeStartMs=[s["episode_start_ms"] for s in budget["segments"]],designSeconds=manifest["design_duration_s"],
                duplicateCommands=2,registeredPackages=1,scope=document["scope"],zipSha256=sha256(Path(package["path"]).read_bytes()).hexdigest(),
                exactPromptBytes=True,changedPrompt=edited)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args=parser.parse_args()
    result=audit()
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(result)
