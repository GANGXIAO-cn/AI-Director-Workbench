"""Publish pack, image-text delivery (staging dir → verify → rename) and the local H3 packager handoff."""
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import stages, timeline
from .db import connect, dumps, new_id, transaction
from .errors import NotFound, Rejected, loads, row
from .storage import canonical, sha256_bytes, write_atomic

DEFAULT_PACKAGER = Path.home() / "Documents/自媒体 2/h3-canvas-studio/tools/project-packager/packager.py"
RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9")
MAX_FILE = 30 * 1024 ** 2


def fmt(ms):
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


class DeliveryMixin:
    # ============================================================ publish settings / pack
    def publish_settings(self, project_id):
        custom = self.get_setting(f"publish:{project_id}")
        if custom:
            return custom
        with connect(self.db_path) as db:
            prod = json.loads(row(db, "SELECT production FROM project WHERE id=?", project_id)["production"])
        if prod.get("publishSettings") and prod.get("assetRoot"):
            path = Path(prod["assetRoot"]) / prod["publishSettings"]
            if path.is_file():
                return json.loads(path.read_text())
        return None

    def set_publish_settings(self, project_id, settings):
        self.put_setting(f"publish:{project_id}", settings)

    def compose_publish(self, episode_id, version_id=None):
        with connect(self.db_path) as db:
            ep = row(db, "SELECT * FROM episode WHERE id=?", episode_id)
            if version_id:
                content = json.loads(row(db, "SELECT content FROM version WHERE id=?", version_id)["content"])
            else:
                content = self._adopted_content(db, episode_id, "publish")
            prompts_ = self._adopted_content(db, episode_id, "prompts") or {}
        if not content:
            raise Rejected("发布包尚未采用")
        cfg = self.publish_settings(ep["project_id"])
        if not cfg:
            raise Rejected("本项目缺少发布设置（前缀、固定正文、标签），请在项目设置中补齐")
        prefix = cfg.get("title_prefix", "")
        title = content["title"] if content["title"].startswith(prefix) else prefix + content["title"]
        body = [content["body"].strip().splitlines()[0], *cfg.get("body_fixed_lines", [])][:3]
        extra = [t for t in (content.get("tags") or []) if t not in cfg.get("fixed_tags", [])]
        if len(extra) > cfg.get("max_extra_tags", 2):
            raise Rejected(f"本集标签最多 {cfg.get('max_extra_tags', 2)} 个")
        tags = [*cfg.get("fixed_tags", []), *extra]
        for ch in cfg.get("forbidden_chars", []):
            if ch in title or any(ch in b for b in body):
                raise Rejected(f"发布文案含禁止字符 {ch}")
        if len(title) > cfg.get("title_max_chars", 100):
            raise Rejected("发布标题过长")
        bgm = any((e.get("sound") or {}).get("bgm") for e in (prompts_.get("segments") or {}).values())
        music = content.get("music") or ("enabled" if bgm else "backup")
        hashtags = " ".join("#" + t for t in tags)
        fields = dict(title=title, body="\n".join(body), tags=hashtags, suno_title=content["suno_title"],
                      suno_style=content["suno_style"], suno_exclude=content.get("suno_exclude", ""))
        platforms = dict(youtube=dict(title=title, description=fields["body"] + "\n\n" + hashtags),
                         facebook=dict(title=title, description=fields["body"]),
                         instagram=dict(caption=f"{title}\n{fields['body']}\n{hashtags}"))
        english_title = title[len(prefix):] if title.startswith(prefix) else title
        return dict(fields=fields, platforms=platforms, music="备用" if music == "backup" else "启用",
                    musicState=music, cues=content.get("cues") or [], englishTitle=english_title, website=cfg.get("website"))

    # ============================================================ delivery checks
    def delivery_problems(self, episode_id, scope="full", segments=None):
        problems = []
        with connect(self.db_path) as db:
            ep = row(db, "SELECT * FROM episode WHERE id=?", episode_id)
            adopted = {s: db.execute("SELECT adopted_version_id FROM artifact WHERE episode_id=? AND stage=?",
                                     (episode_id, s)).fetchone() for s in ("story", "script", "plan", "prompts", "publish")}
            adopted = {k: (v[0] if v else None) for k, v in adopted.items()}
            needed = ("story", "script", "plan", "prompts", "publish") if scope == "full" else ("plan", "prompts")
            for stage in needed:
                if not adopted[stage]:
                    problems.append(dict(stage=stage, segment=None, file=None, problem=f"{stage} 尚未采用"))
            if not adopted["plan"]:
                return problems, None
            plan = self._adopted_content(db, episode_id, "plan")
            pr = self._adopted_content(db, episode_id, "prompts") or {}
            spec = json.loads(row(db, "SELECT spec FROM kickoff WHERE episode_id=?", episode_id)["spec"])
            pending = [dict(r) for r in db.execute("SELECT * FROM impact WHERE episode_id=? AND state='pending'", (episode_id,))]
        lay = timeline.layout(plan)
        segs = [s for s in lay["segments"] if scope == "full" or s["id"] in (segments or [])]
        if scope == "trial" and (not segments or len(segs) != len(set(segments))):
            problems.append(dict(stage="plan", segment=None, file=None, problem="试验范围须选择存在的生成段"))
        for p in timeline.check(plan):
            if p["segment"] is None or any(s["id"] == p["segment"] for s in segs):
                problems.append(dict(stage="plan", segment=p["segment"], file=None, problem=p["problem"]))
        blocked = self.blocked_segments(episode_id)
        if not adopted["prompts"]:
            return problems, None
        status = self.prompt_review_status(adopted["prompts"])
        for seg in segs:
            entry = (pr.get("segments") or {}).get(seg["id"])
            if not entry:
                problems.append(dict(stage="prompts", segment=seg["id"], file=None, problem="缺少提示词"))
                continue
            for p in stages.check_segment_prompt(seg["id"], entry, seg["duration_ms"], seg["strategy"]):
                problems.append(dict(stage="prompts", segment=seg["id"], file=f"prompts/{seg['id']}.txt", problem=p["problem"]))
            for ref in entry.get("refs", []):
                try:
                    data = self.asset_bytes(ref["assetVersionId"])
                    if len(data) > MAX_FILE:
                        problems.append(dict(stage="assets", segment=seg["id"], file=ref["label"], problem="单素材超过 30 MiB"))
                except (NotFound, OSError, ValueError) as exc:
                    problems.append(dict(stage="assets", segment=seg["id"], file=ref.get("label"), problem=f"素材缺失或指纹不符：{exc}"))
            for reason in blocked.get(seg["id"], []):
                problems.append(dict(stage="space", segment=seg["id"], file=None, problem=reason))
            st = status.get(seg["id"])
            if not st or not st["ok"]:
                problems.append(dict(stage="review", segment=seg["id"], file=None,
                                     problem="两遍独审未完成或有未处理意见（相关内容变化后须重新审核）"))
            for imp in pending:
                t = imp["target_ref"]
                if t == f"prompts:{seg['id']}" or (t.startswith("joint:") and seg["id"] in t[6:].split("-")):
                    problems.append(dict(stage="recheck", segment=seg["id"], file=None, problem=f"待复核：{imp['reason']}"))
        if spec.get("aspectRatio") not in RATIOS:
            problems.append(dict(stage="spec", segment=None, file=None, problem="画幅不在 H3 支持范围"))
        return problems, dict(ep=ep, adopted=adopted, plan=plan, prompts=pr, lay=lay, segs=segs, spec=spec)

    # ============================================================ deliver
    def deliver(self, episode_id, scope, command_id, segments=None):
        if scope not in ("full", "trial"):
            raise Rejected("交付范围只能是 full 或 trial")
        with connect(self.db_path) as db:
            prior = db.execute("SELECT result FROM command WHERE command_id=?", (command_id,)).fetchone()
        if prior:
            return json.loads(prior[0])
        problems, ctx = self.delivery_problems(episode_id, scope, segments)
        if problems:
            raise Rejected("结构阻断：交付核验未通过", detail=problems)
        ep, segs = ctx["ep"], ctx["segs"]
        did = new_id()
        parent = self.store.root / "projects" / ep["project_id"] / "episodes" / episode_id / "deliveries"
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=parent, prefix=".staging-"))
        try:
            files = self._write_delivery(staging, did, scope, ctx)
            for rel, digest in files.items():          # verify by actually reading what was written
                if sha256_bytes((staging / rel).read_bytes()) != digest:
                    raise Rejected(f"写入校验失败：{rel}")
            manifest = dict(deliveryId=did, scope=scope, files=files)
            write_atomic(staging / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
            final = parent / did
            os.rename(staging, final)                    # atomic publish on the same filesystem
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        manifest_hash = sha256_bytes(canonical(manifest))
        result = dict(deliveryId=did, scope=scope, path=str(final), html=str(final / "00_图文对照.html"),
                      segments=[s["id"] for s in segs], manifestHash=manifest_hash)
        with transaction(self.db_path) as db:
            db.execute("INSERT INTO delivery VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (did, episode_id, scope, dumps([s["id"] for s in segs]), manifest_hash,
                        final.relative_to(self.store.root).as_posix(), "deliverable", dumps(ctx["adopted"]), command_id,
                        self.clock()))
            if scope == "full":
                db.execute("UPDATE episode SET state='delivered', revision=revision+1 WHERE id=?", (episode_id,))
                db.execute("UPDATE topic SET status='已制作' WHERE episode_id=?", (episode_id,))
                db.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?)",
                           (new_id(), ep["project_id"], episode_id, did, "delivery", f"图文已交付（{len(segs)} 段）",
                            "", f"delivery:{did}", self.clock()))
            db.execute("INSERT INTO command VALUES(?,?,?,?,?)", (command_id, "deliver", sha256_bytes(canonical(
                dict(e=episode_id, s=scope, g=segments))), dumps(result), self.clock()))
        return result

    def _write_delivery(self, root: Path, did, scope, ctx):
        files = {}

        def put(rel, data: bytes):
            write_atomic(root / rel, data)
            files[rel] = sha256_bytes(data)

        ep, plan, pr, segs, spec = ctx["ep"], ctx["plan"], ctx["prompts"], ctx["segs"], ctx["spec"]
        publish = self.compose_publish(ep["id"]) if scope == "full" else None
        english = publish["englishTitle"] if publish else f"{ep['display_code']} trial"
        assets, seg_entries, used = [], [], set()
        offset = segs[0]["start_ms"] if scope == "trial" else 0
        for seg in segs:
            entry = pr["segments"][seg["id"]]
            refs = []
            for n, ref in enumerate(entry["refs"], 1):
                v = self.asset_version(ref["assetVersionId"])
                if v["id"] not in used:
                    used.add(v["id"])
                    safe = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", v["name"])[:60]
                    rel = f"assets/{safe}_v{v['version_no']}_{v['sha256'][:8]}.png"
                    put(rel, self.store.read_blob(v["sha256"]))
                    assets.append(dict(id=v["id"], path=rel, type=v["kind"], sha256=v["sha256"],
                                       origin=v.get("origin", "workbench") or "workbench", version=f"v{v['version_no']}",
                                       name=v["name"]))
                refs.append(dict(asset_id=v["id"], label=ref["label"], upload_order=n, role=ref.get("role", "")))
            put(f"prompts/{seg['id']}.txt", entry["txt"].encode())
            settings = dict(entry.get("generation_settings") or {})
            settings.setdefault("audio", not (entry.get("sound") or {}).get("silent", False))
            seg_entries.append(dict(id=seg["id"], name=seg["id"], duration_s=seg["duration_ms"] / 1000
                                    if seg["duration_ms"] % 1000 else seg["duration_ms"] // 1000,
                                    start=fmt(seg["start_ms"] - offset), references=refs, prompt=f"prompts/{seg['id']}.txt",
                                    generation_settings=settings, label_style="chinese", strategy=seg["strategy"]))
        for a in assets:
            a["origin"] = "workbench"
        manifest = dict(schema_version=2, episode_id=ep["display_code"], english_title=english,
                        aspect_ratio=spec.get("aspectRatio"), scope=scope, segments=seg_entries,
                        assets=[{k: a[k] for k in ("id", "path", "type", "sha256", "origin", "version")} for a in assets],
                        workbench=dict(deliveryId=did, adopted=ctx["adopted"]))
        put("episode.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        status = self.prompt_review_status(ctx["adopted"]["prompts"])
        review_lines = ["# 审核依据", ""]
        for seg in segs:
            st = status[seg["id"]]
            for name in ("coldRead", "comparison"):
                r = st[name]
                review_lines.append(f"- {seg['id']} {'冷读' if name == 'coldRead' else '对照'}：原结论 {r.get('decision')}，"
                                    f"被审版本 {r.get('versionId')}，审核方 {r.get('reviewer')}")
                for i in r.get("issues", []):
                    if i.get("waiver_reason"):
                        review_lines.append(f"  - 用户放行：{i['reason']} —— 理由：{i['waiver_reason']}")
        put("review.md", "\n".join(review_lines).encode())
        if publish:
            f = publish["fields"]
            md = (f"# {english}\n\n## 发布标题\n{f['title']}\n\n## 正文\n{f['body']}\n\n## 话题标签\n{f['tags']}\n\n"
                  f"## Suno Title\n{f['suno_title']}\n\n## Style of Music\n{f['suno_style']}\n\n## Exclude Styles\n"
                  f"{f['suno_exclude']}\n\n音乐：{publish['music']}\n\n## 音乐对位\n" +
                  "\n".join(f"- {c.get('at')} {c.get('action')}" for c in publish["cues"]))
            put("01_发布包.md", md.encode())
            must = [s for s in plan.get("shots", []) if s.get("mustKeep")]
            handoff = ("# 精剪交接\n\n## 必留镜头\n" + "\n".join(f"- {s['id']}：{s.get('action', '')}" for s in must) +
                       "\n\n## 声音\n" + "\n".join(f"- {sid}：{json.dumps(e.get('sound') or {}, ensure_ascii=False)}"
                                                for sid, e in pr["segments"].items()) +
                       "\n\n## 入景位置\n" + "\n".join(f"- {s['id']}：{s['placement']}" for s in plan.get("shots", [])
                                                   if s.get("placement")))
            put("03_精剪交接.md", handoff.encode())
            for v in self.episode_assets(ep["id"]):
                if v["id"] not in used and v["kind"] == "image":
                    safe = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", v["name"])[:60]
                    put(f"补选区/{safe}_v{v['version_no']}_{v['sha256'][:8]}.png", self.store.read_blob(v["sha256"]))
        for s_ in seg_entries:
            s_["_txt"] = pr["segments"][s_["id"]]["txt"]
        put("00_图文对照.html", self._render_html(english, seg_entries, assets, publish, scope).encode())
        return files

    def _render_html(self, title, segs, assets, publish, scope):
        by_id = {a["id"]: a for a in assets}
        e = html.escape
        parts = [f"<!doctype html><meta charset='utf-8'><title>{e(title)}</title>",
                 "<style>body{font-family:-apple-system,'PingFang SC',sans-serif;background:#111214;color:#e8e8ea;max-width:1100px;"
                 "margin:auto;padding:24px}img{max-height:220px;border-radius:8px;margin:4px}pre{white-space:pre-wrap;"
                 "background:#1c1d21;padding:12px;border-radius:8px}section{border-top:1px solid #2a2b30;padding:16px 0}</style>",
                 f"<h1>{e(title)}</h1><p>范围：{'整集完整交付' if scope == 'full' else '试验范围'}</p>"]
        for s in segs:
            parts.append(f"<section><h2>{e(s['id'])} · {s['start']} · {s['duration_s']} 秒 · {e(s['strategy'])}</h2><div>")
            for r in s["references"]:
                a = by_id[r["asset_id"]]
                parts.append(f"<figure style='display:inline-block'><img src='{e(a['path'])}'><figcaption>{e(r['label'])} "
                             f"{e(r.get('role', ''))}</figcaption></figure>")
            parts.append(f"</div><pre>{e(s.get('_txt', ''))}</pre>")
            parts.append(f"<p><a href='{e(s['prompt'])}'>{e(s['prompt'])}</a></p></section>")
        if publish:
            for k, label in (("title", "发布标题"), ("body", "正文"), ("tags", "话题标签"), ("suno_title", "Suno Title"),
                             ("suno_style", "Style of Music"), ("suno_exclude", "Exclude Styles")):
                parts.append(f"<section><h3>{label}</h3><pre>{e(publish['fields'][k])}</pre></section>")
        return "\n".join(parts)

    def delivery_view(self, delivery_id):
        with connect(self.db_path) as db:
            d = loads(row(db, "SELECT * FROM delivery WHERE id=?", delivery_id), "segments", "versions")
        root = self.store.root / d["relpath"]
        manifest = json.loads((root / "manifest.json").read_text())
        d["files"] = manifest["files"]
        d["html"] = str(root / "00_图文对照.html")
        return d

    def verify_delivery(self, delivery_id):
        """Offline re-check: every listed file exists with the same fingerprint, and nothing else crept in."""
        d = self.delivery_view(delivery_id)
        root = self.store.root / d["relpath"]
        errors = []
        for rel, digest in d["files"].items():
            p = root / rel
            if not p.is_file():
                errors.append(dict(file=rel, problem="缺失"))
            elif sha256_bytes(p.read_bytes()) != digest:
                errors.append(dict(file=rel, problem="指纹不符（外部修改须重新核图、登记版本并复审）"))
        extra = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
                       and str(p.relative_to(root)) not in d["files"] and p.name != "manifest.json")
        errors += [dict(file=x, problem="清单外文件") for x in extra]
        episode = json.loads((root / "episode.json").read_text())
        return dict(ok=not errors, errors=errors, segments=[s["id"] for s in episode["segments"]],
                    episodeId=episode["episode_id"])

    # ============================================================ packager handoff
    def _packager(self):
        path = Path(self.get_setting("packager_path") or DEFAULT_PACKAGER)
        if not path.is_file():
            raise Rejected(f"找不到本地打包器：{path}")
        spec = importlib.util.spec_from_file_location("h3_packager", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def package(self, delivery_id, segments, command_id, overrides=None):
        d = self.delivery_view(delivery_id)
        root = self.store.root / d["relpath"]
        episode = json.loads((root / "episode.json").read_text())
        problems = []
        if not 1 <= len(segments) <= 200:
            problems.append("每包 1–200 段")
        known = {s["id"]: s for s in episode["segments"]}
        for sid in segments:
            s = known.get(sid)
            if not s:
                problems.append(f"{sid}：不在本次交付内")
                continue
            dur = s["duration_s"]
            if not isinstance(dur, int) or not 5 <= dur <= 15:
                problems.append(f"{sid}：每段须 5–15 秒整数，当前 {dur}")
        for a in episode["assets"]:
            if (root / a["path"]).stat().st_size > MAX_FILE:
                problems.append(f"{a['path']}：单素材超过 30 MiB，请按章或分段打包；不会压缩原图")
        if episode["aspect_ratio"] not in RATIOS:
            problems.append("画幅不在 H3 支持范围")
        if problems:
            raise Rejected("打包前检查未通过", detail=problems)
        mod = self._packager()
        out = self.store.root / "packages" / delivery_id
        try:
            project = mod.load_project(str(root / "00_图文对照.html"))
            path, package = mod.build_package(project, segments, overrides or None, output=str(out))
        except ValueError as exc:
            raise Rejected(f"打包器拒绝：{exc}")
        pid = new_id()
        with transaction(self.db_path) as db:
            db.execute("INSERT INTO package VALUES(?,?,?,?,?,?,?)",
                       (pid, delivery_id, dumps(segments), str(Path(path).relative_to(self.store.root)),
                        sha256_bytes(Path(path).read_bytes()), dumps(package), self.clock()))
        return dict(packageId=pid, path=str(path), project=package)
