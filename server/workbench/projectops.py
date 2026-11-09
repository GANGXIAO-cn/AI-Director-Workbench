"""Project-level operations: closure, settings/spec changes, knowledge, topics, groups, expert mode,
model catalog, method-pack versions, backup/restore, todos and task records."""
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

from . import activity, contracts, prompts
from .db import connect, dumps, new_id, transaction
from .expert import ExpertMixin
from .history import HistoryMixin
from .errors import Conflict, NotFound, Rejected, loads, row
from .specs import validate_spec
from .methodpack import available_packs, load_pack, method_entries, pack_changes
from .stages import render
from .storage import canonical, file_sha256, sha256_bytes, write_atomic

# Capability metadata. Sources: CLI --help on this machine and live runs recorded in the phase reports.
MODEL_CATALOG = {
    "codex": dict(effortFlag="-c model_reasoning_effort", models=[
        dict(id=None, label="执行器默认", efforts=[None], status="verified", source="阶段 0/1 实测（默认）"),
        dict(id="gpt-6.1-sol", label="gpt-6.1-sol", efforts=[None, "low", "medium", "high"], status="partially_verified",
             source="阶段 0 实测 low；medium/high 待检测")]),
    "claude": dict(effortFlag="--effort", models=[
        dict(id=None, label="执行器默认", efforts=[None], status="verified", source="阶段 1 实测（默认）"),
        dict(id="sonnet", label="sonnet", efforts=[None, "low", "medium", "high", "xhigh", "max"], status="pending",
             source="claude --help 列出 low/medium/high/xhigh/max；sonnet/medium 因额度上限未测"),
        dict(id="opus", label="opus", efforts=[None, "low", "medium", "high", "xhigh", "max"], status="pending",
             source="claude --help")]),
    "antigravity": dict(effortFlag="原生型号内含档位", models=[
        dict(id=None, label="执行器默认", efforts=[None], status="verified", source="阶段 0 实测（默认）"),
        dict(id="gemini-3.8-flash-high", label="Gemini 3.8 Flash · high", efforts=[None], status="verified",
             source="阶段 0 实测"),
        dict(id="gemini-3.8-flash-medium", label="Gemini 3.8 Flash · medium", efforts=[None], status="pending",
             source="本机模型清单列出，未实测"),
        dict(id="gemini-3.8-flash-low", label="Gemini 3.8 Flash · low", efforts=[None], status="pending",
             source="本机模型清单列出，未实测")]),
}

def text_catalog():
    catalog = dict(MODEL_CATALOG)
    cache = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
    try:
        raw = json.loads(cache.read_text())
        available = raw["models"]
        if not isinstance(available, list):
            return catalog
    except (OSError, ValueError, KeyError, TypeError):
        return catalog
    known = {m["id"]: m for m in MODEL_CATALOG["codex"]["models"]}
    models = [known[None]]
    seen = set()
    for item in available:
        if not isinstance(item, dict) or item.get("visibility") != "list":
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip() or slug in seen:
            continue
        seen.add(slug)
        efforts = [e["effort"] for e in (item.get("supported_reasoning_levels") or [])
                   if isinstance(e, dict) and isinstance(e.get("effort"), str)]
        models.append(dict(id=slug, label=item.get("display_name") or slug, efforts=[None, *efforts],
                           status=known.get(slug, {}).get("status", "pending"),
                           context_window=item.get("context_window"), source="本机 Codex 型号清单"))
    if len(models) > 1:
        catalog["codex"] = dict(MODEL_CATALOG["codex"], models=models, fetchedAt=raw.get("fetched_at"))
    return catalog


