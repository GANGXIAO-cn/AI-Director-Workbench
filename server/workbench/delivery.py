"""Publish pack, image-text delivery (staging dir → verify → rename) and the local H3 packager handoff."""
import ast
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import re
import hashlib
import tempfile
import zipfile

from . import finecut, h3export, readable, shotrefs, imagechoices, publishing, stages, timeline, rhythm, deliverypage, promptreceipt, acting
from .db import connect, dumps, new_id, transaction
from .methodpack import load_config
from .images import image_size
from .errors import NotFound, Rejected, loads, row
from .storage import canonical, sha256_bytes, write_atomic

DEFAULT_PACKAGER = Path(__file__).resolve().parents[2] / "resources/h3-packager/packager.py"
from .specs import RATIOS, validate_spec, frame_geometry, h3_preset_defaults
MAX_FILE = 30 * 1024 ** 2


def package_geometry(root, manifest, files):
    def read_asset(asset):
        name = asset["path"]
        if name not in files or files[name] != asset["sha256"]:
            raise ValueError("九宫格原图与交付文件清单不符")
        return (root / name).read_bytes()
    return frame_geometry(manifest, read_asset)


def verify_sound_fields(root, episode, files):
    for seg in episode["segments"]:
        if seg.get("prompt_format") != "readable_v3":
            if episode.get("h3_rules") == "v1":
                raise ValueError(f"{seg['id']}：h3_rules 要求 readable_v3")
            continue
        if seg["prompt"] not in files:
            raise ValueError(f"{seg['id']}：分段 TXT 未登记")
        readable.check_h3_sound(seg, (root / seg["prompt"]).read_text())
        settings = seg.get("generation_settings") or {}
        readable.check_audio_switch(seg["id"], settings.get("audio", True), seg.get("full_silence"),
                                    seg.get("native_bgm") == "generate")


def fmt(ms):
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


