"""Write each H3 segment in its own request and checkpoint validated replies."""
import json
from pathlib import Path

from . import contracts, prompts
from .decisions import check_decision_notes
from .db import connect, dumps, transaction
from .executors.base import ExecOutcome, ExecRequest
from .officialh3 import has_unquoted_chinese
from .promptassembly import assemble
from .replyformat import bind_writing_reply, document_to_internal
from .stages import WRITABLE, check_segment_prompt, normalize
from .storage import canonical, sha256_bytes


def scoped_entries(entries, sid, segment, layout):
    refs = [ref["assetVersionId"] for ref in segment["refs"]]
    images = {}
    result = []
    for e in entries:
        role = e["role"]
        if e.get("binary"):
            images[Path(e["name"]).stem] = e
            continue
        if role.startswith("adopted_") or role in {"required_images"}:
            continue
        if role in {"prompt_scaffold", "material_use_conclusions", "plan_layout", "shot_rhythm",
                    "script_scene_source", "cast_bindings", "episode_assets", "dialogue_timing"}:
            data = json.loads(e["text"])
            if role in {"prompt_scaffold", "material_use_conclusions"}:
                data = {sid:data.get(sid)}
            elif role == "plan_layout":
                data = dict(layout=dict(segments=[layout]), shots=[s for s in data["shots"] if s["segment"] == sid])
            elif role == "script_scene_source":
                scenes = data["segments"].get(sid, [])
                data = dict(segments={sid:scenes}, scenes={s["sceneId"]:data["scenes"].get(s["sceneId"]) for s in scenes})
            elif role == "cast_bindings":
                data["rows"] = [r for r in data["rows"] if r["segment"] == sid]
            elif role == "episode_assets":
                data = [a for a in data if a["assetVersionId"] in refs]
            elif role == "shot_rhythm":
                data = [r for r in data if r["段ID"] == sid]
            else:
                data = [r for r in data.get("rows", []) if r["segment"] == sid]
            e = dict(e, text=dumps(data))
        result.append(e)
    return result + [images[vid] for vid in refs if vid in images]


def segment_map(value, sid):
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        item = value[0]
        if item.get("id") == sid:
            value = {sid: {key: field for key, field in item.items() if key != "id"}}
    if not isinstance(value, dict) or list(value) != [sid] or not isinstance(value[sid], dict):
        raise ValueError("仅提交本段 " + sid + "，用段号作键，或在唯一条目中填写 id")
    return value


def segment_decision_errors(fields, sid, scaffold, decisions, plan):
    notes = fields.get("decisionNotes", [])
    if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
        return ["decisionNotes 须为记录数组"]
    # The request is bound to one segment; the writer need not repeat its ID.
    for note in notes:
        note["segmentId"] = sid
    if not decisions:
        return []
    entry = assemble(fields["segments"][sid], scaffold)
    return check_decision_notes(decisions, "prompts", {"segments": {sid: entry}, "decisionNotes": notes}, plan)


def combined_fields(parts):
    fields = {"segments": {}}
    for part in parts:
        for key, value in part.items():
            if key == "segments":
                fields[key].update(value)
            elif isinstance(value, list):
                items = fields.setdefault(key, [])
                for item in value:
                    if item not in items:
                        items.append(item)
            elif value:
                fields[key] = (fields.get(key, "") + "\n" + value).strip()
    if "productionNoteResponses" in fields:
        from .productionnotes import merge_segment_responses
        fields["productionNoteResponses"] = merge_segment_responses(fields["productionNoteResponses"])
    return fields


