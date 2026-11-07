"""Two-pass prompt review per segment (cold read → comparison) with partial re-review."""
import json

from . import prompts, timeline
from .db import connect, dumps, new_id, transaction
from .errors import Rejected, loads, row
from .storage import canonical, sha256_bytes
from .stages import render


class PromptReviewMixin:
    def _segment_material(self, db, version_id, segment):
        v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
        art = row(db, "SELECT * FROM artifact WHERE id=?", v["artifact_id"])
        if art["stage"] != "prompts":
            raise Rejected("两遍独审针对提示词版本")
        entry = (v["content"].get("segments") or {}).get(segment)
        if not entry:
            raise Rejected(f"版本中没有 {segment}")
        refs = []
        for ref in entry.get("refs", []):
            av = self.asset_version(ref["assetVersionId"], db)
            refs.append(dict(ref, sha256=av["sha256"], name=av["name"]))
        own = sha256_bytes(canonical(dict(txt=entry.get("txt", ""), refs=[(r["label"], r["sha256"]) for r in refs])))
        return v, art, entry, refs, own

    def _segment_hashes(self, db, version_id):
        v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
        art = row(db, "SELECT * FROM artifact WHERE id=?", v["artifact_id"])
        plan = self._adopted_content(db, art["episode_id"], "plan") or {}
        order = [s["id"] for s in timeline.layout(plan)["segments"]] if plan else sorted(v["content"].get("segments", {}))
        own = {s: self._segment_material(db, version_id, s)[4] for s in order if s in v["content"].get("segments", {})}
        comp = {}
        for i, s in enumerate(order):
            if s not in own:
                continue
            neighbours = [own.get(order[j]) for j in (i - 1, i + 1) if 0 <= j < len(order)]
            comp[s] = sha256_bytes(canonical([own[s], neighbours]))
        return order, own, comp

    def _review_task(self, db, art):
        r = db.execute("SELECT id FROM task WHERE artifact_id=? AND goal='两遍独审' ORDER BY created_at DESC", (art["id"],)).fetchone()
        if r:
            return r[0]
        kickoff = self._kickoff(db, art["episode_id"])
        tid, thread = new_id(), new_id()
        db.execute("INSERT INTO task(id,project_id,episode_id,artifact_id,stage,goal,max_rounds,executor_plan,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (tid, art["project_id"], art["episode_id"], art["id"], "prompts", "两遍独审",
                                                 2, dumps(kickoff["executor_plan"]), self.clock()))
        db.execute("INSERT INTO thread VALUES(?,?,?)", (thread, tid, f"prompt-review:{art['id']}"))
        return tid

    def start_prompt_review(self, version_id, segment, pass_name, command_id, provider=None):
        if pass_name not in ("cold_read", "comparison"):
            raise Rejected("遍次只能是 cold_read 或 comparison")
        payload = dict(v=version_id, s=segment, p=pass_name, pr=provider)
        with transaction(self.db_path) as db:
            def run():
                v, art, entry, refs, own = self._segment_material(db, version_id, segment)
                tid = self._review_task(db, art)
                plan_exec = json.loads(row(db, "SELECT executor_plan FROM task WHERE id=?", tid)["executor_plan"])
                prov = provider or plan_exec["review"]["provider"]
                if refs and not getattr(self.executors[prov], "reads_images", True):
                    raise Rejected(f"{prov} 在当前隔离配置下不能读图，请选择能读图的审核执行器")
                images = [dict(role="image", name=f"{r['label']}.png", data=self.store.read_blob(r["sha256"])) for r in refs]
                txt = dict(role="segment_txt", name=f"{segment}.txt", text=entry.get("txt", ""))
                order_note = dict(role="reference_order", name="order.txt",
                                  text="\n".join(f"{r['label']}：{r.get('role') or '用途未写'}" for r in refs))
                if pass_name == "cold_read":
                    entries, scope_hash = [txt, order_note, *images], own
                else:
                    _, owns, comps = self._segment_hashes(db, version_id)
                    cold = db.execute("SELECT * FROM review WHERE version_id IN (SELECT id FROM version WHERE artifact_id=?) "
                                      "AND pass='cold_read' AND scope=? AND scope_hash=? ORDER BY created_at DESC",
                                      (art["id"], segment, own)).fetchone()
                    if not cold:
                        raise Rejected("须先完成本段冷读并保存观察")
                    kickoff = self._kickoff(db, art["episode_id"])
                    story = self._adopted_content(db, art["episode_id"], "story") or {}
                    plan = self._adopted_content(db, art["episode_id"], "plan") or {}
                    lay = timeline.layout(plan) if plan else dict(segments=[])
                    order = [s["id"] for s in lay["segments"]]
                    i = order.index(segment) if segment in order else -1
                    neighbours = [dict(role="neighbour_txt", name=f"{order[j]}.txt",
                                       text=v["content"]["segments"].get(order[j], {}).get("txt", ""))
                                  for j in (i - 1, i + 1) if 0 <= j < len(order) and i >= 0]
                    shots = [s for s in plan.get("shots", []) if s.get("segment") == segment]
                    entries = [dict(role="cold_read_observations", name="pass1.json", text=cold["observations"]),
                               txt, order_note, *images,
                               dict(role="adopted_story", name="story.md", text=render("story", story)),
                               dict(role="project_settings", name="settings.txt",
                                    text=prompts.render_settings(kickoff["settings_snapshot"])),
                               dict(role="plan_segment", name=f"{segment}-plan.json", text=json.dumps(
                                   dict(segment=next((s for s in lay["segments"] if s["id"] == segment), None), shots=shots),
                                   ensure_ascii=False)),
                               dict(role="intent", name="intent.txt", text=kickoff["goal"] or "（未写）"), *neighbours]
                    scope_hash = comps[segment]
                run_id = self._queue_run(db, tid, pass_name, None, "", target_version_id=version_id, provider=prov,
                                         entries=entries, extra_header=dict(scope=segment, scopeHash=scope_hash))
                return dict(runId=run_id, taskId=tid, scopeHash=scope_hash)
            return self._command(db, command_id, "prompt_review", payload, run)

    def _seg_review_state(self, db, artifact_id, segment, pass_name, scope_hash):
        r = db.execute("SELECT r.* FROM review r JOIN version v ON v.id=r.version_id WHERE v.artifact_id=? AND r.pass=? "
                       "AND r.scope=? AND r.scope_hash=? ORDER BY r.created_at DESC", (artifact_id, pass_name, segment,
                                                                                       scope_hash)).fetchone()
        if not r:
            return dict(state="needed")
        issues = [dict(i) for i in db.execute(
            "SELECT i.*, (SELECT reason FROM waiver w WHERE w.issue_id=i.id AND w.scope_hash=?) AS waiver_reason "
            "FROM issue i WHERE review_id=? ORDER BY ordinal", (scope_hash, r["id"]))]
        blocking = [i["id"] for i in issues if i["blocking"]]
        open_ = [i["id"] for i in issues if not i["blocking"] and not i["waiver_reason"]]
        state = ("passed" if r["original_decision"] == "pass" else "blocked" if blocking
                 else "pending_decision" if open_ else "waived")
        obs = json.loads(r["observations"])
        return dict(state=state, reviewId=r["id"], decision=r["original_decision"], issues=issues, reviewer=r["reviewer"],
                    versionId=r["version_id"], observation=obs[0]["evidence"] if obs else None)

    def prompt_review_status(self, version_id):
        with connect(self.db_path) as db:
            order, own, comp = self._segment_hashes(db, version_id)
            art_id = row(db, "SELECT artifact_id FROM version WHERE id=?", version_id)["artifact_id"]
            out = {}
            for seg in order:
                if seg not in own:
                    continue
                cold = self._seg_review_state(db, art_id, seg, "cold_read", own[seg])
                cmp_ = self._seg_review_state(db, art_id, seg, "comparison", comp[seg])
                ok = cold["state"] in ("passed", "waived") and cmp_["state"] in ("passed", "waived")
                out[seg] = dict(hash=own[seg], coldRead=cold, comparison=cmp_, ok=ok,
                                waived=cold["state"] == "waived" or cmp_["state"] == "waived")
        return out

    def waive_segment_issue(self, issue_id, reason, command_id):
        with transaction(self.db_path) as db:
            def run():
                issue = row(db, "SELECT * FROM issue WHERE id=?", issue_id)
                rv = row(db, "SELECT * FROM review WHERE id=?", issue["review_id"])
                if not rv["scope"]:
                    raise Rejected("不是分段审查意见")
                if issue["blocking"]:
                    raise Rejected("文件缺失、引用错误、超限或必守冲突必须修复，不能放行")
                if not reason.strip():
                    raise Rejected("放行须写理由")
                wid = new_id()
                db.execute("INSERT INTO waiver(id,issue_id,version_id,reason,created_at,scope_hash) VALUES(?,?,?,?,?,?)",
                           (wid, issue_id, rv["version_id"], reason.strip(), self.clock(), rv["scope_hash"]))
                return dict(waiverId=wid)
            return self._command(db, command_id, "waive_seg", dict(i=issue_id, r=reason), run)

    def _after_review(self, db, task, run, review_id, result):
        rv = row(db, "SELECT * FROM review WHERE id=?", review_id)
        if rv["pass"] == "comparison" and result["decision"] == "pass":
            seg = rv["scope"]
            ep = task["episode_id"]
            db.execute("UPDATE impact SET state='resolved' WHERE episode_id=? AND state='pending' AND "
                       "(target_ref=? OR (target_ref LIKE 'joint:%' AND (target_ref LIKE ? OR target_ref LIKE ?)))",
                       (ep, f"prompts:{seg}", f"joint:{seg}-%", f"joint:%-{seg}"))
        return []