class DeliveryMixin:
    def _mark_deliveries_stale(self, db, episode_id, reason, *, stage=None, adopted=None, reviewed=None, segment=None):
        for d in db.execute("SELECT * FROM delivery WHERE episode_id=? AND state='deliverable'", (episode_id,)).fetchall():
            versions = json.loads(d["versions"])
            if stage and (stage not in versions or versions[stage] == adopted):
                continue
            if reviewed and (versions.get("prompts") != reviewed or segment not in json.loads(d["segments"])):
                continue
            db.execute("UPDATE delivery SET state='needs_recheck' WHERE id=?", (d["id"],))
            db.execute('UPDATE episode_completion SET completed_at=NULL WHERE episode_id=?',(episode_id,))
            db.execute("UPDATE episode SET state='in_production' WHERE id=? AND state='delivered'",(episode_id,))
            db.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?)",
                       (new_id(), self._episode_in(db, episode_id)["project_id"], episode_id, d["id"],
                        "needs_recheck", reason, "", d["id"], self.clock()))

    def _require_current_delivery(self, db, delivery_id):
        if db.execute("SELECT 1 FROM storage_removed WHERE kind='delivery' AND owner=? AND removed_at>0 LIMIT 1", (delivery_id,)).fetchone():
            raise Rejected("这份中间交付文件已清理，请选择最终交付版本")
        if row(db, "SELECT state FROM delivery WHERE id=?", delivery_id)["state"] == "needs_recheck":
            raise Rejected("这份交付之后有上游改动或新的审查意见，请重新核验后再出包")

    def script_plan_budget(self, episode_id, script_version, plan_version):
        with connect(self.db_path) as db:
            def content(stage, vid):
                item = row(db, "SELECT v.content FROM version v JOIN artifact a ON a.id=v.artifact_id "
                               "WHERE a.episode_id=? AND a.stage=? AND v.id=?", episode_id, stage, vid)
                return json.loads(item["content"])
            script = content("script", script_version)
            plan = content("plan", plan_version)
        return timeline.script_budget(script, plan, timeline.layout(plan)["segments"])

    # ============================================================ publish settings / pack
    def publish_settings(self, project_id):
        return self._effective_publish_settings(project_id, self.get_setting(f"publish:{project_id}"))

    def _effective_publish_settings(self, project_id, custom):
        with connect(self.db_path) as db:
            project = row(db, "SELECT config_id,production FROM project WHERE id=?", project_id)
        prod = json.loads(project["production"])
        default_format = prod.get("publishCopyFormat")
        if default_format is None and project["config_id"] == "puggy":
            default_format = load_config("puggy")["production"].get("publishCopyFormat")
        if custom is not None and not isinstance(custom, dict):
            raise Rejected("发布设置须为对象")
        if custom is not None:
            merged = {"copy_format": default_format, **custom}
            if merged["copy_format"] != "instrumental_v1" or "hook_emoji" in merged:
                return publishing.settings(merged)
        if prod.get("publishSettings") and prod.get("assetRoot"):
            path = Path(prod["assetRoot"]) / prod["publishSettings"]
            if path.is_file():
                original = json.loads(path.read_text())
                if custom is None:
                    return publishing.settings(dict(original, copy_format=default_format))
                # Older project overrides can omit the required closing marker from the profile.
                merged["hook_emoji"] = original.get("hook_emoji", "")
        return publishing.settings(merged if custom is not None else None)

    def set_publish_settings(self, project_id, settings):
        effective = self._effective_publish_settings(project_id, settings)
        self.put_setting(f"publish:{project_id}", effective)

    def import_publish_pack(self, project_id, name, data, source="upload"):
        cfg = self.publish_settings(project_id)
        if cfg.get("copy_format") != "instrumental_v1":
            raise Rejected("项目未配置配乐发布包检查")
        diagnostic = None
        try:
            publishing.check_copy_pack(data.decode("utf-8"), cfg)
        except (Rejected, UnicodeDecodeError) as exc:
            diagnostic = str(exc)
        digest = self.store.put_blob(data)
        iid = new_id()
        with transaction(self.db_path) as db:
            db.execute("INSERT INTO historical_check VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (iid, project_id, "publish_pack", Path(name).name, source, digest, dumps(cfg),
                        int(diagnostic is None), diagnostic, self.clock()))
        return dict(importId=iid, sha256=digest, parseStatus="parsed" if diagnostic is None else "failed", diagnostic=diagnostic)

    def _publish_basis(self, db, episode_id):
        versions = {r["stage"]:r["adopted_version_id"] for r in db.execute(
            "SELECT stage,adopted_version_id FROM artifact WHERE episode_id=? AND stage IN ('story','plan','prompts')",
            (episode_id,))}
        story = self._adopted_content(db, episode_id, "story") or {}
        plan = self._adopted_plan(db, episode_id) or {}
        prompts = self._adopted_content(db, episode_id, "prompts") or {}
        shots = {s["id"]:s for s in plan.get("shots", [])}
        rows = []
        for seg in timeline.layout(plan)["segments"]:
            sound = prompts.get("segments", {}).get(seg["id"], {}).get("sound") or {}
            rows.append(dict(segment=seg["id"], at=fmt(seg["start_ms"])+"–"+fmt(seg["end_ms"]),
                             nativeBgm=bool(sound.get("bgm")), silent=bool(sound.get("silent")),
                             actions=[dict(shot=s, action=shots[s].get("action","")) for s in seg["shots"]]))
        return dict(versions=versions, synopsis=story.get("synopsis",""), segments=rows)

    def compose_publish(self, episode_id, version_id=None):
        with connect(self.db_path) as db:
            ep = row(db, "SELECT * FROM episode WHERE id=?", episode_id)
            art = row(db, "SELECT id,adopted_version_id FROM artifact WHERE episode_id=? AND stage='publish'", episode_id)
            vid = version_id or art["adopted_version_id"]
            if not vid:
                raise Rejected("发布包尚未采用")
            content = json.loads(self._version_of(db, vid, art["id"])["content"])
            snapshot = db.execute("SELECT settings FROM version_publish_config WHERE version_id=?", (vid,)).fetchone()
            cfg = json.loads(snapshot[0]) if snapshot else self.publish_settings(ep["project_id"])
            frozen = db.execute("SELECT basis FROM version_publish_basis WHERE version_id=?", (vid,)).fetchone()
            basis = json.loads(frozen[0]) if frozen else self._publish_basis(db, episode_id)
        return self._compose_publish_content(content, cfg, basis, bool(frozen))

    def _compose_publish_content(self, content, cfg, basis, frozen):
        if not all(isinstance(content.get(k), str) and content[k].strip() for k in ("title","body")):
            raise Rejected("发布标题与正文须有实际内容")
        prefix = cfg.get("title_prefix", "")
        title = content["title"] if content["title"].startswith(prefix) else prefix + content["title"]
        body_text = content["body"].strip()
        fixed_lines = cfg["body_fixed_lines"]
        if fixed_lines:
            body_text = body_text.splitlines()[0]
        emoji = cfg.get("hook_emoji")
        if emoji and not body_text.endswith(" " + emoji):
            body_text = body_text.removesuffix(emoji).rstrip() + " " + emoji
        body = [body_text, *fixed_lines]
        extra = [t for t in (content.get("tags") or []) if t not in cfg.get("fixed_tags", [])]
        if len(extra) > cfg.get("max_extra_tags", 2):
            raise Rejected(f"本集标签最多 {cfg.get('max_extra_tags', 2)} 个")
        tags = [*cfg.get("fixed_tags", []), *extra]
        for ch in cfg.get("forbidden_chars", []):
            if ch in title or any(ch in b for b in [*body, *tags]):
                raise Rejected(f"发布文案含禁止字符 {ch}")
        if len(title) > cfg.get("title_max_chars", 100):
            raise Rejected("发布标题过长")
        bgm = any(s["nativeBgm"] for s in basis["segments"])
        music = content.get("music") or ("enabled" if bgm else "backup")
        hashtags = " ".join("#" + t for t in tags)
        fields = dict(title=title, body="\n".join(body), tags=hashtags, **publishing.field_values(content,cfg))
        strict = cfg.get("copy_format") == "instrumental_v1"
        if strict:
            fields["tags"] = ", ".join(tags)
        labels = dict(publishing.CORE, **{f["key"]:f["label"] for f in cfg["additional_fields"]})
        copy_text = publishing.pack_markdown(fields, labels, cfg)
        problems = []
        try:
            checked = publishing.check_copy_pack(copy_text, cfg) if strict else None
        except Rejected as exc:
            problems.append(str(exc))
        try:
            cues = publishing.cue_rows(content.get("cues") or [], music, basis["segments"], strict, frozen=bool(frozen))
        except Rejected as exc:
            problems.append(str(exc))
        if problems:
            raise Rejected("；".join(problems))
        platform_description = "\n\n".join(s for s in (fields["body"], hashtags) if s)
        platforms = dict(youtube=dict(title=title, description=platform_description),
                         facebook=dict(title=title, description=platform_description),
                         instagram=dict(caption=checked["instagram_caption"] if checked else f"{title}\n{fields['body']}\n{hashtags}"))
        english_title = title[len(prefix):] if title.startswith(prefix) else title
        if strict and (not re.search("[A-Za-z]", english_title) or re.search("[\u3400-\u9fff]", english_title)):
            raise Rejected("本集须有英文视频标题")
        return dict(fields=fields, copyText=copy_text, fieldLabels=labels, settings=cfg, platforms=platforms, music="备用" if music == "backup" else "启用",
                    musicState=music, cueBasis=basis, basisFrozen=bool(frozen),
                    cues=cues, englishTitle=english_title, website=cfg.get("website"))

    # ============================================================ delivery checks
    def delivery_problems(self, episode_id, scope="full", segments=None, require_publish=True):
        from .workflow import ORDER, OPTIONAL
        if scope not in ("full", "trial"):
            raise Rejected("scope须为full或trial")
        problems = []
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            ep = row(db, "SELECT * FROM episode WHERE id=?", episode_id)
            adopted = {s: db.execute("SELECT adopted_version_id FROM artifact WHERE episode_id=? AND stage=?",
                                     (episode_id, s)).fetchone() for s in (*ORDER, "publish")}
            adopted = {k: (v[0] if v else None) for k, v in adopted.items()}
            workflow_documents = {stage: self._adopted_content(db, episode_id, stage)
                                  for stage, vid in adopted.items() if vid}
            skipped_stages = {r["stage"]: r["reason"] for r in db.execute(
                "SELECT stage,reason FROM optional_stage_choice WHERE episode_id=?", (episode_id,))}
            locked = self._kickoff(db, episode_id).get("sourceIntent") == "locked_screenplay"
            needed = tuple(s for s in ORDER if s not in OPTIONAL and (not locked or s not in ("story","characters","treatment"))) if scope == "full" else ("plan", "prompts")
            if scope == "full" and require_publish:
                needed = (*needed, "publish")
            for stage in needed:
                if not adopted[stage]:
                    problems.append(dict(stage=stage, segment=None, file=None, problem=f"{'发布文案' if stage == 'publish' else stage} 尚未采用"))
            if scope == "full":
                unconfirmed=db.execute("SELECT count(*) FROM production_supplement WHERE episode_id=? AND state='proposed'",(episode_id,)).fetchone()[0]
                if unconfirmed:
                    problems.append(dict(stage="space",segment=None,file=None,problem=f"有 {unconfirmed} 条制作补充未经导演确认"))
            if not adopted["plan"]:
                return problems, None
            plan = self._adopted_content(db, episode_id, "plan")
            story = self._adopted_content(db, episode_id, "story") or {}
            directions = finecut.directions(db, episode_id) if scope == "full" else None
            if scope == "full" and adopted["publish"]:
                frozen = db.execute("SELECT basis FROM version_publish_basis WHERE version_id=?", (adopted["publish"],)).fetchone()
                if frozen and json.loads(frozen[0])["segments"] != self._publish_basis(db, episode_id)["segments"]:
                    problems.append(dict(stage="publish",segment=None,file=None,
                                         problem="分段动作、时窗或声音设计已变更，请核对音乐对位并保存采用新版发布包"))
            script = self._adopted_content(db, episode_id, "script") or {}
            cast = self._cast_matrix(db, episode_id)
            pr = self._adopted_content(db, episode_id, "prompts") or {}
            spec = json.loads(row(db, "SELECT spec FROM kickoff WHERE episode_id=?", episode_id)["spec"])
            required = imagechoices.requirements(db, episode_id)
            pending = [dict(r) for r in db.execute("SELECT * FROM impact WHERE episode_id=? AND state='pending'", (episode_id,))]
            if scope == "full":
                for item in pending:
                    if item["kind"] in ("workflow_change","decision_change","supplement_change","palette_change"):
                        problems.append(dict(stage="recheck", segment=None, file=None, problem="待复核：" + item["reason"]))
        lay = timeline.layout(plan)
        segs = [s for s in lay["segments"] if scope == "full" or s["id"] in (segments or [])]
        if scope == "trial" and (not segments or len(segs) != len(set(segments))):
            problems.append(dict(stage="plan", segment=None, file=None, problem="试验范围须选择存在的生成段"))
        try:
            maximum = validate_spec(spec)["segmentMaxSeconds"]
        except Rejected as exc:
            problems.append(dict(stage="spec", segment=None, file=None, problem=str(exc)))
            maximum = 15
        if scope == "full" and spec.get("targetDuration") == "over_60" and lay["total_ms"] <= 60000:
            problems.append(dict(stage="plan", segment=None, file=None, problem="本集目标时长要求严格超过 60 秒"))
        for seg in segs:
            if maximum < 15 and seg["duration_ms"] > maximum * 1000:
                problems.append(dict(stage="plan", segment=seg["id"], file=None, problem=f"超过本集设定的 {maximum} 秒上限"))
        for p in timeline.check(plan):
            if p["segment"] is None or any(s["id"] == p["segment"] for s in segs):
                problems.append(dict(stage="plan", segment=p["segment"], file=None, problem=p["problem"]))
        try:
            baselines = self.project_role_assets(ep["project_id"])["roles"]
            by_role = {str(alias).casefold():r for r in baselines for alias in (r["id"],r.get("name")) if alias}
            if by_role:
                selected = {s["id"] for s in segs}
                for r in cast["rows"]:
                    if r["segment"] not in selected:
                        continue
                    for role, cell in r["cells"].items():
                        if cell["status"] == "ok" and role.casefold() in by_role and cell.get("assetVersionId"):
                            image = self.asset_version(cell["assetVersionId"])
                            if image["category"] == "identity" and image["sha256"] != by_role[role.casefold()]["sha256"]:
                                problems.append(dict(stage="cast", segment=r["segment"], file=image["name"], problem="固定角色不匹配现行角色基准"))
        except Rejected as exc:
            problems.append(dict(stage="cast", segment=None, file=None, problem=str(exc)))
        blocked = self.blocked_segments(episode_id)
        if not adopted["prompts"]:
            return problems, None
        with connect(self.db_path) as db:
            scoped = {s["id"]:pr["segments"][s["id"]] for s in segs if s["id"] in pr.get("segments",{})}
            for error in self._prompt_reference_errors(db, episode_id, scoped):
                problems.append(dict(stage="prompts", segment=None, file=None, problem=error))
        image_usage = {}
        decoded_images = {}
        order=[s['id'] for s in sorted(plan.get('segments',[]),key=lambda s:s['order'])]
        joints={'joint:'+a+'-'+b:[a,b] for a,b in zip(order,order[1:])}
        for seg in segs:
            entry = (pr.get("segments") or {}).get(seg["id"])
            if not entry:
                problems.append(dict(stage="prompts", segment=seg["id"], file=None, problem="缺少提示词"))
                continue
            for p in stages.check_segment_prompt(seg["id"], entry, seg["duration_ms"], seg["strategy"], seg["shots"]):
                problems.append(dict(stage="prompts", segment=seg["id"], file=f"prompts/{seg['id']}.txt", problem=p["problem"]))
            for ref in entry.get("refs", []):
                try:
                    data = self.asset_bytes(ref["assetVersionId"])
                    if len(data) > MAX_FILE:
                        problems.append(dict(stage="assets", segment=seg["id"], file=ref["label"], problem="单素材超过 30 MiB"))
                    elif ref.get("kind") == "image":
                        image_usage.setdefault(sha256_bytes(data), set()).add(seg["id"])
                        vid = ref["assetVersionId"]
                        if vid not in decoded_images:
                            decoded_images[vid] = image_size(data) is not None
                        if not decoded_images[vid]:
                            problems.append(dict(stage="assets", segment=seg["id"], file=ref["label"],
                                                 problem="采用的图片原文件无法完整解码"))
                except (NotFound, OSError, ValueError) as exc:
                    problems.append(dict(stage="assets", segment=seg["id"], file=ref.get("label"), problem=f"素材缺失或指纹不符：{exc}"))
            for reason in blocked.get(seg["id"], []):
                problems.append(dict(stage="space", segment=seg["id"], file=None, problem=reason))
            for imp in pending:
                t = imp["target_ref"]
                affected=joints.get(t,[])
                if t == f"prompts:{seg['id']}" or seg['id'] in affected:
                    problems.append(dict(stage="recheck", segment=seg["id"], affectedSegments=affected or [seg['id']], file=None, problem=f"待复核：{imp['reason']}"))
        if spec.get("aspectRatio") not in RATIOS:
            problems.append(dict(stage="spec", segment=None, file=None, problem="画幅不在 H3 支持范围"))
        rhythm_result = None
        try:
            rhythm_result = rhythm.check(plan,pr.get("segments",{}),[s["id"] for s in segs])
        except (ValueError,TypeError,KeyError) as exc:
            problems.append(dict(stage="prompts",segment=None,file=None,problem=str(exc)))
        placements = self.product_placements(episode_id)
        if scope == "full" and placements["verified"] < placements["minimum"]:
            reason = "、".join(dict.fromkeys(p["issue"] for p in placements["records"] if p["issue"]))
            problems.append(dict(stage="assets", kind="product_reference", segment=None, file=None,
                                 problem=f"商品入景覆盖 {placements['verified']}/{placements['minimum']}："
                                         + (reason or "对应镜头须绑定商品参考，并随该段交付")))
        frozen, choice_problems = imagechoices.check_scope(required, plan, scope, [s["id"] for s in segs], image_usage)
        problems.extend(choice_problems)
        for rule in frozen:
            if rule["sha256"] in image_usage:
                continue
            try:
                self.store.read_blob(rule["sha256"])
            except (ValueError, OSError):
                problems.append(dict(stage="assets", segment=None, file=rule["name"], problem="必用原版本不存在或原图指纹不符"))
        return problems, dict(workflowDocuments=workflow_documents, skippedStages=skipped_stages, requiredImages=frozen, allRequirements=required, ep=ep, adopted=adopted, story=story, directions=directions, script=script, plan=plan, prompts=pr, lay=lay, segs=segs, spec=spec, cast=cast,
                              rhythm=rhythm_result,placements=[p for p in placements["records"] if not p["issue"] and p["segment"] in {s["id"] for s in segs}])

    # ============================================================ deliver
    def deliver(self, episode_id, scope, command_id, segments=None, pre_publication=False):
        return self._deliver(episode_id, scope, command_id, segments, pre_publication)

    def _local_output(self, db, prior):
        if "packageId" in prior:
            source = row(db, "SELECT relpath FROM package WHERE id=?", prior["packageId"])
            return dict(prior, path=str(self.store._inside(self.store.root/source["relpath"])))
        source = row(db, "SELECT relpath FROM delivery WHERE id=?", prior["deliveryId"])
        path = self.store._inside(self.store.root/source["relpath"])
        return dict(prior, path=str(path), html=str(path/"00_图文对照.html"))

    def _deliver(self, episode_id, scope, command_id, segments=None, pre_publication=False):
        if type(pre_publication) is not bool:
            raise Rejected("交接模式须为布尔值")
        payload = dict(e=episode_id, s=scope, g=segments)
        if pre_publication:
            payload['prePublication'] = True
        with connect(self.db_path) as db:
            prior = self._existing_command(db, command_id, "deliver", payload)
            if prior:
                return self._local_output(db, prior)
        problems, ctx = self.delivery_problems(episode_id, scope, segments, require_publish=not pre_publication)
        if problems:
            raise Rejected("结构阻断：交付核验未通过", detail=problems)
        ep, segs = ctx["ep"], ctx["segs"]
        did = new_id()
        parent = self.store.root / "projects" / ep["project_id"] / "episodes" / episode_id / "deliveries"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent, prefix=".staging-") as work:
            staging = Path(work)
            files = self._write_delivery(staging, did, scope, ctx)
            for rel, digest in files.items():          # verify by actually reading what was written
                if sha256_bytes((staging / rel).read_bytes()) != digest:
                    raise Rejected(f"写入校验失败：{rel}")
            manifest = dict(deliveryId=did, scope=scope, files=files)
            write_atomic(staging / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
            final = parent / did
            manifest_hash = sha256_bytes(canonical(manifest))
            result = dict(deliveryId=did, scope=scope, path=str(final), html=str(final / "00_图文对照.html"),
                          segments=[s["id"] for s in segs], manifestHash=manifest_hash)
            with transaction(self.db_path) as db:
                prior = self._existing_command(db, command_id, "deliver", payload)
                if prior:
                    return self._local_output(db, prior)
                current = {r[0]:r[1] for r in db.execute("SELECT stage,adopted_version_id FROM artifact WHERE episode_id=?", (episode_id,))}
                if any(current.get(stage) != version for stage,version in ctx["adopted"].items()):
                    raise Rejected("写入交付期间采用版本发生变化，请重新核验")
                if self._kickoff(db, episode_id)["spec"] != ctx["spec"]:
                    raise Rejected("写入交付期间本集规格发生变化，请重新核验")
                if imagechoices.requirements(db, episode_id) != ctx["allRequirements"]:
                    raise Rejected("写入交付期间必用图发生变化，请重新核验")
                if scope == "full" and finecut.directions(db, episode_id) != ctx["directions"]:
                    raise Rejected("写入交付期间交接要求发生变化，请重新核验")
                if self._cast_matrix(db, episode_id) != ctx["cast"]:
                    raise Rejected("写入交付期间角色设定发生变化，请重新核验")
                os.rename(staging, final)
                db.execute("INSERT INTO delivery VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (did, episode_id, scope, dumps([s["id"] for s in segs]), manifest_hash,
                            final.relative_to(self.store.root).as_posix(), "deliverable", dumps(ctx["adopted"]), command_id,
                            self.clock()))
                if scope == "full" and ctx['adopted']['publish']:
                    from .completion import current as completion_current, adopted as complete
                    if not completion_current(self,db,episode_id)['completed']:
                        complete(self,db,dict(episode_id=episode_id,stage='publish'),ctx['adopted']['publish'])
                    db.execute("UPDATE episode SET state='delivered', revision=revision+1 WHERE id=?", (episode_id,))
                    db.execute("UPDATE topic SET status='已制作' WHERE episode_id=?", (episode_id,))
                    db.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?)",
                               (new_id(), ep["project_id"], episode_id, did, "delivery", f"图文已交付（{len(segs)} 段）",
                                "", f"delivery:{did}", self.clock()))
                db.execute("INSERT INTO command VALUES(?,?,?,?,?)", (command_id, "deliver", sha256_bytes(canonical(payload)), dumps(result), self.clock()))
        return result

    def _write_delivery(self, root: Path, did, scope, ctx):
        files = {}

        def put(rel, data: bytes):
            write_atomic(root / rel, data)
            files[rel] = sha256_bytes(data)

        ep, plan, pr, segs, spec = ctx["ep"], ctx["plan"], ctx["prompts"], ctx["segs"], ctx["spec"]
        publish = self.compose_publish(ep["id"],ctx["adopted"]["publish"]) if scope == "full" and ctx["adopted"]["publish"] else None
        english = publish["englishTitle"] if publish else (ep["title"] or ep["display_code"]) + (" trial" if scope == "trial" else "")
        assets, seg_entries, used = [], [], set()
        def pack_asset(version, directory):
            safe = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", version["name"])[:60]
            data = self.store.read_blob(version["sha256"])
            ext = deliverypage.media_suffix(version["kind"], version["name"], data)
            rel = f"{directory}/{safe}_v{version['version_no']}_{version['sha256'][:8]}{ext}"
            self.store.materialize_blob(version["sha256"], root / rel)
            files[rel] = version["sha256"]
            return rel

        prompt_paths = set()
        package_ms = 0
        for seg in segs:
            entry = pr["segments"][seg["id"]]
            refs = []
            for n, ref in enumerate(entry["refs"], 1):
                v = self.asset_version(ref["assetVersionId"])
                if v["id"] not in used:
                    used.add(v["id"])
                    rel = pack_asset(v, "assets")
                    assets.append(dict(id=v["id"], path=rel, type=v["kind"], sha256=v["sha256"],
                                       origin=h3export.asset_origin(v), version=f"v{v['version_no']}",
                                       name=v["name"], category="grid" if v["grid"] else v["category"]))
                refs.append(dict(asset_id=v["id"], label=ref["label"], upload_order=n, role=ref.get("role", "")))
                if ref.get("shots"):
                    refs[-1]["shots"] = ref["shots"]
            prompt_file = "prompts/" + deliverypage.link(seg["id"]).replace("/", "%2F") + ".txt"
            if len(prompt_file.encode()) > 230 or prompt_file.casefold() in prompt_paths:
                prompt_file = "prompts/by-id/" + sha256_bytes(seg["id"].encode()) + ".txt"
            prompt_paths.add(prompt_file.casefold())
            put(prompt_file, entry["txt"].encode())
            settings = dict(entry.get("generation_settings") or {})
            inherited = {key:value for key,value in h3_preset_defaults(spec).items() if key not in settings}
            settings.update(inherited)
            settings.setdefault("audio", not (entry.get("sound") or {}).get("silent", False))
            seg_entries.append(dict(id=seg["id"], name=seg["id"], duration_s=seg["duration_ms"] / 1000
                                    if seg["duration_ms"] % 1000 else seg["duration_ms"] // 1000,
                                    start=fmt(package_ms), references=refs, prompt=prompt_file,
                                    generation_settings=settings, label_style="chinese", strategy=seg["strategy"]))
            if entry.get("prompt_format") == "h3_official":
                seg_entries[-1]["prompt_format"] = "h3_official"
            if inherited:
                seg_entries[-1]["workbenchPresetParams"] = list(inherited)
            package_ms += seg["duration_ms"]
            if entry.get("prompt_format") == "readable_v3":
                seg_entries[-1].update(prompt_format="readable_v3",reference_strategy=seg["strategy"],
                                       native_bgm="generate" if (entry.get("sound") or {}).get("bgm") else "none",
                                       full_silence=bool((entry.get("sound") or {}).get("silent")))
        manifest = dict(schema_version=2, episode_id=ep["display_code"], english_title=english,
                        aspect_ratio=spec.get("aspectRatio"), scope=scope, segments=seg_entries,
                        design_duration_s=package_ms / 1000, video_check=dict(status="not_tested"),
                        assets=[{k: a[k] for k in ("id", "path", "type", "sha256", "origin", "version", "name", "category")} for a in assets],
                        workbench=dict(deliveryId=did, adopted=ctx["adopted"], requiredImages=ctx["requiredImages"], flow="storyboard-translation-v1"))
        if scope == "full":
            from .workflow import STAGES, METHOD_REF
            documents = {}
            for stage, label in STAGES:
                if stage not in ctx["workflowDocuments"]:
                    continue
                rel = f"九阶段成果/{label}.json"
                put(rel, canonical(ctx["workflowDocuments"][stage]))
                documents[stage] = dict(path=rel, sha256=files[rel], version=ctx["adopted"][stage])
            manifest["workbench"]["workflow"] = dict(method=METHOD_REF, documents=documents,
                                                       skipped=ctx["skippedStages"])
        if all(seg.get("prompt_format") == "readable_v3" for seg in seg_entries):
            manifest["h3_rules"] = "v1"
        budget = dict(scriptVersion=ctx["adopted"]["script"], planVersion=ctx["adopted"]["plan"],
                      scope=scope, **timeline.script_budget(ctx["script"], plan, segs))
        selected = {seg["id"] for seg in segs}
        if any(shot.get("dialogueCues") for shot in plan["shots"] if shot["segment"] in selected):
            budget["dialogueTiming"] = acting.report(plan, selected)
        put("分段与时长预算.json", json.dumps(budget, ensure_ascii=False, indent=2).encode())
        manifest["workbench"]["timingBudget"] = "分段与时长预算.json"
        review = ctx.get("rhythm") or {}
        if review.get("report"):
            put("镜头充分性联合表.md",rhythm.markdown(review["rows"]).encode())
            put("镜头节奏核对.json",json.dumps(review["report"],ensure_ascii=False,indent=2).encode())
            config = dict(version=2, plan="镜头充分性联合表.md")
            if review["legacy"]:
                manifest["workbench"]["partialShotRhythm"] = dict(**config, segments=list(review["report"]["segment_shot_counts"]))
            else:
                manifest["shot_rhythm"] = config
        review_lines = ["# 本次图文交付", "09按采用分镜编写，内容检查在写作任务内完成；未执行提示词后的整体独审。", "实际视频动态效果：not_tested。"]
        with connect(self.db_path) as db:
            plan_review=self._latest_review(db,ctx["adopted"]["plan"])
            if plan_review:
                review_lines.append("08分镜审核原结论："+plan_review['original_decision'])
                manifest["workbench"]["planReview"] = dict(id=plan_review['id'],decision=plan_review['original_decision'])
            else:
                ancestor=db.execute("""WITH RECURSIVE lineage(id,depth) AS (
                    SELECT base_id,1 FROM version WHERE id=? UNION ALL
                    SELECT v.base_id,l.depth+1 FROM version v JOIN lineage l ON v.id=l.id)
                    SELECT v.display_no,r.id FROM lineage l JOIN version v ON v.id=l.id
                    JOIN review r ON r.version_id=v.id ORDER BY l.depth,r.pass_no DESC LIMIT 1""",
                    (ctx["adopted"]["plan"],)).fetchone()
                if ancestor:
                    review_lines.append(f"08采用稿沿用 R{ancestor['display_no']:02d} 的版本链。独审记录属于该前序版本。")
                    manifest["workbench"]["ancestorPlanReview"] = ancestor["id"]
                else:
                    review_lines.append("08采用稿暂无独审记录。")
            if shotrefs.plan_enabled(plan):
                matrix=self._cast_matrix(db,ctx['ep']['id'])
                packets={seg['id']:shotrefs.segment_packet(self,db,ctx['ep']['id'],plan,matrix,seg['id'],
                           ctx['prompts']['segments'][seg['id']]) for seg in segs}
                shotrefs.export_packets(root,manifest,put,packets=packets)
        placements = ctx["placements"]
        for p in placements:
            if p.get('drawingReference'):
                review_lines.append(f"- 商品 {p['code']} · {p['segment']}/{p['shot_id']} · 主图仅作九宫格绘制参考，视频只接收本段九宫格 {p['scene_version']} · 动态 not_tested")
                continue
            if p.get('directReference'):
                review_lines.append(f"- 商品 {p['code']} {p['product']['name']} · {p['segment']}/{p['shot_id']} · 独立商品参考 {p['scene_version']} · 交付文件 {next(a['sha256'] for a in assets if a['id']==p['scene_version'])} · 目录原图 {p['product']['sha256']} · 动态 not_tested")
                continue
            review_lines.append(f"- 商品 {p['code']} {p['product']['name']} · {p['segment']}/{p['shot_id']} · 场面版本 {p['scene_version']} · "
                                f"主图 {p['product']['sha256']} · 看图观察：{p['observation']} · 动态 not_tested")
        put("review.md", "\n".join(review_lines).encode())
        pack = deliverypage.publish_snapshot(publish) if publish else None
        if publish:
            put("01_发布包.md", deliverypage.publish_markdown(english,pack).encode())
        if scope == "full":
            handoff = finecut.snapshot(ctx, did, publish or dict(englishTitle=english, music="未配置剪辑配乐", cues=[]))
            handoff["sourceFiles"] = dict(manifest["workbench"]["workflow"]["documents"])
            for key,label in (("story","故事稿"),("script","剧本"),("plan","制作方案")):
                path = f"交接来源/{label}.json"
                put(path, canonical(ctx[key]))
                handoff["sourceFiles"][key] = dict(path=path, sha256=files[path], version=ctx["adopted"][key])
            manifest["workbench"]["finecut"] = handoff
            put("03_精剪交接.md", finecut.render(handoff).encode())
        by_id = {a["id"]: a for a in assets}
        versions = self.episode_assets(ep["id"])
        # Inputs may come from a project library or a predecessor episode.
        local_ids = {v["id"] for v in versions}
        versions.extend(self.asset_version(a["id"]) for a in assets if a["id"] not in local_ids)
        gallery = []
        with connect(self.db_path) as db:
            selected_images = {r[0] for r in db.execute('SELECT adopted_version_id FROM asset_need WHERE episode_id=? AND removed=0', (ep['id'],))}
        selected_images.update(v['id'] for v in versions if set(v.get('marks',[])) & {'required','adopted'})
        for v in versions:
            if v.get('cleanedAt') or v["kind"] != "image" or v['id'] not in used | (selected_images if scope == 'full' else set()):
                continue
            rel = by_id[v["id"]]["path"] if v["id"] in used else pack_asset(v, "补选区")
            uses = [s["id"] for s in seg_entries if any(r["asset_id"] == v["id"] for r in s["references"])]
            with connect(self.db_path) as db:known=[dict(r) for r in db.execute("SELECT * FROM accepted_image_issue WHERE asset_version_id=?",(v["id"],))]
            from .progress_expert import provisional_image_issues
            with connect(self.db_path) as db:provisional=provisional_image_issues(db,ep['id'],v['id'])
            gallery.append(dict(provisionalIssues=provisional,knownIssues=known,id=v["id"], name=v["name"], version=f"v{v['version_no']}", path=rel,
                                segments=uses, marks=v.get("marks", [])))
        image_paths = {v["id"]: v["path"] for v in gallery}
        bindings = []
        selected = {s["id"]: {r["asset_id"] for r in s["references"]} for s in seg_entries}
        for row_ in ctx["cast"]["rows"]:
            sid = row_["segment"]
            if sid not in selected:
                continue
            for role, cell in sorted(row_["cells"].items()):
                item = dict(role=role, segment=sid, status=cell["status"], cells=cell["cells"], note=cell["note"])
                if cell.get("depictsMoment"):
                    item["depictsMoment"] = cell["depictsMoment"]
                vid = cell.get("assetVersionId")
                if vid:
                    version = self.asset_version(vid)
                    if vid not in image_paths:
                        image_paths[vid] = pack_asset(version, "角色基准")
                    item.update(id=vid, name=version["name"], version=f"v{version['version_no']}",
                                path=image_paths[vid], uploaded=vid in selected[sid])
                bindings.append(item)
        roster = {}
        for role, record in ctx["cast"]["roster"].items():
            listed = [r for r in bindings if r["role"] == role]
            if scope == "trial" and not any(r["status"] != "absent" for r in listed):
                continue
            roster[role] = dict(record, segments=[r["segment"] for r in listed if r["status"] != "absent"],
                                unconfirmed=[sid for sid in selected if not any(r["segment"] == sid for r in listed)])
            if record.get("assetVersionId"):
                vid = record["assetVersionId"]
                if vid not in image_paths:
                    image_paths[vid] = pack_asset(self.asset_version(vid), "角色基准")
                roster[role]["path"] = image_paths[vid]
        manifest["workbench"]["page"] = dict(version=2, project=ctx["ep"]["display_code"]+" · "+english,
                                             images=gallery, requiredImages=ctx["requiredImages"], publish=pack, bindings=bindings, roster=roster)
        with connect(self.db_path) as db:
            fix=db.execute("SELECT scope FROM continuous_run WHERE episode_id=? AND mode='fix' AND state='running' ORDER BY created_at DESC,rowid DESC LIMIT 1",(ctx['ep']['id'],)).fetchone()
        if fix:
            manifest['workbench']['changes']=[dict(type=item['type'],segments=item['segments'],scenes=item['scenes'],needKeys=item['needKeys'],instruction=item['instruction'],reason=item['reason']) for item in json.loads(fix['scope'])]
        manifest["workbench"]["frameGeometry"] = package_geometry(root, manifest, files)
        put("episode.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        put("00_图文对照.html", deliverypage.render(root, manifest, files).encode())
        imagechoices.verify_manifest(manifest, files)
        verify_sound_fields(root, manifest, files)
        deliverypage.verify(root, manifest, files)
        return files

    def package_file(self,package_id):
        with connect(self.db_path) as db:
            rel=row(db,"SELECT relpath FROM package WHERE id=?",package_id)["relpath"]
        path=(self.store.root / rel).resolve()
        with connect(self.db_path) as db:
            from .storage_maintenance import removed
            if removed(db, rel): raise NotFound("旧 H3 包已按 10 天保留规则清理")
        if (self.store.root / "packages").resolve() not in path.parents or not path.is_file():raise NotFound("未找到已导出的 H3 包")
        return path

    def delivery_view(self, delivery_id):
        with connect(self.db_path) as db:
            d = loads(row(db, "SELECT * FROM delivery WHERE id=?", delivery_id), "segments", "versions")
            from .storage_maintenance import removed
            d['cleanedAt'] = db.execute("SELECT max(removed_at) FROM storage_removed WHERE kind='delivery' AND owner=?", (delivery_id,)).fetchone()[0]
            d["packages"]=[dict(id=r["id"],filename=Path(r["relpath"]).name,cleanedAt=removed(db,r['relpath'])) for r in db.execute("SELECT id,relpath FROM package WHERE delivery_id=? ORDER BY created_at DESC,rowid DESC",(delivery_id,))]
            d["revision"] = db.execute("SELECT count(*) FROM delivery WHERE episode_id=? AND scope=? AND rowid<=(SELECT rowid FROM delivery WHERE id=?)",(d["episode_id"],d["scope"],delivery_id)).fetchone()[0]
        root = self.store.root / d["relpath"]
        manifest = json.loads((root / "manifest.json").read_text())
        d["files"] = manifest["files"]
        d["html"] = str(root / "00_图文对照.html")
        return d

    def verify_delivery(self, delivery_id):
        """Offline re-check: every listed file exists with the same fingerprint, and nothing else crept in."""
        with connect(self.db_path) as db:
            record = row(db, "SELECT relpath,manifest_hash,segments,episode_id FROM delivery WHERE id=?", delivery_id)
            code = row(db, "SELECT display_code FROM episode WHERE id=?", record["episode_id"])["display_code"]
            cleaned = db.execute("SELECT max(removed_at) FROM storage_removed WHERE kind='delivery' AND owner=?", (delivery_id,)).fetchone()[0]
            if cleaned:
                return dict(ok=False,cleanedAt=cleaned,episodeId=code,segments=json.loads(record['segments']),
                            errors=[dict(file='交付记录',problem='中间交付文件已按 10 天规则清理')])
        root = self.store.root / record["relpath"]
        errors = []
        result = dict(segments=json.loads(record["segments"]), episodeId=code, errors=errors)
        try:
            manifest = json.loads((root / "manifest.json").read_bytes())
            if sha256_bytes(canonical(manifest)) != record["manifest_hash"]:
                raise ValueError("清单指纹与交付记录不符")
        except (OSError, ValueError) as exc:
            errors.append(dict(file="manifest.json", problem=str(exc)))
            return dict(result, ok=False)
        files = manifest["files"]
        for rel, digest in files.items():
            p = root / rel
            try:
                if root.resolve() not in p.resolve().parents or p.is_symlink():
                    errors.append(dict(file=rel, problem="交付文件路径已改变"))
                elif not p.is_file():
                    errors.append(dict(file=rel, problem="缺失"))
                elif sha256_bytes(p.read_bytes()) != digest:
                    errors.append(dict(file=rel, problem="指纹不符（外部修改须重新核图、登记版本并复审）"))
            except OSError as exc:
                errors.append(dict(file=rel, problem=str(exc)))
        extra = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
                       and str(p.relative_to(root)) not in files and p != root / "manifest.json")
        errors += [dict(file=x, problem="清单外文件") for x in extra]
        if not errors:
            try:
                episode = json.loads((root / "episode.json").read_bytes())
                if "frameGeometry" in episode.get("workbench", {}):
                    if package_geometry(root, episode, files) != episode["workbench"]["frameGeometry"]:
                        raise ValueError("根据交付原图复算的画幅与登记结果不同")
                if "requiredImages" in episode.get("workbench", {}):
                    imagechoices.verify_manifest(episode, files)
                if "page" in episode.get("workbench", {}):
                    deliverypage.verify(root, episode, files)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append(dict(file="00_图文对照.html", problem=str(exc)))
        if not errors:
            try:
                verify_sound_fields(root, episode, files)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append(dict(file="episode.json", problem=str(exc)))
        if not errors and "prompt_review" in episode:
            try:
                promptreceipt.verify_package(root, episode, files)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append(dict(file="review.md", problem=str(exc)))
        if not errors and "镜头节奏核对.json" in files:
            def read_listed(name):
                if name not in files:
                    raise ValueError(f"镜头节奏核对引用了交付清单外的文件 {name}")
                return (root / name).read_text()
            try:
                episode = json.loads(read_listed("episode.json"))
                partial = episode.get("workbench", {}).get("partialShotRhythm")
                if partial:
                    eligible = [s for s in episode["segments"] if s.get("prompt_format") == "readable_v3"]
                    if [s["id"] for s in eligible] != partial["segments"]:
                        raise ValueError("可核对的交付段号与节奏报告范围不同")
                    episode = dict(episode, segments=eligible, shot_rhythm=partial)
                if rhythm.packaged_report(episode, read_listed) != json.loads(read_listed("镜头节奏核对.json")):
                    raise ValueError("根据交付 TXT 与联合表复算的镜头节奏结果不同")
            except (KeyError, TypeError, OSError, ValueError, ArithmeticError) as exc:
                errors.append(dict(file="镜头节奏核对.json", problem=str(exc)))
        return dict(result, ok=not errors)

    # ============================================================ packager handoff
    def _packager_path(self):
        return Path(self.get_setting("packager_path") or os.environ.get("ADW_PACKAGER_PATH") or DEFAULT_PACKAGER).expanduser()

    def packager_status(self):
        path = self._packager_path()
        result = dict(path=str(path), sha256=None, state="unavailable", lastPackage=None)
        try:
            if not path.is_file():
                raise ValueError("找不到本地打包器")
            if path.suffix != ".py":
                raise ValueError("请选择 Python 打包器文件")
            raw = path.read_bytes()
            result["sha256"] = sha256_bytes(raw)
            functions = {node.name for node in ast.parse(raw, str(path)).body if isinstance(node, ast.FunctionDef)}
            missing = sorted({"load_project", "package_contents", "write_zip"} - functions)
            if missing:
                raise ValueError("缺少打包入口 " + "、".join(missing))
            result["state"] = "ready"
            with connect(self.db_path) as db:
                last = db.execute("SELECT p.id,p.created_at,p.sha256 FROM package p JOIN package_tool t ON t.package_id=p.id "
                                  "WHERE t.tool_sha256=? ORDER BY p.created_at DESC LIMIT 1", (result["sha256"],)).fetchone()
            if last:
                result["lastPackage"] = dict(last)
        except (OSError, SyntaxError, ValueError) as exc:
            result["reason"] = str(exc)
        return result

    def _packager(self):
        path = self._packager_path()
        try:
            raw = path.read_bytes()
            spec = importlib.util.spec_from_file_location("h3_packager", path)
            if spec is None or spec.loader is None:
                raise ValueError("请选择 Python 打包器文件")
            mod = importlib.util.module_from_spec(spec)
            exec(compile(raw, str(path), "exec"), mod.__dict__)
            if not all(callable(getattr(mod,name,None)) for name in ("load_project", "package_contents", "write_zip")):
                raise ValueError("打包入口不完整")
            return mod, sha256_bytes(raw)
        except (OSError, SyntaxError, ImportError, ValueError) as exc:
            raise Rejected(f"无法加载本地打包器 {path}：{exc}") from exc

    def _prepare_package(self, delivery_id, segments, overrides, segment_overrides):
        if overrides is not None and (not isinstance(overrides, dict) or overrides.keys() - h3export.OPTIONS):
            raise Rejected("导出覆盖项仅支持分辨率、声音、候选、档位和种子")
        if not isinstance(segments, list) or not 1 <= len(segments) <= 200 or not all(isinstance(s,str) for s in segments):
            raise Rejected("每包须选择 1–200 个生成段")
        if segment_overrides is not None and (not isinstance(segment_overrides, dict)
                or segment_overrides.keys() - set(segments)
                or any(not isinstance(values, dict) or values.keys() - h3export.OPTIONS
                       for values in segment_overrides.values())):
            raise Rejected("单段覆盖须指定本次导出的段号和有效参数")
        with connect(self.db_path) as db:
            self._require_current_delivery(db, delivery_id)
        verified = self.verify_delivery(delivery_id)
        if not verified["ok"]:
            errors = verified["errors"]
            raise Rejected("交付验包失败：" + "、".join(str(e["file"]) for e in errors), detail=errors)
        d = self.delivery_view(delivery_id)
        root = self.store.root / d["relpath"]
        registry = dict(deliveryId=delivery_id, scope=d["scope"], files=d["files"])
        if sha256_bytes(canonical(registry)) != d["manifest_hash"]:
            raise Rejected("交付文件清单已变化")
        mod, tool_sha = self._packager()
        try:
            project = mod.load_project(str(root / "00_图文对照.html"))
            if sha256_bytes(project["raw"]) != d["files"]["episode.json"]:
                raise ValueError("集清单与登记版本不同")
            inputs = h3export.unique_inputs(project, segments, overrides, segment_overrides)
            filename, package, files = mod.package_contents(inputs, segments, None)
            if d["scope"] == "trial":
                package["scope"] = "segments"
            sources = {s["id"]:s for s in project["data"]["segments"]}
            for packed in package["segments"]:
                seg = sources[packed["id"]]
                if seg.get("prompt_format") == "readable_v3":
                    readable.check_audio_switch(seg["id"], packed["params"]["audio"], seg.get("full_silence"),
                                                seg.get("native_bgm") == "generate")
                name = seg["prompt"]
                raw = (root / name).read_bytes()
                if sha256_bytes(raw) != d["files"].get(name):
                    raise ValueError(f"{name} 在验包后发生变化")
                text = raw.decode("utf-8")
                if packed["prompt"] != text.replace("\r\n", "\n").replace("\r", "\n"):
                    raise ValueError(f"{name} 与打包器读入的内容不同")
                from .officialh3 import package_text
                packed["prompt"] = package_text(text) if seg.get("prompt_format") == "h3_official" else text
            h3export.prepare(package, files, project, lambda aid:h3export.asset_origin(self.asset_version(aid)))
        except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            raise Rejected(f"打包器拒绝：{exc}") from exc
        return d, mod, tool_sha, filename, package, files, project

    def package_preview(self, delivery_id, segments, overrides=None, segment_overrides=None):
        _, _, tool_sha, _, package, _, project = self._prepare_package(delivery_id, segments, overrides, segment_overrides)
        assets = {a["id"]:a for a in project["data"]["assets"]}
        sources = {s["id"]:s for s in project["data"]["segments"]}
        preview = []
        for seg in package["segments"]:
            source = sources[seg["id"]]
            refs = []
            ordered = sorted(source["references"], key=lambda r:r["upload_order"])
            for ref, packed in zip(ordered, seg["refs"], strict=True):
                asset = assets[ref["asset_id"]]
                refs.append(dict(label=ref["label"], role=ref.get("role", ""), name=asset.get("name") or asset["path"],
                                 sourceId=asset["id"], packedId=packed["assetId"], path=asset["path"], kind=asset["type"],
                                 version=asset["version"], sha256=asset["sha256"]))
            origins = {key:"本次单段覆盖" if key in (segment_overrides or {}).get(seg["id"], {}) else
                       "本次覆盖" if key in (overrides or {}) else
                       "本集有效规格" if key in source.get("workbenchPresetParams", []) else
                       "单段设置" if key in source.get("generation_settings", {}) else "打包器默认"
                       for key in h3export.OPTIONS}
            origins.update(ratio="本集画幅", duration="单段时长")
            preview.append(dict(id=seg["id"], strategy=source.get("strategy") or source.get("reference_strategy"), params=seg["params"], origins=origins, refs=refs))
        return dict(deliveryId=delivery_id, manifestHash=package["manifestSha256"], toolHash=tool_sha, segments=preview,
                    previewHash=sha256_bytes(canonical([delivery_id, tool_sha, package])))

    def package(self, delivery_id, segments, command_id, overrides=None, expected_preview=None, segment_overrides=None):
        payload = dict(delivery=delivery_id, segments=segments, overrides=overrides or {})
        if segment_overrides:
            payload["segmentOverrides"] = segment_overrides
        if expected_preview is not None:
            payload["preview"] = expected_preview
        with connect(self.db_path) as db:
            self._require_current_delivery(db, delivery_id)
            prior = self._existing_command(db, command_id, "package", payload)
            if prior:
                return self._local_output(db, prior)
        d, mod, tool_sha, filename, package, files, _ = self._prepare_package(delivery_id, segments, overrides, segment_overrides)
        if expected_preview is not None and expected_preview != sha256_bytes(canonical([delivery_id, tool_sha, package])):
            raise Rejected("打包器或出包内容已变化，请重新预览")
        pid = new_id()
        out = self.store.root / "packages" / delivery_id
        out.mkdir(parents=True, exist_ok=True)
        if d["scope"] == "trial" or Path(filename).name != filename or len(filename.encode()) > 240:
            filename = f"{d['scope']}_{pid}.h3project.zip"
        path = out / filename
        with tempfile.TemporaryDirectory(dir=out, prefix=".staging-") as work:
            pending = Path(work) / path.name
            with pending.open("xb") as stream:
                mod.write_zip(stream, package, files)
            try:
                h3export.verify_zip(pending, package)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                raise Rejected(f"H3 ZIP 核对失败：{exc}") from exc
            digest = hashlib.sha256()
            with pending.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            with transaction(self.db_path) as db:
                self._require_current_delivery(db, delivery_id)
                def register():
                    if path.exists():
                        raise Rejected("目标 ZIP 已存在，请重新出包")
                    pending.rename(path)
                    db.execute("INSERT INTO package VALUES(?,?,?,?,?,?,?)",
                               (pid, delivery_id, dumps(segments), str(path.relative_to(self.store.root)),
                                digest.hexdigest(), dumps(package), self.clock()))
                    db.execute("INSERT INTO package_tool(package_id,tool_sha256) VALUES(?,?)", (pid, tool_sha))
                    return dict(packageId=pid, path=str(path), project=package)
                return self._command(db, command_id, "package", payload, register)
