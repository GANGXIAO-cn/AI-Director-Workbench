"""Exercise both project profiles through normal task/segment-review entry points."""
from hashlib import sha256
import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"server"),str(ROOT/"server/tests")]
from helpers import Env
from workbench.executors.fake import draft
from workbench.methodpack import pack_dir

def collect(wb, request, label, profile, contract):
    rid = request.run_id
    doc=wb.run_manifest(rid)
    assert doc["matches"]
    manifest=doc["manifest"]
    assert manifest["methodPack"]["id"] == contract["id"]
    entries=wb.store.manifest_texts(manifest)
    keys=contract["stages"][label]
    annex=contract["projectFiles"].get(profile,{})
    wanted=[contract["files"].get(key) or annex[key] for key in keys if key in contract["files"] or key in annex]
    actual=[e for e in entries if e["role"] in ("method","project_method")]
    assert [e["sha256"] for e in actual]==[r["sha256"] for r in wanted]
    if profile=="_template":
        texts="\n".join(e["text"] for e in entries if e["text"] is not None)
        assert not re.search(r"milo|leo|puggy|女主人|suno|fourthwall|猫不说",texts,re.I),label
        if contract["id"] not in {"short-film@v1", "short-film@v2"} and any(key not in contract["files"] for key in keys):
            assert "本项目未提供此阶段" in texts, label
    if label=="cold_read":
        assert {e["role"] for e in entries}=={"method","segment_txt","reference_order","image"}
        assert next(e for e in entries if e["role"]=="reference_order")["text"]=="图片1"
    for e in entries:
        assert sha256(wb.store.read_blob(e["sha256"])).hexdigest()==e["sha256"]
    prompt = request.prompt.encode()
    schema = json.dumps(request.schema, ensure_ascii=False).encode()
    attachment_hashes = [sha256(path.read_bytes()).hexdigest() for path in request.images]
    assert attachment_hashes == [e["sha256"] for e in entries if e.get("binary")]
    for e in entries:
        expected = e["text"] if not e.get("binary") else f"（图片，按顺序附在本次请求中，sha256={e['sha256']}）"
        assert f"===== {e['role']} · {e['name']} =====\n{expected}" in request.prompt
    if profile == "_template":
        assert not re.search(r"milo|leo|puggy|女主人|suno|fourthwall|猫不说", request.prompt, re.I), label
    assert doc["fingerprint"] in request.prompt and request.model == manifest["requestedModel"]
    return dict(executorRequest=dict(provider=manifest["provider"],model=request.model,sessionMode=request.session_mode,
                    promptUtf8Bytes=len(prompt),schemaUtf8Bytes=len(schema),promptSha256=sha256(prompt).hexdigest(),
                    schemaSha256=sha256(schema).hexdigest(),imageHashes=attachment_hashes),stage=label,run=rid,inputFingerprint=doc["fingerprint"],textBytes=sum(e["bytes"] for e in entries if not e.get("binary")),
                imageBytes=sum(e["bytes"] for e in entries if e.get("binary")),methods=manifest["methodPack"]["files"],
                inputs=[{k:e[k] for k in ("role","name","bytes","sha256")} for e in entries])

def run(directory, ref="short-film@v7"):
    contract=json.loads((pack_dir(ref)/"manifest.json").read_text())
    env=Env(directory)
    wb=env.wb
    audit={}
    for profile in ["_template","puggy"]:
        pid=wb.create_project(profile,"方法装配对照")["projectId"]
        wb.adopt_pack(pid, ref, profile + ref)
        role = dict(provider="codex",model="gpt-6.1-sol",effort="medium")
        _,eid=env.new_episode(config=profile,project_id=pid,idea="夜班最后一位客人回来取伞",
                              plan={"create":role,"review":role})
        records=[]
        def execute(rid, label):
            env.drain(rid)
            request, = [item for item in env.codex.calls if item.run_id==rid]
            status, = [r for task in wb.task_records(episode_id=eid) for r in task["runs"] if r["id"]==rid]
            assert status["status"]=="succeeded", (label,status["reason"],status["diagnostic"])
            records.append(collect(wb,request,label,profile,contract))
        def create(stage, fields=None):
            if fields:
                values={k:(v if isinstance(v,str) else json.dumps(v,ensure_ascii=False)) for k,v in fields.items()}
                env.codex.script.append(lambda req:draft(req,**values))
            job=wb.create_task(eid,stage,"按本阶段方法起草")
            execute(job["runId"],stage)
            art=env.art(eid,stage)
            version, = [v for v in art["versions"] if v["source_run_id"]==job["runId"]]
            return art["id"],version["id"]
        def adopt(art,version):
            wb.adopt(art,version,None,"adopt-"+version,and_continue=True)
        art,version=create("story")
        execute(wb.submit_review(version,eid+"storyreview")["runId"],"story_review")
        adopt(art,version)
        adopt(*create("script"))
        plan=env.plan86({"G01":[8000]})
        adopt(*create("plan",{"shots":plan["shots"],"segments":plan["segments"]}))
        image=env.image(pid,eid)
        prompts,pv=create("prompts",env.prompts_for(plan,image["versionId"]))
        adopt(prompts,pv)
        for which in ["cold_read","comparison"]:
            execute(wb.start_prompt_review(pv,"G01",which,eid+which)["runId"],which)
        fields=dict(title="The Last Umbrella",body="A passenger returns to the platform.",tags=["umbrella"])
        if profile=="puggy":
            fields.update(suno_title="Night Train",suno_style="quiet, 90 BPM",suno_exclude="vocals")
        create("publish",fields)
        audit[profile]=records
    return audit

if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="short-film@v7")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix="adw-method-inputs-"))
    result=run(root, args.ref)
    output = args.output or ROOT / "修复验证_20261108" / (args.ref.split("@")[1]+"_两类项目运行输入.json")
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print({profile:[(r["stage"],r["textBytes"]) for r in rows] for profile,rows in result.items()})
