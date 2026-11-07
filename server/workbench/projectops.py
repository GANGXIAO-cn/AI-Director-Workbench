"""Project-level operations: closure, settings/spec changes, knowledge, topics, groups, expert mode,
model catalog, method-pack versions, backup/restore, todos and task records."""
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

from . import contracts, prompts
from .db import connect, dumps, new_id, transaction
from .errors import Conflict, NotFound, Rejected, loads, row
from .methodpack import load_pack
from .stages import render
from .storage import canonical, sha256_bytes, write_atomic

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

STOP_KINDS = ("confirm_point", "paid_batch", "rounds_exhausted", "structural_block", "model_conflict",
              "unknown_request", "settings_change", "required_violation", "isolation_unverified", "waiting_quota")
MANDATORY_STOPS = ("paid_batch", "rounds_exhausted", "structural_block", "unknown_request", "required_violation",
                   "isolation_unverified", "waiting_quota")


def bigrams(text):
    t = re.sub(r"\s+", "", text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


class ProjectOpsMixin:
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
        self._run_proposal(run_id, cid)
        return dict(closureId=cid, runId=run_id)

    def _run_proposal(self, run_id, closure_id):
        with transaction(self.db_path) as db:
            run = row(db, "SELECT * FROM run WHERE id=?", run_id)
            db.execute("UPDATE run SET status='running', started_at=? WHERE id=?", (self.clock(), run_id))
        manifest, digest = self.store.load_manifest(run["manifest_relpath"])
        entries = self.store.manifest_texts(manifest)
        trailer = (f"运行号：{run_id}\n输入指纹：{digest}\n回包为 kind=proposal：items 中每项写类别、内容、真实出处（source 的 "
                   "objectId/versionId 填采用稿版本）、建议强度（required/variable）和提议依据。只提可以长期复用的设定。")
        from .executors.base import ExecRequest
        workdir = self.store.run_dir(run_id) / "work"
        workdir.mkdir(parents=True, exist_ok=True)
        outcome = self.executors[run["provider"]].start(ExecRequest(
            run_id=run_id, purpose="proposal", kind="proposal", prompt=prompts.prompt_from(entries, trailer, "proposal"),
            schema=contracts.branch_schema("proposal"), session_mode="new", workdir=workdir))
        result = outcome.structured if outcome.ok else None
        problems = contracts.validate(result) if result else [outcome.error_class or "failed"]
        if result and not problems and (result.get("kind") != "proposal" or result["runId"] != run_id
                                        or result["inputFingerprint"] != digest):
            problems = ["回包不属于本次运行"]
        with transaction(self.db_path) as db:
            if problems:
                db.execute("UPDATE run SET status='failed', reason=?, ended_at=? WHERE id=?", ("; ".join(problems)[:300],
                                                                                            self.clock(), run_id))
                db.execute("UPDATE closure SET state='failed' WHERE id=?", (closure_id,))
                return
            for n, it in enumerate(result["items"], 1):
                db.execute("INSERT INTO proposal_item(id,closure_id,ordinal,category,content,source_ref,suggested_strength,"
                           "rationale) VALUES(?,?,?,?,?,?,?,?)", (new_id(), closure_id, n, it["category"], it["content"],
                                                                  dumps(it["source"]), it["suggestedStrength"], it["rationale"]))
            db.execute("UPDATE run SET status='succeeded', ended_at=?, session_id=? WHERE id=?",
                       (self.clock(), outcome.session_id, run_id))
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
                                f"{ep['display_code']} 交付 {c['delivery_id'][:8]} · {it['source_ref']}", it["reason"]))
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

    def update_preset(self, project_id, values: dict, command_id):
        """Changing the shared preset never touches episodes that already copied it at kickoff."""
        with transaction(self.db_path) as db:
            def run():
                p = row(db, "SELECT * FROM project WHERE id=?", project_id)
                new = dict(json.loads(p["spec_preset"]), **values)
                db.execute("UPDATE project SET spec_preset=?, revision=revision+1 WHERE id=?", (dumps(new), project_id))
                rev = db.execute("SELECT coalesce(max(rev),0)+1 FROM preset WHERE project_id=? AND name='default'",
                                 (project_id,)).fetchone()[0]
                db.execute('INSERT INTO preset(id,project_id,name,rev,"values",created_at) VALUES(?,?,?,?,?,?)',
                           (new_id(), project_id, "default", rev, dumps(new), self.clock()))
                return dict(preset=new, rev=rev)
            return self._command(db, command_id, "preset", dict(p=project_id, v=values), run)

    def episode_spec_change(self, episode_id, key, value, command_id, confirm=False):
        with transaction(self.db_path) as db:
            k = self._kickoff(db, episode_id)
            plan = self._adopted_content(db, episode_id, "plan") or {}
            impacts = []
            if key == "aspectRatio":
                impacts = [dict(target=f"镜头构图 {s['id']}", reason="画幅变化") for s in plan.get("shots", [])]
                impacts += [dict(target=f"九宫格 {v['name']} v{v['version_no']}", reason="画布随画幅")
                            for v in self.episode_assets(episode_id) if v["grid"]]
                impacts += [dict(target=f"状态图 {v['name']} v{v['version_no']}", reason="构图随画幅")
                            for v in self.episode_assets(episode_id) if v["category"] == "state"]
                impacts += [dict(target="品牌片尾", reason="画幅"), dict(target="打包参数 ratio", reason="画幅")]
            preview = dict(key=key, old=k["spec"].get(key), new=value, impacts=impacts,
                           kept=[f"身份图 {v['name']} v{v['version_no']}" for v in self.episode_assets(episode_id)
                                 if v["category"] == "identity"])
            if not confirm:
                return dict(preview=preview)
            def run():
                spec = dict(k["spec"], **{key: value})
                db.execute("UPDATE kickoff SET spec=? WHERE episode_id=?", (dumps(spec), episode_id))
                db.execute("INSERT INTO spec_change VALUES(?,?,?,?,?,?,?)", (new_id(), episode_id, key, dumps(k["spec"].get(key)),
                                                                             dumps(value), dumps(impacts), self.clock()))
                ep = row(db, "SELECT * FROM episode WHERE id=?", episode_id)
                self._record_impacts(db, dict(project_id=ep["project_id"], episode_id=episode_id), impacts, "spec_change",
                                     f"spec:{key}")
                return dict(preview=preview, spec=spec)
            return self._command(db, command_id, "spec_change", dict(e=episode_id, k=key, v=value), run)

    # ============================================================ knowledge and topics
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
                row(db, "SELECT id FROM knowledge WHERE id=?", knowledge_id)
                db.execute("INSERT OR IGNORE INTO citation VALUES(?,?,?,?,?,?,?)",
                           (new_id(), task_id, knowledge_id, reason, "pending", None, self.clock()))
                return dict(ok=True)
            return self._command(db, command_id, "cite", dict(t=task_id, k=knowledge_id), run)

    def add_topic(self, project_id, code, premise, mechanism="", motivation="", command_id=None, origin="user"):
        with transaction(self.db_path) as db:
            def run():
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

    # ============================================================ expert mode
    def authorize_expert(self, project_id, episodes, stop_stage, division, image_cap=0, stops=(), command_id=None):
        for s in stops:
            if s not in STOP_KINDS:
                raise Rejected(f"未知停点 {s}")
        with transaction(self.db_path) as db:
            def run():
                aid = new_id()
                all_stops = sorted(set(stops) | set(MANDATORY_STOPS))
                db.execute("INSERT INTO expert_auth VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (aid, project_id, dumps(episodes), stop_stage, dumps(division), image_cap, dumps(all_stops),
                            "authorized", None, "[]", self.clock()))
                return dict(authId=aid, stops=all_stops, mandatory=list(MANDATORY_STOPS))
            return self._command(db, command_id, "expert", dict(p=project_id, e=episodes, s=stop_stage), run)

    def expert_run(self, auth_id):
        """Advance the authorised scope until a stop. Paid batches always stop; required reviews wait when the
        reviewer's isolation is unverified; rounds are never reset."""
        log = []
        with connect(self.db_path) as db:
            auth = loads(row(db, "SELECT * FROM expert_auth WHERE id=?", auth_id), "episodes", "division", "stops")

        def stop(reason, **info):
            with transaction(self.db_path) as db:
                db.execute("UPDATE expert_auth SET state='stopped', stop_reason=?, log=? WHERE id=?",
                           (dumps(dict(reason=reason, **info)), dumps(log), auth_id))
            return dict(state="stopped", reason=reason, log=log, **info)

        for eid in auth["episodes"]:
            view = self.episode_view(eid)
            story = next(a for a in view["artifacts"] if a["stage"] == "story")
            task = story["tasks"][-1] if story["tasks"] else None
            if not task:
                res = self.create_task(eid, "story", "专家模式：写本集故事稿", auto_revise=True,
                                       executor=dict(create=auth["division"].get("create", {}),
                                                     review=auth["division"].get("review", {})) if auth["division"] else None,
                                       command_id=f"{auth_id}:{eid}:task")
                self._drain(res["runId"], log)
                task_id = res["taskId"]
            else:
                task_id = task["id"]
            for _ in range(6):
                view = self.episode_view(eid)
                story = next(a for a in view["artifacts"] if a["stage"] == "story")
                task = next(t for t in story["tasks"] if t["id"] == task_id)
                if story["adopted_version_id"]:
                    break
                if task["state"] == "waiting_quota":
                    return stop("waiting_quota", episode=eid)
                if task["state"] == "rounds_exhausted":
                    return stop("rounds_exhausted", episode=eid, usedRounds=task["used_rounds"])
                latest = story["versions"][-1] if story["versions"] else None
                if not latest:
                    return stop("structural_block", episode=eid, detail="没有草稿")
                gate = latest["gate"]
                if gate["blocking"]:
                    return stop("required_violation", episode=eid, versionId=latest["id"])
                if gate["review"] is None:
                    try:
                        rv = self.submit_review(latest["id"], f"{auth_id}:{latest['id']}:review", task_id=task_id)
                    except Rejected as exc:
                        if "隔离" in str(exc):
                            return stop("isolation_unverified", episode=eid, detail=str(exc))
                        raise
                    self._drain(rv["runId"], log)
                    continue
                if gate["adoptable"]:
                    self.adopt(story["id"], latest["id"], story["adopted_version_id"], f"{auth_id}:{latest['id']}:adopt")
                    log.append(dict(adopted=latest["id"]))
                    break
                if gate["unresolved"]:
                    return stop("confirm_point", episode=eid, detail="普通意见需要导演决定是否放行")
                if "confirm_point" in auth["stops"]:
                    return stop("confirm_point", episode=eid)
            if auth["stop_stage"] == "story":
                continue
        with transaction(self.db_path) as db:
            db.execute("UPDATE expert_auth SET state='done', log=? WHERE id=?", (dumps(log), auth_id))
        return dict(state="done", log=log)

    def _drain(self, run_id, log):
        queue = [run_id]
        while queue:
            rid = queue.pop(0)
            queue += self.execute_run(rid)
            log.append(dict(run=rid))

    # ============================================================ models and executors
    def model_catalog(self):
        return dict(text=MODEL_CATALOG, image=self.image_providers())

    def validate_selection(self, provider, model, effort):
        cat = MODEL_CATALOG.get(provider)
        if not cat:
            raise Rejected("未知执行器")
        entry = next((m for m in cat["models"] if m["id"] == model), None)
        if entry is None:
            return dict(provider=provider, model=model, effort=effort, status="pending", note="原生型号，能力待检测")
        if effort not in entry["efforts"]:
            raise Rejected(f"{model or '默认型号'} 不支持强度 {effort}，请重选或使用默认", detail=dict(allowed=entry["efforts"]))
        return dict(provider=provider, model=model, effort=effort, status=entry["status"])

    def set_task_executor(self, task_id, role, provider, model=None, effort=None):
        self.validate_selection(provider, model, effort)
        with transaction(self.db_path) as db:
            t = row(db, "SELECT executor_plan FROM task WHERE id=?", task_id)
            plan = json.loads(t["executor_plan"])
            plan[role] = dict(provider=provider, model=model, effort=effort)
            db.execute("UPDATE task SET executor_plan=? WHERE id=?", (dumps(plan), task_id))
        return plan

    # ============================================================ method pack versions
    def regress_pack(self, ref, samples=None):
        """Replay isolated samples through the new pack's prompts and contract checks. Not counted as real quality."""
        pack = load_pack(ref)
        results = []
        for s in samples or [dict(name="夜班柴犬样例", settings=[], idea="一只过分认真的柴犬第一次值夜班")]:
            entries = prompts.creation_inputs(pack, prompts.render_settings(s["settings"]), s["idea"], "story", None, s["idea"])
            leaked = [e["name"] for e in entries if re.search(r"MILO|LEO|PUGGY", e["text"] or "")] if not s["settings"] else []
            results.append(dict(sample=s["name"], inputs=len(entries), leaked=leaked, ok=not leaked))
        return dict(ref=ref, files=pack["files"], results=results, ok=all(r["ok"] for r in results), counted=False)

    def adopt_pack(self, project_id, ref, command_id):
        reg = self.regress_pack(ref)
        if not reg["ok"]:
            raise Rejected("隔离样例回归未通过", detail=reg)
        with transaction(self.db_path) as db:
            def run():
                db.execute("UPDATE project SET method_pack=? WHERE id=?", (ref, project_id))
                db.execute("INSERT INTO pack_adoption VALUES(?,?,?,?,?)", (new_id(), project_id, ref, dumps(reg), self.clock()))
                return dict(projectId=project_id, methodPack=ref)
            return self._command(db, command_id, "adopt_pack", dict(p=project_id, r=ref), run)

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
        for sub in ("blobs", "projects", "runs"):
            for p in (self.store.root / sub).rglob("*") if (self.store.root / sub).exists() else []:
                if p.is_file():
                    rel = p.relative_to(self.store.root).as_posix()
                    dest = target / "files" / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
                    files[rel] = sha256_bytes(p.read_bytes())
        manifest = dict(backupId=bid, db=sha256_bytes((target / "workbench.sqlite").read_bytes()), files=files)
        write_atomic(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        with transaction(self.db_path) as db:
            db.execute("INSERT INTO backup VALUES(?,?,?,?,?,?)", (bid, f"backups/{bid}", sha256_bytes(canonical(manifest)),
                                                                 len(files), note, self.clock()))
        return dict(backupId=bid, files=len(files))

    def restore(self, backup_id, target_dir):
        """Restore into a separate directory, verify every file, then report; the live data is untouched.
        The caller switches ADW_DATA_DIR to the verified directory. Unknown paid requests stay unknown."""
        src = self.store.root / "backups" / backup_id
        manifest = json.loads((src / "manifest.json").read_text())
        target = Path(target_dir)
        pre = self.backup(note=f"恢复 {backup_id} 前的快照")
        try:
            target.mkdir(parents=True, exist_ok=False)
            probe = target / ".write-test"
            probe.write_bytes(b"x")
            probe.unlink()
        except OSError as exc:
            raise Rejected(f"恢复目录不可写：{target}（{exc.strerror or exc}）", detail=dict(preRestoreBackup=pre["backupId"]))
        shutil.copy2(src / "workbench.sqlite", target / "workbench.sqlite")
        bad = []
        for rel, digest in manifest["files"].items():
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / "files" / rel, dest)
            if sha256_bytes(dest.read_bytes()) != digest:
                bad.append(rel)
        if sha256_bytes((target / "workbench.sqlite").read_bytes()) != manifest["db"]:
            bad.append("workbench.sqlite")
        if bad:
            shutil.rmtree(target, ignore_errors=True)
            raise Rejected("恢复校验失败，保留原数据", detail=dict(files=bad, preRestoreBackup=pre["backupId"]))
        return dict(restoredTo=str(target), files=len(manifest["files"]), preRestoreBackup=pre["backupId"])

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
                                  title=f"{s['display_code']} · {segs} {s['label']}待确认", action="去本集素材"))
            for b in db.execute("SELECT b.*, e.display_code FROM image_batch b JOIN episode e ON e.id=b.episode_id WHERE "
                                "e.project_id=? AND b.state='pending_reconcile'", (project_id,)):
                todos.append(dict(kind="reconcile", episodeId=b["episode_id"], objectId=b["id"],
                                  title=f"{b['display_code']} 图像请求结果待核对", action="查原批次"))
        return todos

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
                    manifest, _ = self.store.load_manifest(r["manifest_relpath"])
                    r["methodPack"] = manifest.get("methodPack", {}).get("id")
                    r["events"] = [json.loads(e[0]) for e in db.execute("SELECT payload FROM event WHERE run_id=? ORDER BY seq",
                                                                        (r["id"],))]
                out.append(t)
        return out
