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
from .stages import WRITABLE, check_segment_prompt, normalize, render
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


def saved_note_blocks(db, version_id, notes):
    """Locate exact per-segment writer notes in a legacy concatenated field."""
    if not version_id or not notes:
        return {}
    rows = db.execute("""WITH RECURSIVE lineage(id,base_id,source_run_id) AS (
        SELECT id,base_id,source_run_id FROM version WHERE id=?
        UNION ALL SELECT v.id,v.base_id,v.source_run_id FROM version v JOIN lineage l ON v.id=l.base_id)
        SELECT p.segment,json_extract(p.fields,'$.notes') AS note FROM lineage l
        JOIN run r ON r.id=l.source_run_id JOIN prompt_segment_result p ON p.task_id=r.task_id
        ORDER BY r.rowid DESC""",(version_id,))
    blocks = {}
    for sid, note in rows:
        if isinstance(note,str) and note.strip() and notes.count(note)==1:
            blocks.setdefault(sid,note)
    return blocks


def write_segments(wb, run, task, manifest, entries):
    rid, tid = run["id"], task["id"]
    basis = sha256_bytes(canonical([(e["role"], e["name"], e["sha256"]) for e in entries]))
    values = {e["role"]:e for e in entries}
    scaffold = json.loads(values["prompt_scaffold"]["text"])
    plan = json.loads(values["plan_layout"]["text"])
    layouts = plan["layout"]["segments"]
    scope = manifest.get("promptSegments")
    notes_only = bool(scope and manifest.get("promptNotesOnly"))
    base_id = run["base_version_id"]
    base = {}
    note_blocks = {}
    if scope:
        if set(scope)-set(scaffold):
            raise ValueError("局部修订段不属于当前分镜")
        layouts = [item for item in layouts if item["id"] in scope]
        with connect(wb.db_path) as db:
            base = json.loads(db.execute("SELECT content FROM version WHERE id=?",(base_id,)).fetchone()[0])
            note_blocks = saved_note_blocks(db,base_id,base.get("notes",""))
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
            if notes_only or not segment_decision_errors(saved, sid, scaffold[sid], decisions, plan):
                parts.append(saved)
                continue
        scoped = scoped_entries(entries, sid, scaffold[sid], layout)
        if scope:
            scoped = [e for e in scoped if e["role"]!="base_version"]
            legacy_notes = notes_only and sid not in note_blocks and bool(base.get("notes"))
            previous = dict(segments={sid:base.get("segments",{}).get(sid)},
                            notes=base.get("notes","") if legacy_notes else note_blocks.get(sid,""))
            scoped.append(dict(role="base_version", name="本段原稿", text=render("prompts",previous)))
        trailer = prompts.draft_trailer("prompts",scoped,
                                        mode="document" if document else "changes")
        trailer += f"\n本次只起草 {sid}，segments 只含这一段。txt 为完整官方 H3 英文正文，原语言对白和画面文字用引号。按本段冻结的镜头、时间和图片顺序编写。素材限制和审查意见是写作依据，把它们落实到对应画面，不把评语或修改要求写进正文。"
        if notes_only:
            trailer = prompts.draft_trailer("prompts",scoped,mode="document" if document else "changes")
            trailer += f"\n本次只修 {sid} 的备注。只提交 notes，包含本段完整修正备注。"
            if legacy_notes:
                trailer += '历史备注没有分段记录，notes 请写含 before、after 的 JSON 文本，逐字引用本段需替换的原句及其修正。'

        prompt = prompts.prompt_from(scoped, trailer, run["purpose"], document=document)
        repair = manifest.get("segmentRepair", {})
        if repair.get("segment") == sid:
            prompt = wb._format_repair_prompt(prompt, repair.get("previousResponse"), [repair["error"]])
        work = wb.store.run_dir(rid) / f"segment-{number:02d}" / "work"
        work.mkdir(parents=True, exist_ok=True)
        images = []
        for e in scoped:
            if e.get("binary"):
                image = work / Path(e["name"]).name
                wb.store.materialize_blob(e["sha256"], image)
                images.append(image)
        request = ExecRequest(rid,run["purpose"],"draft",prompt,None if document else contracts.writing_schema(),
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
                if result.get("kind") != "draft" or result.get("baseVersionId") != base_id:
                    raise ValueError("须提交对应基版的本段提示词")
                fields = {}
                for c in result["changes"]:
                    key = c["field"]
                    if key not in WRITABLE["prompts"] or key in fields or c["targetId"] != task["artifact_id"]:
                        raise ValueError("稿件字段或目标不符")
                    fields[key] = normalize("prompts",key,c["value"])
                if report and isinstance(fields.get("productionNoteResponses"), list):
                    fields["productionNoteResponses"] = [dict(item, reportVersionId=report) if isinstance(item, dict) else item
                                                         for item in fields["productionNoteResponses"]]
                if notes_only:
                    if set(fields)!={"notes"} or not isinstance(fields["notes"],str) or not fields["notes"].strip():
                        raise ValueError("只提交本段的 notes 备注")
                    if legacy_notes:
                        patch = json.loads(fields["notes"])
                        before, after = patch["before"], patch["after"]
                        notes = base["notes"]
                        if not isinstance(before,str) or not before.strip() or notes.count(before)!=1 or not isinstance(after,str) or not after.strip():
                            raise ValueError("须提供能在原备注中唯一定位的原句和修改后文字")
                        start=notes.index(before)
                        for other,block in note_blocks.items():
                            at=notes.index(block)
                            if other!=sid and start<at+len(block) and at<start+len(before):
                                raise ValueError("备注修改超出本段")
                        fields.update(notes=after,previousNoteText=before)
                    break
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
                previous = outcome.structured
                if previous is None:
                    raw = work.parent / f"raw-response-{attempt}.txt"
                    if raw.is_file():
                        previous = raw.read_text(encoding="utf-8")
                if attempt == 2:
                    receipt = dict(segment=sid, error=str(exc), previousResponse=previous)
                    (wb.store.run_dir(rid) / "segment-repair.json").write_bytes(canonical(receipt))
                    return wb._finish(rid,"failed",reason="分段提示词校验失败",diagnostic=f"{sid}：{exc}",outcome=outcome)
                request.prompt = wb._format_repair_prompt(prompt,previous,[str(exc)])
        with transaction(wb.db_path) as db:
            if db.execute("SELECT status FROM run WHERE id=?", (rid,)).fetchone()[0] != "running":
                return []
            db.execute("INSERT INTO prompt_segment_result VALUES(?,?,?,?) ON CONFLICT(task_id,basis,segment) DO UPDATE SET fields=excluded.fields",(tid,basis,sid,dumps(fields)))
        parts.append(fields)
    fields = combined_fields(parts)
    if scope:
        notes = base.get("notes","")
        replacements = []
        additions = []
        for layout,part in zip(layouts,parts):
            sid = layout["id"]
            note = part.get("notes")
            if not note:
                continue
            previous_note = part.get("previousNoteText") or note_blocks.get(sid)
            if previous_note:
                start = notes.index(previous_note)
                replacements.append((start,start+len(previous_note),note))
            else:
                additions.append(note)
        last_start = len(notes)
        for start,end,note in sorted(replacements,reverse=True):
            if end>last_start:
                raise ValueError("两个分段的备注修改范围重叠")
            notes = notes[:start]+note+notes[end:]
            last_start = start
        fields = {key:value for key,value in fields.items() if key in ("segments","decisionNotes")}
        if notes_only:
            fields = {}
        if replacements or additions:
            fields["notes"] = "\n".join(filter(None,[notes,*additions]))
    changes = [dict(targetId=task["artifact_id"],field=k,value=v if isinstance(v,str) else dumps(v))
               for k,v in fields.items()]
    outcome.structured = dict(schemaVersion=1,kind="draft",runId=rid,inputFingerprint=run["input_hash"],
                              baseVersionId=base_id,changes=changes)
    errors = wb._precheck(run,task,"draft",outcome.structured)
    if errors:
        return wb._finish(rid,"failed",reason="整稿校验失败",diagnostic="；".join(errors),outcome=outcome)
    (wb.store.run_dir(rid)/"result.json").write_bytes(canonical(outcome.structured))
    return wb._ingest(rid,"draft",outcome,0)
