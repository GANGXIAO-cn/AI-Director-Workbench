"""Episode-scoped expert authorizations and resumable stage execution."""
import json
import logging

from .db import connect, dumps, new_id, transaction
from .errors import Conflict, Rejected, loads, row
from .storage import canonical, sha256_bytes

from .workflow import ORDER, LABELS as FILM_LABELS
STAGES = (*ORDER, "delivery", "publish")
LABELS = dict(FILM_LABELS, delivery="整集图文交付", publish="发布文案")
MANDATORY = ("paid_batch", "rounds_exhausted", "structural_block", "model_conflict", "unknown_request",
             "settings_change", "required_violation", "isolation_unverified", "waiting_quota")
OPTIONAL = ("confirm_story", "confirm_plan", "confirm_delivery", "confirm_point")


class Stop(Exception):
    def __init__(self, reason, detail, **extra):
        self.info = dict(reason=reason, detail=detail, **extra)


class ExpertMixin:
    def _expert_input(self, db, project_id, eid):
        ep = row(db, "SELECT project_id,display_code FROM episode WHERE id=?", eid)
        if ep["project_id"] != project_id:
            raise Rejected("授权的集必须属于当前项目")
        kick = self._kickoff(db, eid)
        project = row(db, "SELECT settings_revision,method_pack FROM project WHERE id=?", project_id)
        versions = {r["stage"]: dict(id=r["id"], number=r["display_no"]) for r in db.execute(
            "SELECT a.stage,v.id,v.display_no FROM artifact a JOIN version v ON v.id=a.adopted_version_id "
            "WHERE a.episode_id=?", (eid,))}
        return dict(code=ep["display_code"], versions=versions, settingsRevision=project["settings_revision"],
                    inputs=sha256_bytes(canonical([kick, project, self._source_text(db, kick),
                                                  self.publish_settings(project_id)])))

    def expert_preview(self, project_id, episodes):
        with connect(self.db_path) as db:
            return {eid: self._expert_input(db, project_id, eid) for eid in episodes}

    def authorize_expert(self, project_id, episodes, stop_stage, division, image_cap=0, stops=(), command_id=None,
                         budget_cents=0, expected=None):
        if not isinstance(episodes, list) or not episodes or len(set(episodes)) != len(episodes):
            raise Rejected("请选择不重复的具体集")
        if stop_stage not in STAGES or set(stops) - set(MANDATORY + OPTIONAL):
            raise Rejected("授权终点或确认点无效")
        budget_cents = 0
        for amount in (image_cap,):
            if type(amount) is not int or amount < 0:
                raise Rejected("图像张数须为非负整数")
        if not isinstance(division, dict):
            raise Rejected("执行分工无效")
        for role, sel in division.items():
            if role not in ("create", "review") or not isinstance(sel, dict) or set(sel) - {"provider", "model", "effort"} or sel.get("provider") not in self.executors:
                raise Rejected("执行分工无效")
            self.validate_selection(sel["provider"], sel.get("model"), sel.get("effort"))
        payload = dict(project=project_id, episodes=episodes, stop=stop_stage, division=division, cap=image_cap,
                       budget=budget_cents, stops=list(stops), expected=expected)
        with transaction(self.db_path) as db:
            def authorize():
                baseline = {eid: self._expert_input(db, project_id, eid) for eid in episodes}
                if expected is not None and expected != baseline:
                    raise Conflict("起始采用版或设定已更新，请刷新授权范围")
                aid = new_id()
                all_stops = sorted(set(stops) | set(MANDATORY))
                db.execute("INSERT INTO expert_auth VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (aid, project_id, dumps(episodes), stop_stage, dumps(division), image_cap,
                            dumps(all_stops), "authorized", None, "[]", self.clock()))
                db.execute("INSERT INTO expert_scope(auth_id,baseline,budget_cents) VALUES(?,?,?)",
                           (aid, dumps(baseline), budget_cents))
                db.execute("INSERT INTO expert_budget_currency VALUES(?,?)", (aid, "USD"))
                return dict(authId=aid, stops=all_stops, mandatory=list(MANDATORY))
            return self._command(db, command_id, "expert", payload, authorize)

    def expert_history(self, project_id, episode_id=None):
        with connect(self.db_path) as db:
            return [dict(r) for r in db.execute("SELECT id,state,stop_stage,created_at,episodes FROM expert_auth "
                                                "WHERE project_id=? ORDER BY created_at DESC,rowid DESC", (project_id,))
                    if episode_id is None or episode_id in json.loads(r["episodes"])]

    def expert_view(self, auth_id):
        with connect(self.db_path) as db:
            a = loads(row(db, "SELECT a.*,s.baseline,s.budget_cents,c.currency,s.cursor,s.pause_requested FROM expert_auth a "
                          "LEFT JOIN expert_scope s ON a.id=s.auth_id LEFT JOIN expert_budget_currency c ON c.auth_id=a.id WHERE a.id=?", auth_id),
                      "episodes", "division", "stops", "log", "stop_reason", "baseline", "cursor")
            a["reserved"] = {r["episode_id"]: r["amount"] for r in db.execute(
                "SELECT b.episode_id,sum(b.budget_cents) AS amount FROM expert_batch x "
                "JOIN image_batch b ON b.id=x.batch_id WHERE x.auth_id=? AND b.state!='cancelled' "
                "GROUP BY b.episode_id", (auth_id,))}
        return dict(a, **(a["stop_reason"] or {}))

    def claim_expert(self, auth_id, resume_token=None):
        with transaction(self.db_path) as db:
            a = loads(row(db, "SELECT * FROM expert_auth WHERE id=?", auth_id), "episodes", "stop_reason", "log")
            if a["state"] in ("running", "done"):
                return False
            if not db.execute("SELECT 1 FROM expert_scope WHERE auth_id=?", (auth_id,)).fetchone():
                raise Rejected("旧授权未保存起点，请按当前采用版重新授权")
            for other in db.execute("SELECT episodes FROM expert_auth WHERE project_id=? AND state='running'",
                                    (a["project_id"],)):
                if set(json.loads(other[0])).intersection(a["episodes"]):
                    raise Conflict("该集已在另一项专家授权中运行")
            if a["state"] in ("stopped", "manual"):
                reason = a["stop_reason"]
                if not resume_token or resume_token != reason.get("token"):
                    return False
                if reason["reason"] == "settings_change":
                    raise Rejected("输入已变更，请重新核对范围并授权")
                a["log"].append(dict(resumed=reason["reason"], checkpoint=reason.get("checkpoint"),
                                     episode=reason.get("episode"), stage=reason.get("stage")))
            for eid in a["episodes"]:
                owner = db.execute("SELECT auth_id FROM expert_episode_owner WHERE episode_id=?", (eid,)).fetchone()
                if owner and owner[0] != auth_id and a["state"] in ("stopped", "manual"):
                    raise Conflict("本集已开启新的专家授权，请使用最新记录")
                db.execute("INSERT INTO expert_episode_owner VALUES(?,?) ON CONFLICT(episode_id) "
                           "DO UPDATE SET auth_id=excluded.auth_id", (eid, auth_id))
            db.execute("UPDATE expert_auth SET state='running',stop_reason=NULL,log=? WHERE id=?",
                       (dumps(a["log"]), auth_id))
            db.execute("UPDATE expert_scope SET pause_requested=0 WHERE auth_id=?", (auth_id,))
            return True

    def pause_expert(self, auth_id):
        with transaction(self.db_path) as db:
            db.execute("UPDATE expert_scope SET pause_requested=1 WHERE auth_id=? AND EXISTS "
                       "(SELECT 1 FROM expert_auth WHERE id=? AND state='running')", (auth_id, auth_id))
        return self.expert_view(auth_id)

    def manual_expert(self, auth_id):
        with transaction(self.db_path) as db:
            a = row(db, "SELECT state,stop_reason FROM expert_auth WHERE id=?", auth_id)
            if a["state"] == "running":
                db.execute("UPDATE expert_scope SET pause_requested=2 WHERE auth_id=?", (auth_id,))
            elif a["state"] == "stopped":
                db.execute("UPDATE expert_auth SET state='manual' WHERE id=?", (auth_id,))
        return self.expert_view(auth_id)

    def expert_run(self, auth_id, resume_token=None):
        if self.claim_expert(auth_id, resume_token):
            return self.advance_expert(auth_id)
        return self.expert_view(auth_id)

    def _reserve_expert_budget(self, db, batch, budget):
        # Track approved image counts within the expert scope.
        auth = db.execute("SELECT a.id,a.image_cap,s.budget_cents,c.currency FROM expert_episode_owner o "
                          "JOIN expert_auth a ON a.id=o.auth_id JOIN expert_scope s ON s.auth_id=a.id "
                          "LEFT JOIN expert_budget_currency c ON c.auth_id=a.id WHERE o.episode_id=? AND a.state IN ('running','stopped')", (batch["episode_id"],)).fetchone()
        if not auth:
            return
        count = db.execute("SELECT coalesce(sum(b.count),0) FROM image_batch b JOIN expert_batch x ON x.batch_id=b.id "
                           "WHERE x.auth_id=? AND b.episode_id=? AND b.state!='cancelled'", (auth["id"],batch["episode_id"])).fetchone()[0]
        if auth["image_cap"] and count + batch["count"] > auth["image_cap"]:
            raise Rejected("本批张数超出专家授权")
        db.execute("INSERT INTO expert_batch VALUES(?,?)", (batch["id"],auth["id"]))

    def _expert_guard(self, db, auth, eid):
        scope = row(db, "SELECT pause_requested FROM expert_scope WHERE auth_id=?", auth["id"])
        if scope["pause_requested"]:
            raise Stop("paused", "已在步骤之间暂停，可以切回手工")
        current = self._expert_input(db, auth["project_id"], eid)
        base = auth["baseline"][eid]
        if base["inputs"] != current["inputs"]:
            raise Stop("settings_change", "开工输入、项目设定或发布设置已变更，请重新核对授权")
        for stage in set(current["versions"]) | set(base["versions"]):
            item = current["versions"].get(stage)
            if item != base["versions"].get(stage):
                own = item and db.execute("SELECT 1 FROM adoption WHERE command_id=? AND version_id=?",
                                          (f"{auth['id']}:adopt:{item['id']}", item["id"])).fetchone()
                confirmed = item and any(x.get("checkpoint") == item["id"] and x.get("episode") == eid
                                         and x.get("stage") == stage for x in auth["log"])
                if not own and not confirmed:
                    raise Stop("settings_change", f"{LABELS[stage]}采用版已变更，请重新核对授权", page=stage)
        pending = db.execute("SELECT id,state FROM image_batch WHERE episode_id=? AND state IN "
                             "('awaiting_authorization','authorized','partial','running','pending_reconcile') "
                             "ORDER BY (state='pending_reconcile') DESC,created_at LIMIT 1", (eid,)).fetchone()
        if pending:
            unknown = pending["state"] == "pending_reconcile"
            raise Stop("unknown_request" if unknown else "paid_batch",
                       "请先核对图像请求的实际结果" if unknown else "请到素材页核对并处理本批，或取消批次",
                       page="assets", batchId=pending["id"])

    def advance_expert(self, auth_id):
        auth = self.expert_view(auth_id)
        cursor = {}
        try:
            for eid in auth["episodes"]:
                for stage in STAGES[:STAGES.index(auth["stop_stage"]) + 1]:
                    cursor = dict(episode=eid, stage=stage)
                    with transaction(self.db_path) as db:
                        db.execute("UPDATE expert_scope SET cursor=? WHERE auth_id=?", (dumps(cursor), auth_id))
                        self._expert_guard(db, auth, eid)
                    if any(x.get("completed") and x.get("episode") == eid and x.get("stage") == stage for x in auth["log"]):
                        continue
                    result = self._expert_stage(auth, eid, stage)
                    self._expert_event(auth, dict(**cursor, completed=True, **result))
            with transaction(self.db_path) as db:
                for eid in auth["episodes"]:
                    cursor = dict(episode=eid, stage=auth["stop_stage"])
                    self._expert_guard(db, auth, eid)
                db.execute("UPDATE expert_auth SET state='done',stop_reason=NULL WHERE id=?", (auth_id,))
            return self.expert_view(auth_id)
        except Stop as exc:
            state, reason = "stopped", dict(cursor, **exc.info)
        except (Rejected, Conflict) as exc:
            state = "stopped"
            reason = dict(cursor, reason="isolation_unverified" if "隔离" in str(exc) else "structural_block",
                          detail=str(exc))
        except Exception as exc:
            logging.exception("expert run %s stopped", auth_id)
            state, reason = "stopped", dict(cursor, reason="interrupted", detail=f"执行中断，需核对任务记录：{type(exc).__name__}")
        with transaction(self.db_path) as db:
            handoff = row(db, "SELECT pause_requested FROM expert_scope WHERE auth_id=?", auth_id)
            if reason and handoff["pause_requested"] == 2:
                state = "manual"
            if reason:
                reason["token"] = new_id()
            db.execute("UPDATE expert_auth SET state=?,stop_reason=? WHERE id=?",
                       (state, dumps(reason) if reason else None, auth_id))
        return self.expert_view(auth_id)

    def _expert_event(self, auth, event):
        auth["log"].append(event)
        with transaction(self.db_path) as db:
            db.execute("UPDATE expert_auth SET log=? WHERE id=?", (dumps(auth["log"]), auth["id"]))

    def _expert_checkpoint(self, auth, eid, stage, key, detail):
        required = "confirm_" + stage in auth["stops"] or (stage == "story" and "confirm_point" in auth["stops"])
        if required and not any(x.get("checkpoint") == key for x in auth["log"]):
            raise Stop("confirm_point", detail, checkpoint=key, versionId=key if stage != "delivery" else None)

    def _expert_execute(self, auth, eid, stage, rid):
        self.execute_run(rid)
        with connect(self.db_path) as db:
            run = row(db, "SELECT status,reason FROM run WHERE id=?", rid)
        self._expert_event(auth, dict(run=rid, episode=eid, stage=stage, status=run["status"]))
        if run["status"] != "succeeded":
            raise Stop("waiting_quota" if run["status"] == "waiting_quota" else "structural_block",
                       run["reason"] or "请在原任务核对运行状态", runId=rid)
        with connect(self.db_path) as db:
            self._expert_guard(db, auth, eid)

    def _expert_stage(self, auth, eid, stage):
        with connect(self.db_path) as db:
            skip = db.execute("SELECT reason FROM optional_stage_choice WHERE episode_id=? AND stage=?", (eid,stage)).fetchone()
            locked = self._kickoff(db,eid).get("sourceIntent") == "locked_screenplay"
        if skip and skip[0]:
            return dict(skipped=True, reason=skip[0])
        if locked and stage in ("story","characters","world","treatment"):
            return dict(source="locked_screenplay", separatelyDeveloped=False)
        if stage == "delivery":
            return self._expert_deliver(auth, eid)
        if stage == "prompts":
            blocked = self.blocked_segments(eid)
            if blocked:
                raise Stop("structural_block", "；".join(msg for msgs in blocked.values() for msg in msgs), page="space")
            for seg in self.cast_matrix(eid)["rows"]:
                for role, cell in seg["cells"].items():
                    if cell["status"] == "unclear":
                        raise Stop("structural_block", f"{seg['segment']} 的 {role} 出镜待核对", page="cast")
        if stage == "publish" and not self.publish_settings(auth["project_id"]):
            raise Stop("structural_block", "请先填写本项目的发布设置", page="preset")
        while True:
            art = next((a for a in self.episode_view(eid)["artifacts"] if a["stage"] == stage), None)
            adopted = art and art["adopted_version_id"]
            versions = art["versions"] if art else []
            task = art["tasks"][-1] if art and art["tasks"] else None
            if adopted:
                version = next(v for v in versions if v["id"] == adopted)
            elif not versions:
                if task:
                    raise Stop("waiting_quota" if task["state"] == "waiting_quota" else "structural_block",
                               "已有任务尚无草稿，请在原位置处理后恢复", taskId=task["id"])
                job = self.create_task(eid, stage, "依据本集开工材料与上游采用版完成" + LABELS[stage],
                                       executor=auth["division"], command_id=f"{auth['id']}:{eid}:{stage}:draft")
                self._expert_execute(auth, eid, stage, job["runId"])
                continue
            else:
                version = versions[-1]
                if task and task["state"] in ("queued", "running", "clarifying", "waiting_quota", "failed"):
                    raise Stop("waiting_quota" if task["state"] == "waiting_quota" else "structural_block",
                               "当前任务有待处理的运行或澄清，请在原位置处理", taskId=task["id"])
            vid, gate = version["id"], version["gate"]
            errors = list(gate["formatErrors"])
            if stage == "plan" and not errors:
                errors += self.check_plan_refs(eid, version["content"])
            if errors:
                raise Stop("structural_block", "；".join(errors), versionId=vid)
            if stage == "story" and adopted:
                if not gate["adoptable"]:
                    raise Stop("required_violation" if gate["blocking"] else "review_decision",
                               "当前故事采用版有待处理的独审意见，请到故事稿核对", versionId=vid)
                # An adopted story can predate an episode's explicit switch to newer settings.
                latest = version["reviews"][-1] if version["reviews"] else None
                if latest:
                    manifest, _ = self.store.load_manifest(latest["manifest_relpath"])
                    with connect(self.db_path) as db:
                        revision = self._kickoff(db, eid)["settings_revision"]
                    if manifest["settingsRevision"] != revision:
                        job = self.submit_review(vid, f"{auth['id']}:{vid}:settings:{revision}",
                                                 **auth["division"].get("review", {}))
                        self._expert_execute(auth, eid, stage, job["runId"])
                        continue
            if stage in ("story", "script", "plan") and not adopted:
                if self._expert_text_review(auth, eid, stage, version, task):
                    continue
            if stage == "publish":
                self.compose_publish(eid, vid)
            if not adopted:
                self._expert_checkpoint(auth, eid, stage, vid, f"确认采用{LABELS[stage]} R{version['display_no']:02d}")
                self._adopt(art["id"], vid, None, f"{auth['id']}:adopt:{vid}", False, expert=auth)
            return dict(versionId=vid, number=version["display_no"], reused=bool(adopted))

    def _expert_text_review(self, auth, eid, stage, version, task):
        vid = version["id"]
        reviews = version["reviews"]
        if not reviews:
            selection = auth["division"].get("review", {})
            job = self.submit_review(vid, f"{auth['id']}:{vid}:review", **selection)
            self._expert_execute(auth, eid, stage, job["runId"])
            return True
        issues = reviews[-1]["issues"]
        hard = [i for i in issues if i["blocking"]]
        if stage=='plan' and not hard:
            for issue in issues:
                if not issue['waiver_reason']:
                    self.waive(issue['id'],vid,'08分镜：不影响结果的普通意见按本版放行',f"{auth['id']}:plan-waive:{issue['id']}")
            return False
        if hard and stage!='plan':
            raise Stop("required_violation" if hard[0]["setting_id"] else "structural_block", hard[0]["reason"], versionId=vid)
        if len({r["reviewer"] for r in reviews}) > 1 and len({r["original_decision"] for r in reviews}) > 1:
            raise Stop("model_conflict", "同一版本的审核者结论不一致，请比较依据并决定采用版", versionId=vid)
        unresolved = [i["reason"] for i in issues if not i["waiver_reason"] and (stage!='plan' or i['blocking'])]
        if not unresolved:
            return False
        if not task or task["used_rounds"] >= task["max_rounds"]:
            raise Stop("rounds_exhausted", "自动修订轮数已用尽，请处理原任务", usedRounds=task["used_rounds"] if task else 0)
        with transaction(self.db_path) as db:
            self._expert_guard(db, auth, eid)
            instruction=('仅局部修正以下关键问题，保留其他段和已接受素材用途，不重写上游：' if stage=='plan' else '按独审修订：')+'；'.join(unresolved)
            rid = self._queue_run(db, task["id"], "revise", vid, instruction, counted=True,
                                  **auth["division"].get("create", {}))
            db.execute("UPDATE task SET used_rounds=used_rounds+1 WHERE id=?", (task["id"],))
        self._expert_execute(auth, eid, stage, rid)
        return True

    def _expert_deliver(self, auth, eid):
        problems, ctx = self.delivery_problems(eid, "full")
        if problems:
            p = problems[0]
            raise Stop("structural_block", p["problem"], page=p["stage"] if p["stage"] in STAGES + ("assets","space","review") else "delivery")
        key = f"{eid}:delivery:" + sha256_bytes(canonical(ctx["adopted"]))
        self._expert_checkpoint(auth, eid, "delivery", key, "确认按当前采用版生成整集图文并核验")
        with connect(self.db_path) as db:
            self._expert_guard(db, auth, eid)
        result = self._deliver(eid, "full", f"{auth['id']}:{key}", expert=auth)
        check = self.verify_delivery(result["deliveryId"])
        if not check["ok"]:
            raise Stop("structural_block", dumps(check["errors"]), deliveryId=result["deliveryId"])
        return dict(deliveryId=result["deliveryId"], verified=True)