def bigrams(text):
    t = re.sub(r"\s+", "", text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


class ProjectOpsMixin(ExpertMixin, HistoryMixin):
    # ============================================================ closure (整理本集设定)
    def start_closure(self, delivery_id, command_id, provider=None):
        with transaction(self.db_path) as db:
            d = row(db, "SELECT * FROM delivery WHERE id=?", delivery_id)
            if d["scope"] != "full":
                raise Rejected("只有整集完整交付可以整理设定")
            existing = db.execute("SELECT * FROM closure WHERE delivery_id=?", (delivery_id,)).fetchone()
            if existing:
                return dict(closureId=existing["id"], state=existing["state"], reopened=True)
            ep = row(db, "SELECT * FROM episode WHERE id=?", d["episode_id"])
            art = row(db, "SELECT * FROM artifact WHERE episode_id=? AND stage='story'", ep["id"])
            kickoff = self._kickoff(db, ep["id"])
            tid, thread = new_id(), new_id()
            db.execute("INSERT INTO task(id,project_id,episode_id,artifact_id,stage,goal,executor_plan,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?)", (tid, ep["project_id"], ep["id"], art["id"], "story", "整理本集设定",
                                                   dumps(kickoff["executor_plan"]), self.clock()))
            db.execute("INSERT INTO thread VALUES(?,?,?)", (thread, tid, f"closure:{delivery_id}"))
            versions = json.loads(d["versions"])
            entries = [dict(role="adopted_" + s, name=f"{s}.md", text=render(s, json.loads(
                           row(db, "SELECT content FROM version WHERE id=?", versions[s])["content"])))
                       for s in ("story", "script") if versions.get(s)]
            entries.append(dict(role="project_settings", name="settings.txt",
                                text=prompts.render_settings(self._settings(db, ep["project_id"])[1])))
            run_id = self._queue_run(db, tid, "proposal", None, "", entries=entries, provider=provider,
                                     extra_header=dict(deliveryId=delivery_id))
            cid = new_id()
            db.execute("INSERT INTO closure VALUES(?,?,?,?,?,?,?)", (cid, ep["id"], delivery_id, run_id, "proposing",
                                                                     None, self.clock()))
        self.execute_run(run_id)
        return dict(closureId=cid, runId=run_id)

    def _run_proposal(self, run_id, closure_id):
        with connect(self.db_path) as db:
            run = row(db, "SELECT * FROM run WHERE id=?", run_id)
        manifest, digest = self.store.load_manifest(run["manifest_relpath"])
        entries = self.store.manifest_texts(manifest)
        trailer = (f"运行号：{run_id}\n输入指纹：{digest}\n回包为 kind=proposal：items 中每项写类别、内容、真实出处（source 的 "
                   "objectId/versionId 填采用稿版本）、建议强度（required/variable）和提议依据。只提可以长期复用的设定。")
        from .executors.base import ExecRequest, ExecOutcome
        workdir = self.store.run_dir(run_id) / "work"
        workdir.mkdir(parents=True, exist_ok=True)
        request = ExecRequest(
            run_id=run_id, purpose="proposal", kind="proposal", prompt=prompts.prompt_from(entries, trailer, "proposal"),
            schema=contracts.branch_schema("proposal"), session_mode="new", workdir=workdir,
            model=run["requested_model"], effort=run["requested_effort"])
        if digest != run["input_hash"]:
            outcome = ExecOutcome(ok=False, error_class="输入清单指纹不符")
        else:
            try:
                outcome = self.executors[run["provider"]].start(request)
            except Exception as exc:
                outcome = ExecOutcome(ok=False, error_class="executor_error", diagnostic=str(exc)[:500])
        result = outcome.structured if outcome.ok else None
        problems = contracts.validate(result) if result else [outcome.error_class or "failed"]
        if result and not problems and (result.get("kind") != "proposal" or result["runId"] != run_id
                                        or result["inputFingerprint"] != digest):
            problems = ["回包不属于本次运行"]
        with transaction(self.db_path) as db:
            if row(db, "SELECT status FROM run WHERE id=?", run_id)["status"] == "stopped":
                db.execute("UPDATE closure SET state='failed' WHERE id=?", (closure_id,))
                return
            if problems:
                self._finish(run_id, "waiting_quota" if outcome.error_class == "quota" else "failed",
                             reason="; ".join(problems)[:300], diagnostic=outcome.diagnostic,
                             outcome=outcome, db=db)
                db.execute("UPDATE closure SET state='failed' WHERE id=?", (closure_id,))
                return
            for n, it in enumerate(result["items"], 1):
                db.execute("INSERT INTO proposal_item(id,closure_id,ordinal,category,content,source_ref,suggested_strength,"
                           "rationale) VALUES(?,?,?,?,?,?,?,?)", (new_id(), closure_id, n, it["category"], it["content"],
                                                                  dumps(it["source"]), it["suggestedStrength"], it["rationale"]))
            self._finish(run_id, "succeeded", outcome=outcome, db=db)
            db.execute("UPDATE closure SET state='open' WHERE id=?", (closure_id,))

    def closure_view(self, closure_id):
        with connect(self.db_path) as db:
            c = loads(row(db, "SELECT * FROM closure WHERE id=?", closure_id), "result")
            c["items"] = [loads(dict(r), "source_ref") for r in db.execute(
                "SELECT * FROM proposal_item WHERE closure_id=? ORDER BY ordinal", (closure_id,))]
        counts = {}
        for it in c["items"]:
            counts[it["decision"] or "undecided"] = counts.get(it["decision"] or "undecided", 0) + 1
        c["counts"] = counts
        return c

    def decide_items(self, closure_id, decisions: list[dict], command_id):
        """Save choices (稍后处理 keeps them). decision: write_setting | ledger_only | drop; optional content edit."""
        with transaction(self.db_path) as db:
            def run():
                c = row(db, "SELECT * FROM closure WHERE id=?", closure_id)
                if c["state"] == "confirmed":
                    raise Rejected("已确认的整理不能再改")
                for d in decisions:
                    if d.get("decision") not in (None, "write_setting", "ledger_only", "drop"):
                        raise Rejected("去向只能是写进设定、仅留台账或不处理")
                    db.execute("UPDATE proposal_item SET decision=?, strength=?, reason=?, content=coalesce(?,content), "
                               "asset_version_id=? WHERE id=? AND closure_id=?",
                               (d.get("decision"), d.get("strength"), d.get("reason"), d.get("content"),
                                d.get("assetVersionId"), d["itemId"], closure_id))
                db.execute("UPDATE closure SET state='staged' WHERE id=?", (closure_id,))
                return dict(closureId=closure_id, state="staged")
            return self._command(db, command_id, "decide", dict(c=closure_id, d=decisions), run)

    def confirm_closure(self, closure_id, command_id):
        with transaction(self.db_path) as db:
            def run():
                c = row(db, "SELECT * FROM closure WHERE id=?", closure_id)
                if c["state"] == "confirmed":
                    return json.loads(c["result"])
                items = [dict(r) for r in db.execute("SELECT * FROM proposal_item WHERE closure_id=? ORDER BY ordinal",
                                                     (closure_id,))]
                missing = [it["ordinal"] for it in items if it["decision"] == "write_setting"
                           and (not (it["reason"] or "").strip() or it["strength"] not in ("required", "variable"))]
                if missing:
                    raise Rejected("写进设定须逐项填写理由和守住程度", detail=dict(rows=missing))
                ep = row(db, "SELECT * FROM episode WHERE id=?", c["episode_id"])
                project = row(db, "SELECT * FROM project WHERE id=?", ep["project_id"])
                writes = [it for it in items if it["decision"] == "write_setting"]
                rev = project["settings_revision"] + (1 if writes else 0)
                out = dict(settings=[], ledger=[], reuse=[])
                for it in writes:
                    key = f"{it['category']}-{c['episode_id'][:4]}-{it['ordinal']}"
                    db.execute("INSERT INTO setting_entry(id,project_id,setting_revision,key,category,strength,content,"
                               "source_ref,user_reason) VALUES(?,?,?,?,?,?,?,?,?)",
                               (new_id(), project["id"], rev, key, it["category"], it["strength"], it["content"],
                                f"{ep['display_code']} 交付 {c['delivery_id'][:8]} · " + (lambda r: f"{r.get('anchor', '')}：{r.get('evidence', '')}")(json.loads(it["source_ref"])), it["reason"]))
                    out["settings"].append(key)
                if writes:
                    db.execute("UPDATE project SET settings_revision=?, revision=revision+1 WHERE id=?", (rev, project["id"]))
                for it in items:
                    if it["decision"] == "ledger_only":
                        lid = new_id()
                        db.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?)",
                                   (lid, project["id"], ep["id"], c["delivery_id"], "observation", it["content"],
                                    it["rationale"], it["source_ref"], self.clock()))
                        out["ledger"].append(lid)
                    if it["asset_version_id"]:
                        v = self.asset_version(it["asset_version_id"], db)
                        if v["grid"]:
                            raise Rejected("九宫格不能申请复用")
                        if not db.execute("SELECT 1 FROM library_entry WHERE asset_version_id=?", (v["id"],)).fetchone():
                            eid = new_id()
                            db.execute("INSERT INTO library_entry VALUES(?,?,?,?,?,?,?,?,?,?)",
                                       (eid, project["id"], v["id"], it["content"], None, "", it["rationale"], "pending",
                                        ep["id"], self.clock()))
                            out["reuse"].append(eid)
                db.execute("UPDATE closure SET state='confirmed', result=? WHERE id=?", (dumps(out), closure_id))
                return out
            return self._command(db, command_id, "confirm_closure", dict(c=closure_id), run)

    # ============================================================ settings change and spec
    def propose_setting_change(self, project_id, key, content, strength, reason, command_id, category="rule"):
        """New revision of one setting; lists in-production episodes that actually use it."""
        if not reason.strip():
            raise Rejected("修改设定须写理由")
        with transaction(self.db_path) as db:
            def run():
                p = row(db, "SELECT * FROM project WHERE id=?", project_id)
                rev = p["settings_revision"] + 1
                old = db.execute("SELECT * FROM setting_entry WHERE project_id=? AND key=? AND retired_revision IS NULL",
                                 (project_id, key)).fetchone()
                if old:
                    db.execute("UPDATE setting_entry SET retired_revision=? WHERE id=?", (rev, old["id"]))
                db.execute("INSERT INTO setting_entry(id,project_id,setting_revision,key,category,strength,content,source_ref,"
                           "user_reason) VALUES(?,?,?,?,?,?,?,?,?)", (new_id(), project_id, rev, key,
                                                                      old["category"] if old else category, strength, content,
                                                                      "项目设定页修改", reason))
                db.execute("UPDATE project SET settings_revision=? WHERE id=?", (rev, project_id))
                affected = []
                for ep in db.execute("SELECT e.id,e.display_code,k.settings_snapshot FROM episode e JOIN kickoff k ON "
                                     "k.episode_id=e.id WHERE e.project_id=? AND e.state='in_production'", (project_id,)):
                    if any(s["key"] == key for s in json.loads(ep["settings_snapshot"])):
                        affected.append(dict(episodeId=ep["id"], code=ep["display_code"], scope="故事独审及引用此设定的场次"))
                return dict(settingsRevision=rev, affected=affected)
            return self._command(db, command_id, "setting_change", dict(p=project_id, k=key, c=content, s=strength), run)

    def choose_episode_settings(self, episode_id, follow_new: bool, command_id):
        with transaction(self.db_path) as db:
            def run():
                ep = row(db, "SELECT * FROM episode WHERE id=?", episode_id)
                rev, snap = self._settings(db, ep["project_id"])
                if follow_new:
                    db.execute("UPDATE kickoff SET settings_revision=?, settings_snapshot=? WHERE episode_id=?",
                               (rev, dumps(snap), episode_id))
                    art = db.execute("SELECT * FROM artifact WHERE episode_id=? AND stage='story'", (episode_id,)).fetchone()
                    self._record_impacts(db, dict(project_id=ep["project_id"], episode_id=episode_id),
                                         [dict(target="story-review", reason=f"按设定修订 {rev} 复核故事独审")],
                                         "settings_follow", f"settings:{rev}")
                db.execute("INSERT INTO episode_settings VALUES(?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET "
                           "settings_revision=excluded.settings_revision, snapshot=excluded.snapshot",
                           (episode_id, rev if follow_new else -1, dumps(snap if follow_new else []), self.clock()))
                return dict(episodeId=episode_id, follow=follow_new)
            return self._command(db, command_id, "ep_settings", dict(e=episode_id, f=follow_new), run)

    def _project_presets(self, db, project_id):
        project = row(db, "SELECT spec_preset FROM project WHERE id=?", project_id)
        presets = {"default": dict(name="default", rev=0, values=json.loads(project["spec_preset"]))}
        latest = db.execute('SELECT p.name,p.rev,p."values" FROM preset p JOIN '
                            '(SELECT name,max(rev) AS rev FROM preset WHERE project_id=? GROUP BY name) v '
                            'ON p.name=v.name AND p.rev=v.rev WHERE p.project_id=? ORDER BY p.name',
                            (project_id,project_id))
        for name, rev, values in latest:
            presets[name] = dict(name=name, rev=rev, values=json.loads(values))
        return presets

    def project_presets(self, project_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            presets = self._project_presets(db, project_id)
            episodes = []
            for item in db.execute("SELECT e.id,e.display_code,k.spec,b.name,b.rev,b.base_values "
                                   "FROM episode e JOIN kickoff k ON k.episode_id=e.id "
                                   "LEFT JOIN kickoff_preset b ON b.episode_id=e.id WHERE e.project_id=? "
                                   "ORDER BY e.display_code", (project_id,)):
                episodes.append(dict(id=item["id"], code=item["display_code"], effective=json.loads(item["spec"]),
                                     name=item["name"], rev=item["rev"],
                                     base=json.loads(item["base_values"]) if item["base_values"] else None))
        return dict(presets=list(presets.values()), episodes=episodes)

    def update_preset(self, project_id, values: dict, command_id, name="default", expected_revision=None, copy_as=None):
        payload = dict(p=project_id, v=values)
        if name!="default" or expected_revision is not None or copy_as is not None:
            payload.update(name=name, expected=expected_revision, copy=copy_as)
        with transaction(self.db_path) as db:
            def run():
                presets = self._project_presets(db, project_id)
                base = presets.get(name)
                if not base:
                    raise Rejected("所选预设不属于本项目")
                if expected_revision is not None and expected_revision != base["rev"]:
                    raise Conflict("预设已更新，请重新载入后核对")
                if not isinstance(values,dict):
                    raise Rejected("规格值须为对象")
                updated = validate_spec({**base["values"], **values})
                target = name
                if copy_as is not None:
                    if not isinstance(copy_as,str) or not 1 <= len(copy_as.strip()) <= 80 or not copy_as.strip().isprintable():
                        raise Rejected("新预设名称须为 1 至 80 个可显示字符")
                    target = copy_as.strip()
                    if target in presets or target=="项目默认":
                        raise Conflict("该预设名称已使用")
                rev = presets[target]["rev"]+1 if target in presets else 1
                if target == "default":
                    db.execute("UPDATE project SET spec_preset=?, revision=revision+1 WHERE id=?", (dumps(updated), project_id))
                db.execute('INSERT INTO preset(id,project_id,name,rev,"values",created_at) VALUES(?,?,?,?,?,?)',
                           (new_id(), project_id, target, rev, dumps(updated), self.clock()))
                return dict(preset=updated, name=target, rev=rev)
            return self._command(db, command_id, "preset", payload, run)

    def episode_spec_change(self, episode_id, key, value, command_id, confirm=False, expected_preview=None):
        with transaction(self.db_path) as db:
            k = self._kickoff(db, episode_id)
            ep = self._episode_in(db,episode_id)
            value = validate_spec(dict(k["spec"], **{key:value}))[key]
            old = k["spec"].get(key)
            impacts, kept = [], []
            if key=="aspectRatio" and old!=value:
                plan = self._adopted_content(db, episode_id, "plan") or {}
                prompts = self._adopted_content(db, episode_id, "prompts") or {}
                usage = {}
                for sid, segment in prompts.get("segments", {}).items():
                    for ref in segment.get("refs", []):
                        usage.setdefault(ref.get("assetVersionId"), []).append(sid)
                impacts.extend(dict(target=f"镜头构图 {s['id']}", reason="已采用制作方案", segment=s.get("segment")) for s in plan.get("shots", []))
                for asset in self.episode_assets(episode_id):
                    vid = asset["id"]
                    used = vid in usage or any(m in ("adopted","required") for m in asset["marks"])
                    if not used:
                        continue
                    label = f"{asset['name']} v{asset['version_no']}"
                    if asset["category"] == "identity" and not asset["grid"]:
                        kept.append(label)
                    elif asset["grid"] or asset["category"] in ("state","brand_sting"):
                        impacts.append(dict(target=("九宫格 " if asset["grid"] else "素材 ")+label,
                                            versionId=vid, reason="、".join(usage.get(vid,[])) or "本集已选原图"))
                impacts.append(dict(target="H3 打包画幅",reason="下次交付使用本集画幅"))
            preview = dict(key=key, old=old, new=value, impacts=impacts, kept=kept)
            basis = list(db.execute("SELECT stage,adopted_version_id FROM artifact WHERE episode_id=? AND stage IN ('plan','prompts') ORDER BY stage",
                                    (episode_id,))) if key=="aspectRatio" else []
            fingerprint = sha256_bytes(canonical([preview,[list(pair) for pair in basis]]))
            if not confirm:
                return dict(preview=preview, fingerprint=fingerprint)
            def run():
                if expected_preview is not None and expected_preview!=fingerprint:
                    raise Conflict("本集规格或依赖已变化，请重新预览")
                if value==old:
                    return dict(preview=preview, spec=k["spec"])
                spec = dict(k["spec"], **{key: value})
                db.execute("UPDATE kickoff SET spec=? WHERE episode_id=?", (dumps(spec), episode_id))
                db.execute("INSERT INTO spec_change VALUES(?,?,?,?,?,?,?)", (new_id(), episode_id, key, dumps(old),
                                                                             dumps(value), dumps(impacts), self.clock()))
                self._record_impacts(db, dict(project_id=ep["project_id"],episode_id=episode_id), impacts, "spec_change", f"spec:{key}")
                return dict(preview=preview, spec=spec)
            payload = dict(e=episode_id, k=key, v=value)
            if expected_preview is not None:
                payload["preview"] = expected_preview
            return self._command(db, command_id, "spec_change", payload, run)

    # ============================================================ knowledge and topics
    def project_knowledge(self, project_id):
        with connect(self.db_path) as db:
            return [dict(r) for r in db.execute("SELECT id,title,kind,source,status,sha256 FROM knowledge WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def import_knowledge(self, project_id, files: list[dict], command_id=None):
        """files: [{name, data(bytes), kind?}] → readable / duplicate / corrupt / needs_category."""
        with transaction(self.db_path) as db:
            def run():
                out = []
                for f in files:
                    digest = sha256_bytes(f["data"])
                    if db.execute("SELECT 1 FROM knowledge WHERE project_id=? AND sha256=?", (project_id, digest)).fetchone():
                        out.append(dict(name=f["name"], status="duplicate"))
                        continue
                    try:
                        text = f["data"].decode("utf-8")
                    except UnicodeDecodeError:
                        out.append(dict(name=f["name"], status="corrupt", diagnostic="不是 UTF-8 文本"))
                        continue
                    status = "readable" if f.get("kind") else "needs_category"
                    kid = new_id()
                    db.execute("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (kid, project_id, f.get("kind") or "uncategorized", f["name"], text, f.get("source", f["name"]),
                                digest, status, f.get("episodeId"), self.clock()))
                    out.append(dict(name=f["name"], status=status, id=kid))
                return out
            return self._command(db, command_id, "knowledge", dict(p=project_id, f=[sha256_bytes(f["data"]) for f in files]),
                                 run)

    def similar(self, project_id, text, limit=5):
        """Local text retrieval over the ledger and knowledge base; returns located, quotable hits."""
        q = bigrams(text)
        with connect(self.db_path) as db:
            rows = [dict(kind="ledger", id=r["id"], title=r["content"][:30], body=r["content"], episode=r["display_code"],
                         source=r["source_ref"]) for r in db.execute(
                "SELECT l.*, e.display_code FROM ledger l JOIN episode e ON e.id=l.episode_id WHERE l.project_id=?", (project_id,))]
            rows += [dict(kind="knowledge", id=r["id"], title=r["title"], body=r["body"], episode=None, source=r["source"])
                     for r in db.execute("SELECT * FROM knowledge WHERE project_id=? AND status='readable'", (project_id,))]
        scored = []
        for r in rows:
            b = bigrams(r["body"])
            score = len(q & b) / max(1, len(q | b))
            if score > 0:
                common = sorted(q & b)
                scored.append(dict(r, score=round(score, 3), overlap="".join(common[:12]),
                                   difference=sorted(b - q)[:8]))
        return sorted(scored, key=lambda r: -r["score"])[:limit]

    def cite(self, task_id, knowledge_id, reason, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                knowledge = row(db, "SELECT project_id FROM knowledge WHERE id=?", knowledge_id)
                task = row(db, "SELECT project_id FROM task WHERE id=?", task_id)
                if task["project_id"] != knowledge["project_id"]:
                    raise Rejected("不能引用其他项目的知识条目")
                db.execute("INSERT OR IGNORE INTO citation VALUES(?,?,?,?,?,?,?)",
                           (new_id(), task_id, knowledge_id, reason, "pending", None, self.clock()))
                return dict(ok=True)
            return self._command(db, command_id, "cite", dict(t=task_id, k=knowledge_id), run)

    def add_topic(self, project_id, code, premise, mechanism="", motivation="", command_id=None, origin="user"):
        with transaction(self.db_path) as db:
            def run():
                if not code.strip() or not premise.strip():
                    raise Rejected("请填写选题编号和故事前提")
                if db.execute("SELECT 1 FROM topic WHERE project_id=? AND code=?", (project_id, code)).fetchone():
                    raise Conflict("该选题编号已存在")
                tid = new_id()
                db.execute("INSERT INTO topic VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (tid, project_id, code, premise, mechanism, motivation, "候选", None, origin, self.clock()))
                return dict(topicId=tid, status="候选")
            return self._command(db, command_id, "topic", dict(p=project_id, c=code, pr=premise), run)

    def topics(self, project_id, status=None):
        with connect(self.db_path) as db:
            out = [dict(r) for r in db.execute("SELECT t.*, e.display_code, e.state AS episode_state FROM topic t LEFT JOIN "
                                               "episode e ON e.id=t.episode_id WHERE t.project_id=? ORDER BY t.code",
                                               (project_id,)) if not status or r["status"] == status]
        for t in out:
            t["similar"] = self.similar(project_id, t["premise"] + t["mechanism"], 3)
        return out

    def _topic_on_kickoff(self, db, episode_id, topic_id):
        t = row(db, "SELECT * FROM topic WHERE id=?", topic_id, missing="选题卡不存在")
        ep = self._episode_in(db, episode_id)
        if ep["project_id"] != t["project_id"] or t["status"] != "候选" or t["episode_id"]:
            raise Conflict("请选择本项目尚未开工的候选卡，已开工的卡请进入对应集")
        db.execute("UPDATE topic SET status='开发中', episode_id=? WHERE id=?", (episode_id, topic_id))
        return t

    def drop_topic(self, topic_id, command_id=None):
        with transaction(self.db_path) as db:
            db.execute("UPDATE topic SET status='弃用' WHERE id=?", (topic_id,))
        return dict(topicId=topic_id, status="弃用")

    # ============================================================ story groups
    def create_group(self, project_id, name, members: list[dict], shared_limits="", command_id=None):
        with transaction(self.db_path) as db:
            def run():
                gid = new_id()
                db.execute("INSERT INTO story_group VALUES(?,?,?,?,?)", (gid, project_id, name, shared_limits, self.clock()))
                for n, m in enumerate(members, 1):
                    self._episode_in(db, m["episodeId"], project_id)
                    db.execute("INSERT INTO group_member VALUES(?,?,?,?,?,?)",
                               (gid, m["episodeId"], m.get("chapter", ""), n, m.get("ending", ""), m.get("nextHook", "")))
                return dict(groupId=gid)
            return self._command(db, command_id, "group", dict(p=project_id, n=name, m=members), run)

    def groups(self, project_id):
        with connect(self.db_path) as db:
            return [dict(r) for r in db.execute("SELECT id,name FROM story_group WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def group_view(self, group_id):
        with connect(self.db_path) as db:
            g = row(db, "SELECT * FROM story_group WHERE id=?", group_id)
            g["members"] = [dict(r) for r in db.execute(
                "SELECT m.*, e.display_code, e.state, e.leftover_facts FROM group_member m JOIN episode e ON e.id=m.episode_id "
                "WHERE group_id=? ORDER BY ordinal", (group_id,))]
        return g

    def continue_in_group(self, group_id, display_code, command_id):
        """承接开新一集: preselects the last member as predecessor and shows its leftovers."""
        g = self.group_view(group_id)
        last = g["members"][-1]
        ep = self.create_episode(g["project_id"], display_code, command_id=command_id)
        with transaction(self.db_path) as db:
            db.execute("INSERT INTO group_member VALUES(?,?,?,?,?,?)", (group_id, ep["episodeId"], last["chapter"],
                                                                       len(g["members"]) + 1, "", ""))
        return dict(ep, predecessor=last["episode_id"], preview=self.kickoff_preview(ep["episodeId"], "continue",
                                                                                     [last["episode_id"]]))

    # ============================================================ models and executors
    def set_quota_retry_policy(self, policy):
        if policy not in ("same_cli", "manual_cli"):
            raise Rejected("请选择本机 CLI 的限流重试方式")
        self.put_setting("quota_retry_policy", policy)
        return {"policy": policy}

    def model_catalog(self):
        return dict(text={k:v for k,v in text_catalog().items() if k in self.executors}, image=self.image_providers(),
                    quotaRetryPolicy=self.get_setting("quota_retry_policy", "manual_cli"))

    def validate_selection(self, provider, model, effort):
        cat = text_catalog().get(provider)
        if provider not in self.executors:
            raise Rejected("未配置的执行器")
        if not cat:
            raise Rejected("未知执行器")
        entry = next((m for m in cat["models"] if m["id"] == model), None)
        if model is not None and (not isinstance(model, str) or not re.fullmatch(r"[\w.:-][\w./:-]{0,180}",model)):
            raise Rejected("原生型号格式无效")
        if effort is not None and (not isinstance(effort, str) or not re.fullmatch("[a-z0-9_-]{1,25}",effort)):
            raise Rejected("原生强度格式无效")
        if entry is None:
            return dict(provider=provider, model=model, effort=effort, status="pending", note="原生型号，能力待检测")
        if effort not in entry["efforts"]:
            raise Rejected(f"{model or '默认型号'} 不支持强度 {effort}，请重选或使用默认", detail=dict(allowed=entry["efforts"]))
        return dict(provider=provider, model=model, effort=effort, status=entry["status"])

    def set_task_executor(self, task_id, role, provider, model=None, effort=None):
        if role not in ("create","review"):
            raise Rejected("执行角色须为创作或独审")
        self.validate_selection(provider, model, effort)
        with transaction(self.db_path) as db:
            t = row(db, "SELECT executor_plan FROM task WHERE id=?", task_id)
            plan = json.loads(t["executor_plan"])
            plan[role] = dict(provider=provider, model=model, effort=effort)
            db.execute("UPDATE task SET executor_plan=? WHERE id=?", (dumps(plan), task_id))
        return plan

    # ============================================================ method pack versions
    def project_stage_methods(self, project_id, stage):
        if stage not in {"topic", "group"}:
            raise Rejected("项目阶段须为选题或连续故事")
        with connect(self.db_path) as db:
            project = row(db, "SELECT config_id,method_pack FROM project WHERE id=?", project_id)
        pack = load_pack(project["method_pack"])
        return dict(ref=pack["id"], sections=method_entries(pack, stage, project["config_id"]))

    def pack_versions(self):
        return available_packs()

    def compare_packs(self, original, candidate, project_id=None):
        profile = None
        if project_id:
            with connect(self.db_path) as db:
                profile = row(db, "SELECT config_id FROM project WHERE id=?", project_id)["config_id"]
        try:
            return pack_changes(load_pack(original), load_pack(candidate), profile)
        except (ValueError, OSError, KeyError) as exc:
            raise Rejected("方法包读取或指纹核对失败：" + str(exc)) from exc

    def regress_pack(self, ref, samples=None):
        """Replay isolated samples through the new pack's prompts and contract checks. Not counted as real quality."""
        try:
            pack = load_pack(ref)
        except (OSError, KeyError, ValueError) as exc:
            raise Rejected("方法包读取或指纹核对失败：" + str(exc)) from exc
        results = []
        for s in samples or [dict(name="夜班柴犬样例", settings=[], idea="一只过分认真的柴犬第一次值夜班")]:
            entries = prompts.creation_inputs(pack, prompts.render_settings(s["settings"]), s["idea"], "story", None, s["idea"])
            leaked = [e["name"] for e in entries if re.search(r"MILO|LEO|PUGGY", e["text"] or "")] if not s["settings"] else []
            results.append(dict(sample=s["name"], inputs=len(entries), leaked=leaked, ok=not leaked))
        roots = pack.get("stages", {"story": [], "story_review": []})
        for stage in roots:
            entries = method_entries(pack, stage)
            leaked = [e["name"] for e in entries if re.search(r"milo|leo|puggy|女主人|suno|fourthwall|/Users/", e["text"], re.I)]
            results.append(dict(sample=stage, inputs=len(entries), leaked=leaked, ok=bool(entries) and not leaked))
        return dict(ref=ref, versions=available_packs(), files=pack["files"], results=results,
                    ok=all(r["ok"] for r in results), counted=False)

    def adopt_pack(self, project_id, ref, command_id, expected_ref=None):
        reg = self.regress_pack(ref)
        if not reg["ok"]:
            raise Rejected("隔离样例回归未通过", detail=reg)
        with transaction(self.db_path) as db:
            def run():
                project = row(db, "SELECT method_pack FROM project WHERE id=?", project_id)
                if expected_ref is not None and project["method_pack"] != expected_ref:
                    raise Conflict("项目方法版本已更新，请刷新后重新比较", current=project["method_pack"])
                db.execute("UPDATE project SET method_pack=? WHERE id=?", (ref, project_id))
                db.execute("INSERT INTO pack_adoption VALUES(?,?,?,?,?)", (new_id(), project_id, ref, dumps(reg), self.clock()))
                return dict(projectId=project_id, methodPack=ref)
            payload = dict(p=project_id, r=ref)
            if expected_ref is not None:
                payload["expected"] = expected_ref
            return self._command(db, command_id, "adopt_pack", payload, run)

    # ============================================================ backup / restore
    def backup(self, note=""):
        bid = new_id()
        target = self.store.root / "backups" / bid
        target.mkdir(parents=True)
        src = sqlite3.connect(self.db_path)
        dst = sqlite3.connect(target / "workbench.sqlite")
        src.backup(dst)          # consistent snapshot
        src.close()
        dst.close()
        files = {}
        roots = ("blobs", "projects", "runs", "director-palettes", "packages")
        for sub in roots:
            for p in (self.store.root / sub).rglob("*") if (self.store.root / sub).exists() else []:
                parts = p.relative_to(self.store.root/sub).parts
                if ((sub=="packages" and len(parts)>=2 and parts[1].startswith(".staging-")) or
                    (sub=="projects" and len(parts)>=5 and parts[1]=="episodes" and parts[3]=="deliveries"
                     and parts[4].startswith(".staging-"))):
                    continue
                if p.is_symlink() and (not p.is_file() or not p.resolve().is_relative_to(self.store.root/sub)):
                    raise Rejected("备份资料包含符号链接，请核对原文件：" + p.relative_to(self.store.root).as_posix())
                if p.is_file():
                    rel = p.relative_to(self.store.root).as_posix()
                    dest = target / "files" / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
                    files[rel] = file_sha256(dest)
        manifest = dict(backupId=bid, db=file_sha256(target/"workbench.sqlite"), files=files, resourceDirs=roots)
        write_atomic(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        with transaction(self.db_path) as db:
            db.execute("INSERT INTO backup VALUES(?,?,?,?,?,?)", (bid, f"backups/{bid}", sha256_bytes(canonical(manifest)),
                                                                 len(files), note, self.clock()))
        return dict(backupId=bid, files=len(files))

    def restore(self, backup_id, target_dir):
        """Restore into a separate directory, verify every file, then report; the live data is untouched.
        The caller switches ADW_DATA_DIR to the verified directory. Unknown paid requests stay unknown."""
        with connect(self.db_path) as db:
            record = row(db, "SELECT relpath,manifest_hash FROM backup WHERE id=?", backup_id, missing="备份未登记")
        src = self.store._inside(self.store.root / record["relpath"])
        try:
            manifest = json.loads((src / "manifest.json").read_bytes())
        except (OSError, ValueError) as exc:
            raise Rejected("无法读取备份清单") from exc
        if sha256_bytes(canonical(manifest)) != record["manifest_hash"] or manifest.get("backupId") != backup_id:
            raise Rejected("备份清单与登记指纹不一致")
        target = Path(target_dir).expanduser().resolve()
        if target.is_relative_to(self.store.root):
            raise Rejected("请在当前工作台数据目录以外选择新的恢复目录")
        pre = self.backup(note=f"恢复 {backup_id} 前的快照")
        files = [(src/"files"/rel, Path(rel), digest) for rel,digest in manifest["files"].items()]
        # Install the database last, after the restored resources have passed read-back checks.
        files.append((src/"workbench.sqlite", Path("workbench.sqlite"), manifest["db"]))
        for source, relative, digest in files:
            if relative.is_absolute() or ".." in relative.parts or not source.resolve().is_relative_to(src):
                raise Rejected("备份文件超出登记目录")
            if not source.is_file() or file_sha256(source) != digest:
                raise Rejected("备份原件校验失败：" + str(relative), detail=dict(preRestoreBackup=pre["backupId"]))
        try:
            target.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise Rejected(f"恢复目录不可写：{target}（{exc.strerror or exc}）", detail=dict(preRestoreBackup=pre["backupId"])) from exc
        for source, relative, digest in files:
            dest = target / relative
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as origin, dest.open("xb") as output:
                    shutil.copyfileobj(origin, output)
                if file_sha256(dest) != digest:
                    raise ValueError("副本与登记指纹不一致")
            except (OSError, ValueError) as exc:
                raise Rejected(f"恢复未完成，已复制的文件保留在 {target}：{relative}（{exc}）",
                               detail=dict(preRestoreBackup=pre["backupId"])) from exc
        notes = [] if "resourceDirs" in manifest else ["旧版备份只收录数据库、原文与素材副本、项目和运行记录。如需自定义色卡原文件或导出的 H3 ZIP，请从原数据目录取回。"]
        return dict(restoredTo=str(target), files=len(manifest["files"]), preRestoreBackup=pre["backupId"], notes=notes)

    def storage_status(self):
        root = self.store.root
        writable = os.access(root, os.W_OK)
        with connect(self.db_path) as db:
            last = db.execute("SELECT * FROM backup ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(dataDir=str(root), writable=writable, lastBackup=dict(last) if last else None)

    # ============================================================ todos and task records
    def project_todos(self, project_id):
        todos = []
        with connect(self.db_path) as db:
            for c in db.execute("SELECT c.*, e.display_code FROM closure c JOIN episode e ON e.id=c.episode_id WHERE "
                                "e.project_id=? AND c.state IN ('open','staged')", (project_id,)):
                n = db.execute("SELECT count(*) FROM proposal_item WHERE closure_id=?", (c["id"],)).fetchone()[0]
                todos.append(dict(kind="closure", episodeId=c["episode_id"], objectId=c["id"],
                                  title=f"{c['display_code']} 收尾：{n} 条设定提议待处理", action="去处理"))
            for s in db.execute("SELECT s.*, e.display_code FROM space_item s JOIN episode e ON e.id=s.episode_id WHERE "
                                "e.project_id=? AND s.status='pending'", (project_id,)):
                segs = "、".join(json.loads(s["depends"]))
                todos.append(dict(kind="space", episodeId=s["episode_id"], objectId=s["id"],
                                  title=f"{s['display_code']} · {segs} {s['label']}待确认", action="去确认空间"))
            for b in db.execute("SELECT b.*, e.display_code FROM image_batch b JOIN episode e ON e.id=b.episode_id WHERE "
                                "e.project_id=? AND b.state='pending_reconcile'", (project_id,)):
                todos.append(dict(kind="reconcile", episodeId=b["episode_id"], objectId=b["id"],
                                  title=f"{b['display_code']} 图像请求结果待核对", action="查原批次"))
        return todos

    def activity_log(self, project_id=None, offset=0, limit=40):
        with connect(self.db_path) as db:
            return activity.page(db,project_id,offset,limit)

    def task_records(self, project_id=None, episode_id=None, stage=None, state=None):
        sql = ("SELECT t.*, e.display_code FROM task t JOIN episode e ON e.id=t.episode_id WHERE 1=1"
               + (" AND t.project_id=?" if project_id else "") + (" AND t.episode_id=?" if episode_id else "")
               + (" AND t.stage=?" if stage else "") + (" AND t.state=?" if state else "") + " ORDER BY t.created_at DESC")
        args = [a for a in (project_id, episode_id, stage, state) if a]
        with connect(self.db_path) as db:
            out = []
            for t in db.execute(sql, args):
                t = self._task_view(db, dict(t))
                for r in t["runs"]:
                    try:
                        manifest, digest = self.store.load_manifest(r["manifest_relpath"])
                        if digest != r["input_hash"]:
                            raise ValueError("指纹不符")
                        r["methodPack"] = manifest.get("methodPack", {}).get("id")
                        r["methods"] = [e["name"] for e in manifest["inputs"] if e["role"] in ("method", "project_method")]
                    except (OSError,ValueError,KeyError,TypeError) as exc:
                        r.update(methodPack=None,methods=[],manifestError="无法核验运行输入清单："+str(exc))
                    r["events"] = [json.loads(e[0]) for e in db.execute("SELECT payload FROM event WHERE run_id=? ORDER BY seq",
                                                                        (r["id"],))]
                out.append(t)
        return out

    # ============================================================ overview for the project home (J16)
    def project_overview(self, project_id):
        from . import timeline
        p = self.project_view(project_id)
        out = []
        with connect(self.db_path) as db:
            for ep in p["episodes"]:
                adopted = {r["stage"]: r["adopted_version_id"] for r in db.execute(
                    "SELECT stage, adopted_version_id FROM artifact WHERE episode_id=?", (ep["id"],))}
                drafts = {r["stage"]: r["n"] for r in db.execute(
                    "SELECT a.stage, count(v.id) AS n FROM artifact a JOIN version v ON v.artifact_id=a.id WHERE a.episode_id=? "
                    "GROUP BY a.stage", (ep["id"],))}
                reviewed = db.execute("SELECT count(*) FROM review r JOIN version v ON v.id=r.version_id JOIN artifact a ON "
                                      "a.id=v.artifact_id WHERE a.episode_id=? AND a.stage='story'", (ep["id"],)).fetchone()[0]
                deliveries = [dict(r) for r in db.execute("SELECT id, scope, created_at FROM delivery WHERE episode_id=? "
                                                          "ORDER BY created_at", (ep["id"],))]
                closure = db.execute("SELECT id, state FROM closure WHERE episode_id=?", (ep["id"],)).fetchone()
                plan = self._adopted_content(db, ep["id"], "plan")
                lay = timeline.layout(plan) if plan else None
                done = sum(1 for s in ("story", "script", "plan") if adopted.get(s))
                if plan:
                    done += 3 if self._space_ok(ep["id"]) else 2
                if adopted.get("prompts"):
                    done += 2
                if adopted.get("publish"):
                    done += 1
                full = [d for d in deliveries if d["scope"] == "full"]
                if full:
                    done = 11
                if full:
                    label = "图文已交付" + (" · 待收尾" if closure and closure["state"] in ("open", "staged") else "")
                    tone = "blue" if closure and closure["state"] in ("open", "staged") else "green"
                elif adopted.get("story"):
                    label, tone = ("制作中" if adopted.get("script") else "故事已成稿"), "green"
                elif reviewed:
                    label, tone = "故事稿待审", "amber"
                elif drafts.get("story"):
                    label, tone = "开发中", "blue"
                elif ep.get("kickoff_id"):
                    label, tone = "已开工", "blue"
                else:
                    label, tone = "待开工", ""
                out.append(dict(ep, adopted=adopted, progress=done, label=label, tone=tone, deliveries=deliveries,
                                closure=dict(closure) if closure else None,
                                segments=len(lay["segments"]) if lay else 0, totalMs=lay["total_ms"] if lay else 0))
        required = sum(1 for s in p["settings"] if s["strength"] == "required")
        return dict(project=p, episodes=out, settings=dict(total=len(p["settings"]), required=required,
                                                            variable=len(p["settings"]) - required),
                    todos=self.project_todos(project_id))

    def _space_ok(self, episode_id):
        return not self.blocked_segments(episode_id)