def write_segments(wb, run, task, manifest, entries):
    rid, tid = run["id"], task["id"]
    basis = sha256_bytes(canonical([(e["role"], e["name"], e["sha256"]) for e in entries]))
    values = {e["role"]:e for e in entries}
    scaffold = json.loads(values["prompt_scaffold"]["text"])
    plan = json.loads(values["plan_layout"]["text"])
    layouts = plan["layout"]["segments"]
    decisions = json.loads(values["director_decisions"]["text"]) if "director_decisions" in values else []
    document = wb._document_mode(run, task)
    report_entry = values.get("production_note_assignments")
    report = json.loads(report_entry["text"])["reportVersionId"] if report_entry else None
    parts = []
    outcome = ExecOutcome(ok=True)
    for number, layout in enumerate(layouts, 1):
        sid = layout["id"]
        with transaction(wb.db_path) as db:
            if db.execute("SELECT status FROM run WHERE id=?", (rid,)).fetchone()[0] != "running":
                return []
            db.execute("INSERT OR REPLACE INTO prompt_segment_progress VALUES(?,?,?,?)",(rid,basis,sid,len(layouts)))
            prior = db.execute("SELECT fields FROM prompt_segment_result WHERE task_id=? AND basis=? AND segment=?",
                               (tid,basis,sid)).fetchone()
        if prior:
            saved = json.loads(prior[0])
            if not segment_decision_errors(saved, sid, scaffold[sid], decisions, plan):
                parts.append(saved)
                continue
        scoped = scoped_entries(entries, sid, scaffold[sid], layout)
        trailer = prompts.draft_trailer("prompts",scoped,
                                        mode="document" if document else "changes")
        trailer += f"\n本次只起草 {sid}，segments 只含这一段。txt 为完整官方 H3 英文正文，原语言对白和画面文字用引号。按本段冻结的镜头、时间和图片顺序编写。"
        prompt = prompts.prompt_from(scoped, trailer, "create", document=document)
        work = wb.store.run_dir(rid) / f"segment-{number:02d}" / "work"
        work.mkdir(parents=True, exist_ok=True)
        images = []
        for e in scoped:
            if e.get("binary"):
                # Stored blobs are immutable. Link image filenames without duplicating media.
                image = work / Path(e["name"]).name
                if not image.exists():
                    image.hardlink_to(wb.store.blob_path(e["sha256"]).resolve())
                images.append(image)
        request = ExecRequest(rid,"create","draft",prompt,None if document else contracts.writing_schema(),
                              run["session_mode"],work,run["requested_model"],run["requested_effort"],
                              int(wb.get_setting("executor_timeout",600)),images,manifest.get("executorConnection",{}))
        for attempt in (1,2):
            with connect(wb.db_path) as db:
                if db.execute("SELECT status FROM run WHERE id=?", (rid,)).fetchone()[0] != "running":
                    return []
            request.attempt = attempt
            outcome = wb.executors[run["provider"]].start(request)
            if not outcome.ok and outcome.error_class != "schema":
                return wb._finish(rid,"waiting_quota" if outcome.error_class=="quota" else "failed",
                                  reason=outcome.error_class,diagnostic=f"{sid}：{outcome.diagnostic}",outcome=outcome)
            try:
                if not outcome.ok:
                    raise ValueError(outcome.diagnostic)
                result = outcome.structured
                if document:
                    result = document_to_internal(run,task,result,None,WRITABLE["prompts"])
                else:
                    result = bind_writing_reply(run, task, result)
                if result.get("kind") != "draft" or result.get("baseVersionId") is not None:
                    raise ValueError("须提交提示词首稿")
                fields = {}
                for c in result["changes"]:
                    key = c["field"]
                    if key not in WRITABLE["prompts"] or key in fields or c["targetId"] != task["artifact_id"]:
                        raise ValueError("稿件字段或目标不符")
                    fields[key] = normalize("prompts",key,c["value"])
                if report and isinstance(fields.get("productionNoteResponses"), list):
                    fields["productionNoteResponses"] = [dict(item, reportVersionId=report) if isinstance(item, dict) else item
                                                         for item in fields["productionNoteResponses"]]
                fields["segments"] = segment_map(fields.get("segments"), sid)
                entry = assemble(fields["segments"][sid], scaffold[sid])
                errors = [p["problem"] for p in check_segment_prompt(sid,entry,layout["duration_ms"],layout["strategy"],layout["shots"])]
                errors.extend(segment_decision_errors(fields, sid, scaffold[sid], decisions, plan))
                if has_unquoted_chinese(entry.get("txt","")):
                    errors.append("正文须为英文，仅原语言对白和画面文字可保留中文")
                with connect(wb.db_path) as db:
                    errors.extend(wb._prompt_reference_errors(db,task["episode_id"],{sid:entry},require_observations=False))
                if errors:
                    raise ValueError("；".join(errors))
                break
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                if attempt == 2:
                    return wb._finish(rid,"failed",reason="分段提示词校验失败",diagnostic=f"{sid}：{exc}",outcome=outcome)
                request.prompt = wb._format_repair_prompt(prompt,dumps(outcome.structured or {}),[str(exc)])
        with transaction(wb.db_path) as db:
            if db.execute("SELECT status FROM run WHERE id=?", (rid,)).fetchone()[0] != "running":
                return []
            db.execute("INSERT INTO prompt_segment_result VALUES(?,?,?,?) ON CONFLICT(task_id,basis,segment) DO UPDATE SET fields=excluded.fields",(tid,basis,sid,dumps(fields)))
        parts.append(fields)
    changes = [dict(targetId=task["artifact_id"],field=k,value=v if isinstance(v,str) else dumps(v))
               for k,v in combined_fields(parts).items()]
    outcome.structured = dict(schemaVersion=1,kind="draft",runId=rid,inputFingerprint=run["input_hash"],
                              baseVersionId=None,changes=changes)
    errors = wb._precheck(run,task,"draft",outcome.structured)
    if errors:
        return wb._finish(rid,"failed",reason="整稿校验失败",diagnostic="；".join(errors),outcome=outcome)
    (wb.store.run_dir(rid)/"result.json").write_bytes(canonical(outcome.structured))
    return wb._ingest(rid,"draft",outcome,0)
