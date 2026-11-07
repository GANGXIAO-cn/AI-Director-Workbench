"""Workbench commands. Models only submit results; adoption, waivers and settings stay with the user."""
import hashlib
import json
from pathlib import Path
import time

from . import contracts, prompts
from .db import dumps, initialize, new_id, transaction, connect
from .executors.base import ExecRequest
from .methodpack import load_config, load_pack
from .storage import Store, canonical, sha256_bytes
from .stages import NEXT, WRITABLE, check_content, normalize, render as render_stage
from .storycheck import render_story

ROUND_LIMIT = 2
REVIEW_PASSES = {"review": "story", "cold_read": "cold_read", "comparison": "comparison"}


from .errors import Conflict, NotFound, Rejected, row as _row, loads as _loads


class Core:
    def __init__(self, data_dir: Path, executors: dict, clock=time.time, image_providers=None):
        self.store = Store(data_dir)
        self.db_path = self.store.db_path
        self.executors = executors
        self.image_providers_map = image_providers or {}
        self.clock = clock
        initialize(self.db_path)
        self.mark_interrupted_requests()

    # ------------------------------------------------------------------ idempotency
    def _command(self, db, command_id, kind, payload, fn):
        if not command_id:
            return fn()
        digest = sha256_bytes(canonical(payload))
        row = db.execute("SELECT kind,payload_hash,result FROM command WHERE command_id=?", (command_id,)).fetchone()
        if row:
            if row["kind"] != kind or row["payload_hash"] != digest:
                raise Conflict("同一命令号已用于不同内容")
            return json.loads(row["result"])
        result = fn()
        db.execute("INSERT INTO command VALUES(?,?,?,?,?)", (command_id, kind, digest, dumps(result), self.clock()))
        return result

    # ------------------------------------------------------------------ settings
    def get_setting(self, key, default=None):
        with connect(self.db_path) as db:
            row = db.execute("SELECT value FROM app_setting WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put_setting(self, key, value):
        with transaction(self.db_path) as db:
            db.execute("INSERT INTO app_setting VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, dumps(value)))

    # ------------------------------------------------------------------ projects
    def create_project(self, config_id, name=None, summary=None, command_id=None):
        config = load_config(config_id)
        load_pack(config["methodPack"])
        payload = dict(config_id=config_id, name=name, summary=summary)
        with transaction(self.db_path) as db:
            def run():
                pid = new_id()
                revision = 1 if config["settings"] else 0
                db.execute("INSERT INTO project VALUES(?,?,?,?,?,?,?,?,1,?)",
                           (pid, name or config["name"] or "未命名项目", summary or config["summary"], config_id,
                            config["methodPack"], revision, dumps(config["specPreset"]),
                            dumps(config.get("production", {})), self.clock()))
                for s in config["settings"]:
                    db.execute("INSERT INTO setting_entry(id,project_id,setting_revision,key,category,strength,content,"
                               "source_ref,user_reason) VALUES(?,?,?,?,?,?,?,?,?)",
                               (new_id(), pid, revision, s["key"], s["category"], s["strength"], s["content"],
                                s.get("sourceRef", ""), s.get("userReason", "")))
                return dict(projectId=pid)
            return self._command(db, command_id, "create_project", payload, run)

    def _settings(self, db, project_id, revision=None):
        if revision is None:
            revision = db.execute("SELECT settings_revision FROM project WHERE id=?", (project_id,)).fetchone()[0]
        rows = db.execute("SELECT key,category,strength,content,source_ref,user_reason FROM setting_entry WHERE project_id=? "
                          "AND setting_revision<=? AND (retired_revision IS NULL OR retired_revision>?) "
                          "ORDER BY setting_revision, rowid", (project_id, revision, revision)).fetchall()
        return revision, [dict(r) for r in rows]

    def list_projects(self):
        with connect(self.db_path) as db:
            return [dict(r) for r in db.execute(
                "SELECT p.id,p.name,p.summary,p.config_id,(SELECT count(*) FROM episode e WHERE e.project_id=p.id)"
                " AS episodes FROM project p ORDER BY created_at DESC")]

    def project_view(self, project_id):
        with connect(self.db_path) as db:
            project = _loads(_row(db, "SELECT * FROM project WHERE id=?", project_id), "spec_preset", "production")
            project["settings"] = self._settings(db, project_id)[1]
            project["episodes"] = [_loads(dict(r), "leftover_facts") for r in db.execute(
                "SELECT e.*, k.id AS kickoff_id FROM episode e LEFT JOIN kickoff k ON k.episode_id=e.id "
                "WHERE e.project_id=? ORDER BY e.display_code", (project_id,))]
            project["imports"] = [dict(r) for r in db.execute(
                "SELECT id,episode_id,original_name,purpose,sha256,bytes,parse_status,diagnostic,copied_at "
                "FROM import_record WHERE project_id=? ORDER BY copied_at", (project_id,))]
        return project

    # ------------------------------------------------------------------ episodes
    def create_episode(self, project_id, display_code, title="", leftover_facts=(), command_id=None):
        payload = dict(project_id=project_id, display_code=display_code, title=title, facts=list(leftover_facts))
        with transaction(self.db_path) as db:
            _row(db, "SELECT id FROM project WHERE id=?", project_id, missing="项目不存在")
            def run():
                if db.execute("SELECT 1 FROM episode WHERE project_id=? AND display_code=?",
                              (project_id, display_code)).fetchone():
                    raise Conflict(f"项目内已有集号 {display_code}")
                eid = new_id()
                db.execute("INSERT INTO episode(id,project_id,display_code,title,leftover_facts,created_at) "
                           "VALUES(?,?,?,?,?,?)", (eid, project_id, display_code, title,
                                                   dumps(list(leftover_facts)), self.clock()))
                return dict(episodeId=eid, displayCode=display_code)
            return self._command(db, command_id, "create_episode", payload, run)

    def record_leftovers(self, episode_id, facts, expected_revision):
        with transaction(self.db_path) as db:
            ep = _row(db, "SELECT revision FROM episode WHERE id=?", episode_id)
            if ep["revision"] != expected_revision:
                raise Conflict("集记录已被更新", current=ep["revision"])
            db.execute("UPDATE episode SET leftover_facts=?, revision=revision+1 WHERE id=?", (dumps(facts), episode_id))
        return dict(revision=expected_revision + 1)

    # ------------------------------------------------------------------ imports
    def import_file(self, project_id, name, data: bytes, purpose, episode_id=None, provided_source="",
                    command_id=None):
        payload = dict(project_id=project_id, name=name, sha=sha256_bytes(data), purpose=purpose, episode_id=episode_id)
        with transaction(self.db_path) as db:
            _row(db, "SELECT id FROM project WHERE id=?", project_id, missing="项目不存在")
            if episode_id:
                self._episode_in(db, episode_id, project_id)
            def run():
                iid = new_id()
                rel = self.store.import_copy(project_id, iid, name, data)
                status, text, diag = "parsed", None, None
                if purpose in ("story_source", "script_source"):
                    try:
                        text = data.decode("utf-8").strip()
                        if not text:
                            status, diag = "failed", "文件为空"
                    except UnicodeDecodeError as exc:
                        status, diag = "failed", f"不是 UTF-8 文本：{exc.reason}"
                db.execute("INSERT INTO import_record VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (iid, project_id, episode_id, Path(name).name, provided_source, purpose,
                            sha256_bytes(data), len(data), rel, status, text, diag, self.clock()))
                return dict(importId=iid, parseStatus=status, diagnostic=diag)
            return self._command(db, command_id, "import", payload, run)

    def read_import(self, import_id):
        with connect(self.db_path) as db:
            rec = _row(db, "SELECT * FROM import_record WHERE id=?", import_id)
        data = (self.store.root / rec["relpath"]).read_bytes()
        rec["intact"] = sha256_bytes(data) == rec["sha256"]
        return rec

    def _episode_in(self, db, episode_id, project_id=None):
        ep = _row(db, "SELECT * FROM episode WHERE id=?", episode_id, missing="集不存在")
        if project_id and ep["project_id"] != project_id:
            raise Rejected("对象不属于该项目")
        return ep

    # ------------------------------------------------------------------ kickoff
    def kickoff_preview(self, episode_id, relation="independent", predecessor_ids=()):
        with connect(self.db_path) as db:
            ep = self._episode_in(db, episode_id)
            project = _loads(_row(db, "SELECT * FROM project WHERE id=?", ep["project_id"]), "spec_preset")
            revision, settings = self._settings(db, ep["project_id"])
            others = [dict(r) for r in db.execute(
                "SELECT id,display_code,leftover_facts FROM episode WHERE project_id=? AND id<>? ORDER BY display_code",
                (ep["project_id"], episode_id))]
            carried = self._carried(db, ep, relation, predecessor_ids)
            imports = [dict(r) for r in db.execute(
                "SELECT id,original_name,purpose,parse_status FROM import_record WHERE project_id=? AND "
                "(episode_id=? OR episode_id IS NULL) AND purpose IN ('story_source','script_source')",
                (ep["project_id"], episode_id))]
        return dict(episode=_loads(ep, "leftover_facts"), settingsRevision=revision, settings=settings,
                    spec=project["spec_preset"], predecessorsAvailable=[_loads(o, "leftover_facts") for o in others],
                    canContinue=bool(others), carriedFacts=carried, imports=imports)

    def _carried(self, db, ep, relation, predecessor_ids):
        if relation == "independent":
            return []
        if relation != "continue":
            raise Rejected("前集关系只能是 independent 或 continue")
        if not predecessor_ids:
            raise Rejected("承接需指定前集")
        facts = []
        for pid in predecessor_ids:
            if pid == ep["id"]:
                raise Rejected("不能承接本集")
            pre = self._episode_in(db, pid, ep["project_id"])
            facts += [f for f in json.loads(pre["leftover_facts"]) if f not in facts]
        return facts

    def start_episode(self, episode_id, source_type, executor_plan, source_import_id=None, idea_text=None,
                      topic_id=None, goal="", relation="independent", predecessor_ids=(), spec_overrides=None,
                      command_id=None):
        payload = dict(episode_id=episode_id, source_type=source_type, plan=executor_plan, imp=source_import_id,
                       idea=idea_text, topic=topic_id, goal=goal, relation=relation, pre=list(predecessor_ids),
                       spec=spec_overrides)
        for role in ("create", "review"):
            if executor_plan.get(role, {}).get("provider") not in self.executors:
                raise Rejected(f"未配置的执行器：{executor_plan.get(role)}")
        with transaction(self.db_path) as db:
            def run():
                ep = self._episode_in(db, episode_id)
                if db.execute("SELECT 1 FROM kickoff WHERE episode_id=?", (episode_id,)).fetchone():
                    raise Conflict("本集已开工")
                sources = dict(import_=source_import_id, idea=idea_text, topic=topic_id)
                given = {"import": source_import_id, "idea": idea_text, "topic": topic_id}
                if source_type not in given or not given[source_type] or sum(bool(v) for v in sources.values()) != 1:
                    raise Rejected("来源类型三选一，且必须提供对应来源")
                if source_type == "import":
                    imp = _row(db, "SELECT * FROM import_record WHERE id=?", source_import_id, missing="导入记录不存在")
                    if imp["project_id"] != ep["project_id"] or imp["episode_id"] not in (None, episode_id):
                        raise Rejected("导入材料不属于本项目本集")
                    if imp["purpose"] not in ("story_source", "script_source") or imp["parse_status"] != "parsed":
                        raise Rejected("该导入材料不能作为故事来源")
                    db.execute("UPDATE import_record SET episode_id=? WHERE id=?", (episode_id, source_import_id))
                carried = self._carried(db, ep, relation, predecessor_ids)
                if source_type == "topic":
                    self._topic_on_kickoff(db, episode_id, topic_id)
                project = _row(db, "SELECT spec_preset FROM project WHERE id=?", ep["project_id"])
                spec = dict(json.loads(project["spec_preset"]), **(spec_overrides or {}))
                revision, settings = self._settings(db, ep["project_id"])
                kid = new_id()
                db.execute("INSERT INTO kickoff VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (kid, episode_id, source_type, source_import_id, topic_id, idea_text, goal, relation,
                            dumps(list(predecessor_ids)), dumps(carried), revision, dumps(settings), dumps(spec),
                            dumps(executor_plan), self.clock()))
                aid = new_id()
                db.execute("INSERT INTO artifact(id,project_id,episode_id,stage) VALUES(?,?,?,?)",
                           (aid, ep["project_id"], episode_id, "story"))
                db.execute("UPDATE episode SET state='in_production', revision=revision+1 WHERE id=?", (episode_id,))
                return dict(kickoffId=kid, storyArtifactId=aid, carriedFacts=carried)
            return self._command(db, command_id, "start_episode", payload, run)

    def _kickoff(self, db, episode_id):
        k = _row(db, "SELECT * FROM kickoff WHERE episode_id=?", episode_id, missing="本集尚未开工")
        return _loads(k, "predecessor_ids", "carried_facts", "settings_snapshot", "spec", "executor_plan")

    def _source_text(self, db, kickoff):
        if kickoff["source_type"] == "import":
            return db.execute("SELECT parsed_text FROM import_record WHERE id=?",
                              (kickoff["source_import_id"],)).fetchone()[0]
        if kickoff["source_type"] == "topic":
            t = db.execute("SELECT * FROM topic WHERE id=?", (kickoff["topic_id"],)).fetchone()
            return f"选题卡 {t['code']}：{t['premise']}\n机制：{t['mechanism']}\n角色动机：{t['motivation']}"
        return kickoff["idea_text"]

    # ------------------------------------------------------------------ tasks, threads, runs
    def create_task(self, episode_id, stage, goal, auto_revise=False, executor=None, command_id=None, selection=None):
        payload = dict(episode_id=episode_id, stage=stage, goal=goal, auto=auto_revise, ex=executor, sel=selection)
        with transaction(self.db_path) as db:
            def run():
                ep = self._episode_in(db, episode_id)
                kickoff = self._kickoff(db, episode_id)
                plan = dict(kickoff["executor_plan"])
                if executor:
                    plan.update(executor)
                if stage not in WRITABLE:
                    raise Rejected("未知阶段")
                prev = next((k for k, v in NEXT.items() if v == stage), None)
                if prev:
                    up = db.execute("SELECT adopted_version_id FROM artifact WHERE episode_id=? AND stage=?",
                                    (episode_id, prev)).fetchone()
                    if not up or not up[0]:
                        raise Rejected("故事未通过独审并采用前，不能写剧本" if stage == "script"
                                       else f"上一步（{prev}）尚未采用")
                    self._ensure_artifact(db, ep, stage)
                aid = db.execute("SELECT id FROM artifact WHERE episode_id=? AND stage=?", (episode_id, stage)).fetchone()[0]
                tid = new_id()
                db.execute("INSERT INTO task(id,project_id,episode_id,artifact_id,stage,goal,auto_revise,max_rounds,"
                           "executor_plan,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (tid, ep["project_id"], episode_id, aid, stage, goal, int(auto_revise), ROUND_LIMIT,
                            dumps(plan), self.clock()))
                thread = new_id()
                db.execute("INSERT INTO thread VALUES(?,?,?)", (thread, tid, f"{stage}:{aid}"))
                self._message(db, thread, "user", goal, selection, None)
                base = db.execute("SELECT adopted_version_id FROM artifact WHERE id=?", (aid,)).fetchone()[0] \
                    or self._latest_version(db, aid)
                run_id = self._queue_run(db, tid, "revise" if base else "create", base, goal, selection)
                return dict(taskId=tid, threadId=thread, runId=run_id, artifactId=aid)
            return self._command(db, command_id, "create_task", payload, run)

    def _message(self, db, thread_id, role, text, selection, command_id, run_id=None, kind="text", payload=None):
        seq = db.execute("SELECT coalesce(max(seq),0)+1 FROM message WHERE thread_id=?", (thread_id,)).fetchone()[0]
        mid = new_id()
        db.execute("INSERT INTO message VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (mid, thread_id, seq, role, text, dumps(selection) if selection else None, command_id, run_id,
                    self.clock(), kind, dumps(payload) if payload is not None else None))
        return mid

    def _latest_version(self, db, artifact_id):
        row = db.execute("SELECT id FROM version WHERE artifact_id=? ORDER BY display_no DESC LIMIT 1",
                         (artifact_id,)).fetchone()
        return row[0] if row else None

    def post_message(self, task_id, text, command_id, selection=None, base_version_id=None):
        """A user message asks for a revision. It never resets the task's round counter."""
        if not text.strip():
            raise Rejected("消息为空")
        payload = dict(task_id=task_id, text=text, selection=selection, base=base_version_id)
        with transaction(self.db_path) as db:
            def run():
                task = _row(db, "SELECT * FROM task WHERE id=?", task_id, missing="任务不存在")
                if task["state"] == "closed":
                    raise Rejected("任务已结束")
                thread = db.execute("SELECT id FROM thread WHERE task_id=?", (task_id,)).fetchone()[0]
                base = base_version_id or self._latest_version(db, task["artifact_id"])
                if base:
                    self._version_of(db, base, task["artifact_id"])
                run_id = self._queue_run(db, task_id, "revise" if base else "create", base, text, selection)
                mid = self._message(db, thread, "user", text, selection, None, run_id)
                return dict(messageId=mid, runId=run_id, usedRounds=task["used_rounds"])
            return self._command(db, command_id, "post_message", payload, run)

    def _version_of(self, db, version_id, artifact_id):
        v = _row(db, "SELECT * FROM version WHERE id=?", version_id, missing="版本不存在")
        if v["artifact_id"] != artifact_id:
            raise Rejected("版本不属于该成果")
        return v

    def _queue_run(self, db, task_id, purpose, base_version_id, request, selection=None, counted=False,
                   target_version_id=None, provider=None, model=None, effort=None, entries=None, extra_header=None):
        task = _row(db, "SELECT * FROM task WHERE id=?", task_id)
        plan = json.loads(task["executor_plan"])
        role = plan["review"] if purpose in REVIEW_PASSES else plan["create"]
        provider = provider or role["provider"]
        if provider not in self.executors:
            raise Rejected(f"未配置的执行器：{provider}")
        model = model if model is not None else role.get("model")
        effort = effort if effort is not None else role.get("effort")
        kickoff = self._kickoff(db, task["episode_id"])
        project = _row(db, "SELECT method_pack FROM project WHERE id=?", task["project_id"])
        pack = load_pack(project["method_pack"])
        settings_text = prompts.render_settings(kickoff["settings_snapshot"])
        kickoff_text = prompts.render_kickoff(kickoff, self._source_text(db, kickoff))
        if entries is not None:
            pass
        elif purpose == "review":
            version = self._version_of(db, target_version_id, task["artifact_id"])
            entries = prompts.review_inputs(pack, settings_text, kickoff_text,
                                            render_stage(task["stage"], json.loads(version["content"])))
            entries += self._extra_review_inputs(db, task, version)
        else:
            base_content = json.loads(self._version_of(db, base_version_id, task["artifact_id"])["content"]) \
                if base_version_id else None
            adopted_story = self._upstream_text(db, task)
            req_text = request if not selection else f"{request}\n（选区：{json.dumps(selection, ensure_ascii=False)}）"
            entries = prompts.creation_inputs(pack, settings_text, kickoff_text, task["stage"], base_content,
                                              req_text, adopted_story)
            entries += self._extra_creation_inputs(db, task)
        run_id = new_id()
        header = dict(schemaVersion=1, purpose=purpose, projectId=task["project_id"], episodeId=task["episode_id"],
                      taskId=task_id, artifactId=task["artifact_id"], stage=task["stage"], provider=provider,
                      requestedModel=model, requestedEffort=effort, methodPack=dict(id=pack["id"], files=pack["files"]),
                      settingsRevision=kickoff["settings_revision"], baseVersionId=base_version_id,
                      reviewedVersionId=target_version_id,
                      sessionMode="ephemeral" if purpose in REVIEW_PASSES else "new", allowClarify=bool(selection),
                      isolationLevel="session" if purpose in REVIEW_PASSES else None, **(extra_header or {}))
        fingerprint, rel = self.store.freeze(run_id, header, entries)
        if header["sessionMode"] == "ephemeral":
            iso = self._isolation_status(provider)
            if iso != "session":
                raise Rejected(f"{provider} 的会话隔离未验证，必需审核等待", detail=dict(isolation=iso))
        db.execute("INSERT INTO run(id,task_id,purpose,counted_round,provider,requested_model,requested_effort,"
                   "session_mode,isolation_level,base_version_id,target_version_id,input_hash,manifest_relpath,"
                   "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, task_id, purpose, int(counted), provider, model, effort, header["sessionMode"],
                    header["isolationLevel"], base_version_id, target_version_id, fingerprint, rel, self.clock()))
        db.execute("UPDATE task SET state='queued' WHERE id=?", (task_id,))
        return run_id

    def retry_run(self, run_id, provider=None, model=None, effort=None, command_id=None):
        """Explicit retry (e.g. after waiting for quota or switching executor). Never automatic."""
        payload = dict(run_id=run_id, provider=provider, model=model, effort=effort)
        with transaction(self.db_path) as db:
            def run():
                old = _row(db, "SELECT * FROM run WHERE id=?", run_id, missing="运行不存在")
                if old["status"] not in ("failed", "waiting_quota", "stopped"):
                    raise Rejected("只有失败、等待额度或已停的运行可以重试")
                manifest, _ = self.store.load_manifest(old["manifest_relpath"])
                request = next((e for e in self.store.manifest_texts(manifest) if e["role"] == "request"), None)
                new_run = self._queue_run(db, old["task_id"], old["purpose"], old["base_version_id"],
                                          request["text"] if request else "", None, bool(old["counted_round"]),
                                          old["target_version_id"], provider or old["provider"],
                                          model if model is not None else old["requested_model"],
                                          effort if effort is not None else old["requested_effort"])
                db.execute("UPDATE run SET reason=coalesce(reason,'')||' → 已重试为 '||? WHERE id=?", (new_run, run_id))
                return dict(runId=new_run)
            return self._command(db, command_id, "retry_run", payload, run)

    # ------------------------------------------------------------------ execution
    def pending_runs(self):
        with connect(self.db_path) as db:
            return [r[0] for r in db.execute("SELECT id FROM run WHERE status='queued' ORDER BY created_at")]

    def execute_run(self, run_id) -> list[str]:
        """Run one queued run to completion; returns follow-up run ids queued by expert mode."""
        with transaction(self.db_path) as db:
            run = _row(db, "SELECT * FROM run WHERE id=?", run_id)
            if run["status"] != "queued":
                return []
            task = _row(db, "SELECT * FROM task WHERE id=?", run["task_id"])
            db.execute("UPDATE run SET status='running', started_at=? WHERE id=?", (self.clock(), run_id))
            db.execute("UPDATE task SET state='running' WHERE id=?", (task["id"],))
        manifest, digest = self.store.load_manifest(run["manifest_relpath"])
        if digest != run["input_hash"]:
            return self._finish(run_id, "failed", reason="输入清单指纹不符")
        entries = self.store.manifest_texts(manifest)
        kind = "review" if run["purpose"] in REVIEW_PASSES else "draft"
        clarify = bool(manifest.get("allowClarify")) and kind == "draft"
        if kind == "review":
            required = [s["key"] for s in self._snapshot_for(task)[1] if s["strength"] == "required"]
            if run["purpose"] == "review":
                trailer = prompts.review_trailer(run_id, digest, run["target_version_id"], required)
            else:
                trailer = prompts.segment_review_trailer(run_id, digest, run["target_version_id"], run["purpose"],
                                                         manifest.get("scope"), required)
        else:
            trailer = prompts.draft_trailer(run_id, digest, task["artifact_id"], run["base_version_id"], task["stage"])
            if clarify:
                trailer += ("\n回包放在 result 字段里。若要求含义不明、无法确定修改范围，先返回 kind=clarification："
                            "question 写一个问题，options 给可点选的答案，relatedIds 写相关对象编号；否则返回 draft。")
        prompt = prompts.prompt_from(entries, trailer, run["purpose"])
        workdir = self.store.run_dir(run_id) / "work"
        workdir.mkdir(parents=True, exist_ok=True)
        images = []
        for n, e in enumerate(x for x in entries if x.get("binary")):
            path = workdir / f"{n + 1:02d}_{e['name']}"
            path.write_bytes(self.store.read_blob(e["sha256"]))
            images.append(path)
        executor = self.executors[run["provider"]]
        request = ExecRequest(run_id=run_id, purpose=run["purpose"], kind=kind, prompt=prompt,
                              schema=contracts.wrapped_schema() if clarify else contracts.branch_schema(kind),
                              session_mode=run["session_mode"],
                              workdir=workdir, model=run["requested_model"], effort=run["requested_effort"],
                              timeout=int(self.get_setting("executor_timeout", 600)), images=images)
        try:
            outcome = executor.start(request)
        except Exception as exc:  # adapter crash is a failed run, never a success
            return self._finish(run_id, "failed", reason="executor_error", diagnostic=repr(exc)[:500])
        self._event(run_id, dict(type="executor_finished", ok=outcome.ok, error=outcome.error_class,
                                 events=outcome.events, isolation=outcome.isolation))
        attempts = 0
        if outcome.ok and clarify and isinstance(outcome.structured, dict) and "result" in outcome.structured:
            outcome.structured = outcome.structured["result"]
        while outcome.ok:
            if clarify and isinstance(outcome.structured, dict) and outcome.structured.get("kind") == "clarification":
                kind = "clarification"
            problems = self._precheck(run, task, kind, outcome.structured)
            if not problems:
                break
            if attempts >= 1:
                return self._finish(run_id, "failed", reason="回包两次不符合契约", diagnostic="; ".join(problems),
                                    outcome=outcome, attempts=attempts)
            attempts += 1   # one same-content re-format request; not counted as a revision round
            request.prompt = prompt + "\n上次回包不符合契约：" + "; ".join(problems) + "\n请只修正格式，内容不变，重新回包。"
            outcome = executor.start(request)
        if not outcome.ok:
            status = "waiting_quota" if outcome.error_class == "quota" else "failed"
            return self._finish(run_id, status, reason=outcome.error_class, diagnostic=outcome.diagnostic,
                                outcome=outcome, attempts=attempts)
        (self.store.run_dir(run_id) / "result.json").write_text(json.dumps(outcome.structured, ensure_ascii=False, indent=2))
        return self._ingest(run_id, kind, outcome, attempts)

    def _snapshot_for(self, task):
        with connect(self.db_path) as db:
            k = self._kickoff(db, task["episode_id"])
        return k["settings_revision"], k["settings_snapshot"]

    def _event(self, run_id, payload):
        with transaction(self.db_path) as db:
            seq = db.execute("SELECT coalesce(max(seq),0)+1 FROM event WHERE run_id=?", (run_id,)).fetchone()[0]
            db.execute("INSERT INTO event(run_id,seq,payload) VALUES(?,?,?)", (run_id, seq, dumps(payload)))

    def _finish(self, run_id, status, reason=None, diagnostic=None, outcome=None, attempts=0, result_hash=None, db=None):
        def apply(db):
            fields = dict(status=status, reason=reason, diagnostic=diagnostic, ended_at=self.clock(),
                          format_attempts=attempts, result_hash=result_hash)
            if outcome:
                fields.update(session_id=outcome.session_id, reported_model=outcome.reported_model,
                              reported_effort=outcome.reported_effort, quota_resets_at=outcome.quota_resets_at,
                              executable=outcome.executable, executor_version=outcome.version)
            db.execute("UPDATE run SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=? AND status<>'stopped'",
                       (*fields.values(), run_id))
            task_id = db.execute("SELECT task_id FROM run WHERE id=?", (run_id,)).fetchone()[0]
            state = {"waiting_quota": "waiting_quota", "failed": "failed"}.get(status, "awaiting_decision")
            db.execute("UPDATE task SET state=? WHERE id=? AND state IN ('running','queued')", (state, task_id))
        if db is not None:
            apply(db)
        else:
            with transaction(self.db_path) as db2:
                apply(db2)
        return []

    def _precheck(self, run, task, kind, result) -> list[str]:
        problems = contracts.validate(result)
        if problems:
            return problems
        if result.get("kind") != kind:
            return [f"结果类型应为 {kind}"]
        if result["runId"] != run["id"]:
            return ["runId 与本次运行不符"]
        if result["inputFingerprint"] != run["input_hash"]:
            return ["inputFingerprint 与输入清单不符"]
        if kind == "clarification":
            return []
        if kind == "draft":
            if result["baseVersionId"] != run["base_version_id"]:
                return ["baseVersionId 与本次基版不符"]
            fields = [c["field"] for c in result["changes"]]
            if len(set(fields)) != len(fields):
                return ["同一字段重复修改"]
            bad = [f"{c['targetId']}.{c['field']}" for c in result["changes"]
                   if c["targetId"] != task["artifact_id"] or c["field"] not in WRITABLE[task["stage"]]]
            return [f"越界修改：{', '.join(bad)}"] if bad else []
        revision, snapshot = self._snapshot_for(task)
        keys = {s["key"] for s in snapshot}
        required = {s["key"] for s in snapshot if s["strength"] == "required"}
        vid = run["target_version_id"]
        problems = []
        if result["reviewedVersionId"] != vid:
            problems.append("reviewedVersionId 与被审版本不符")
        expected_pass = REVIEW_PASSES[run["purpose"]]
        if result["pass"] != expected_pass:
            problems.append(f"遍次应为 {expected_pass}")
        if expected_pass == "cold_read" and any(i["settingId"] for i in result["issues"]):
            problems.append("冷读不开放设定，settingId 须为 null")
        for issue in result["issues"]:
            if issue["settingId"] is not None and issue["settingId"] not in keys:
                problems.append(f"不存在的设定编号：{issue['settingId']}")
            if issue["location"]["versionId"] != vid:
                problems.append("问题位置不属于被审版本")
        covered = {o["objectId"] for o in result["observations"]}
        if expected_pass == "story" and (missing := sorted(required - covered)):
            problems.append("未逐条核对必守设定：" + "、".join(missing))
        if result["decision"] == "pass" and result["issues"]:
            problems.append("pass 不能带未解决问题")
        if result["decision"] != "pass" and not result["issues"]:
            problems.append("return/uncertain 必须有对应意见")
        return problems

    def _ingest(self, run_id, kind, outcome, attempts):
        result = outcome.structured
        body = canonical(result)
        follow = []
        with transaction(self.db_path) as db:
            run = _row(db, "SELECT * FROM run WHERE id=?", run_id)
            task = _row(db, "SELECT * FROM task WHERE id=?", run["task_id"])
            if kind == "clarification":
                thread = db.execute("SELECT id FROM thread WHERE task_id=?", (task["id"],)).fetchone()[0]
                self._message(db, thread, "assistant", result["question"], None, None, run_id, kind="clarify",
                              payload=dict(options=result["options"], relatedIds=result["relatedIds"],
                                           baseVersionId=run["base_version_id"]))
                self._finish(run_id, "succeeded", outcome=outcome, attempts=attempts,
                             result_hash=sha256_bytes(body), db=db)
                db.execute("UPDATE task SET state='clarifying' WHERE id=?", (task["id"],))
            elif kind == "draft":
                if db.execute("SELECT 1 FROM version WHERE source_run_id=?", (run_id,)).fetchone():
                    raise Conflict("该运行的结果已入库")   # runId is consumed once
                base = json.loads(self._version_of(db, run["base_version_id"], task["artifact_id"])["content"]) \
                    if run["base_version_id"] else {}
                try:
                    content = dict(base, **{c["field"]: normalize(task["stage"], c["field"], c["value"])
                                            for c in result["changes"]})
                except ValueError:
                    raise Rejected("字段内容不是有效 JSON")
                vid = self._new_version(db, task["artifact_id"], run["base_version_id"], "ai", content, run_id)
                thread = db.execute("SELECT id FROM thread WHERE task_id=?", (task["id"],)).fetchone()[0]
                self._message(db, thread, "assistant", f"已生成草稿 R{self._display_no(db, vid):02d}", None, None, run_id,
                              kind="draft", payload=dict(versionId=vid, baseVersionId=run["base_version_id"],
                                                         provider=run["provider"]))
                self._finish(run_id, "succeeded", outcome=outcome, attempts=attempts,
                             result_hash=sha256_bytes(body), db=db)
                if task["auto_revise"] and task["stage"] == "story":   # expert mode: draft goes straight to review
                    follow.append(self._queue_run(db, task["id"], "review", None, "", target_version_id=vid))
            else:
                rid = new_id()
                manifest, _ = self.store.load_manifest(run["manifest_relpath"])
                scope, scope_hash = manifest.get("scope") or "", manifest.get("scopeHash")
                pass_name = REVIEW_PASSES[run["purpose"]]
                pass_no = db.execute("SELECT count(*)+1 FROM review WHERE version_id=? AND pass=? AND scope=?",
                                     (run["target_version_id"], pass_name, scope)).fetchone()[0]
                snapshot = {s["key"]: s for s in self._kickoff(db, task["episode_id"])["settings_snapshot"]}
                db.execute("INSERT INTO review(id,version_id,run_id,input_hash,pass_no,pass,original_decision,isolation_level,"
                           "reviewer,observations,created_at,scope,scope_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (rid, run["target_version_id"], run_id, run["input_hash"], pass_no, pass_name,
                            result["decision"], "session", run["provider"], dumps(result["observations"]), self.clock(),
                            scope, scope_hash))
                for n, issue in enumerate(result["issues"], 1):
                    setting = snapshot.get(issue["settingId"]) if issue["settingId"] else None
                    blocking = issue["category"] == "format" or bool(setting and setting["strength"] == "required")
                    db.execute("INSERT INTO issue VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (new_id(), rid, n, dumps(issue["location"]), issue["category"], issue["settingId"],
                                setting["content"] if setting else None, issue["decision"], issue["reason"], int(blocking)))
                thread = db.execute("SELECT id FROM thread WHERE task_id=?", (task["id"],)).fetchone()[0]
                self._message(db, thread, "system", f"独立审查（会话隔离）结论：{result['decision']}，"
                              f"{len(result['issues'])} 条意见", None, None, run_id)
                self._finish(run_id, "succeeded", outcome=outcome, attempts=attempts,
                             result_hash=sha256_bytes(body), db=db)
                follow += self._after_review(db, task, run, rid, result)
                if result["decision"] != "pass" and task["auto_revise"] and pass_name == "story":
                    if task["used_rounds"] < task["max_rounds"]:
                        db.execute("UPDATE task SET used_rounds=used_rounds+1 WHERE id=?", (task["id"],))
                        request = "按独立审查意见修订：\n" + "\n".join(
                            f"- {i['location']['anchor']}：{i['reason']}" for i in result["issues"])
                        follow.append(self._queue_run(db, task["id"], "revise", run["target_version_id"], request,
                                                      counted=True))
                    else:
                        db.execute("UPDATE task SET state='rounds_exhausted' WHERE id=?", (task["id"],))
        return follow

    def _display_no(self, db, vid):
        return db.execute("SELECT display_no FROM version WHERE id=?", (vid,)).fetchone()[0]

    def _new_version(self, db, artifact_id, base_id, origin, content, run_id=None, note=""):
        no = db.execute("SELECT coalesce(max(display_no),0)+1 FROM version WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
        vid = new_id()
        blob = self.store.put_blob(canonical(content))
        db.execute("INSERT INTO version VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (vid, artifact_id, no, base_id, origin, run_id, dumps(content), blob, note, self.clock()))
        return vid

    # ------------------------------------------------------------------ manual drafts and review
    def save_manual(self, artifact_id, base_version_id, content, command_id, origin="manual", note=""):
        payload = dict(artifact_id=artifact_id, base=base_version_id, content=content, origin=origin)
        with transaction(self.db_path) as db:
            def run():
                art = _row(db, "SELECT * FROM artifact WHERE id=?", artifact_id, missing="成果不存在")
                base = {}
                if base_version_id:
                    base = json.loads(self._version_of(db, base_version_id, artifact_id)["content"])
                unknown = set(content) - set(WRITABLE[art["stage"]])
                if unknown:
                    raise Rejected("不可写字段：" + "、".join(sorted(unknown)))
                vid = self._new_version(db, artifact_id, base_version_id, origin, dict(base, **content), note=note)
                return dict(versionId=vid, displayNo=self._display_no(db, vid))
            return self._command(db, command_id, "save_manual", payload, run)

    def submit_review(self, version_id, command_id, task_id=None, provider=None, model=None, effort=None):
        """送审: freeze the read version and queue a new-session review run."""
        payload = dict(version_id=version_id, task_id=task_id, provider=provider, model=model, effort=effort)
        with transaction(self.db_path) as db:
            def run():
                version = _row(db, "SELECT * FROM version WHERE id=?", version_id, missing="版本不存在")
                art = _row(db, "SELECT * FROM artifact WHERE id=?", version["artifact_id"])
                if art["stage"] not in ("story", "script"):
                    raise Rejected("提示词使用两遍独审，其余阶段没有文本独审")
                tid = task_id
                if tid:
                    task = _row(db, "SELECT * FROM task WHERE id=?", tid)
                    if task["artifact_id"] != art["id"]:
                        raise Rejected("任务不属于该成果")
                else:
                    row = db.execute("SELECT id FROM task WHERE artifact_id=? ORDER BY created_at DESC LIMIT 1",
                                     (art["id"],)).fetchone()
                    if not row:
                        raise Rejected("请先在本集建立故事任务")
                    tid = row[0]
                run_id = self._queue_run(db, tid, "review", None, "", target_version_id=version_id,
                                         provider=provider, model=model, effort=effort)
                if art["stage"] == "story":
                    db.execute("UPDATE topic SET status='待审稿' WHERE episode_id=? AND status='开发中'", (art["episode_id"],))
                return dict(runId=run_id, taskId=tid)
            return self._command(db, command_id, "submit_review", payload, run)

    # ------------------------------------------------------------------ waivers and adoption
    def _latest_review(self, db, version_id):
        row = db.execute("SELECT * FROM review WHERE version_id=? ORDER BY pass_no DESC LIMIT 1", (version_id,)).fetchone()
        return dict(row) if row else None

    def waive(self, issue_id, version_id, reason, command_id):
        payload = dict(issue_id=issue_id, version_id=version_id, reason=reason)
        with transaction(self.db_path) as db:
            def run():
                issue = _row(db, "SELECT * FROM issue WHERE id=?", issue_id, missing="意见不存在")
                review = _row(db, "SELECT * FROM review WHERE id=?", issue["review_id"])
                if review["version_id"] != version_id:
                    raise Rejected("放行只适用于被审版本")
                latest = self._latest_review(db, version_id)
                if latest["id"] != review["id"]:
                    raise Rejected("该意见属于旧的审查遍次")
                if issue["blocking"]:
                    raise Rejected("必守设定冲突或格式错误不能放行，请改故事或去项目设定")
                if not reason.strip():
                    raise Rejected("放行须写理由")
                wid = new_id()
                db.execute("INSERT INTO waiver(id,issue_id,version_id,reason,created_at) VALUES(?,?,?,?,?)", (wid, issue_id, version_id, reason.strip(), self.clock()))
                return dict(waiverId=wid)
            return self._command(db, command_id, "waive", payload, run)

    def adoption_gate(self, version_id, db=None):
        def compute(db):
            version = _row(db, "SELECT * FROM version WHERE id=?", version_id, missing="版本不存在")
            art = _row(db, "SELECT * FROM artifact WHERE id=?", version["artifact_id"])
            content = json.loads(version["content"])
            gate = dict(versionId=version_id, formatErrors=check_content(art["stage"], content,
                                                                         self._adopted_plan(db, art["episode_id"])),
                        review=None,
                        blocking=[], unresolved=[], reviewRequired=art["stage"] == "story")
            if art["stage"] == "story":
                review = self._latest_review(db, version_id)
                if review:
                    issues = [dict(r) for r in db.execute(
                        "SELECT i.*, w.reason AS waiver_reason FROM issue i LEFT JOIN waiver w ON w.issue_id=i.id "
                        "AND w.version_id=? WHERE i.review_id=? ORDER BY ordinal", (version_id, review["id"]))]
                    gate["review"] = dict(id=review["id"], decision=review["original_decision"],
                                          isolation=review["isolation_level"], passNo=review["pass_no"])
                    gate["blocking"] = [i["id"] for i in issues if i["blocking"]]
                    gate["unresolved"] = [i["id"] for i in issues if not i["blocking"] and not i["waiver_reason"]]
            gate["adoptable"] = (not gate["formatErrors"] and not gate["blocking"] and not gate["unresolved"]
                                 and (gate["review"] is not None or not gate["reviewRequired"]))
            return gate
        if db is not None:
            return compute(db)
        with connect(self.db_path) as db2:
            return compute(db2)

    def adopt(self, artifact_id, version_id, expected_adopted_version_id, command_id, and_continue=False):
        payload = dict(artifact_id=artifact_id, version_id=version_id, expected=expected_adopted_version_id,
                       cont=and_continue)
        with transaction(self.db_path) as db:
            def run():
                art = _row(db, "SELECT * FROM artifact WHERE id=?", artifact_id, missing="成果不存在")
                self._version_of(db, version_id, artifact_id)
                if art["adopted_version_id"] != expected_adopted_version_id:
                    raise Conflict("采用版已变化，请比较后重新决定", current=art["adopted_version_id"])
                if version_id == art["adopted_version_id"]:
                    raise Rejected("该版本已是采用版")
                gate = self.adoption_gate(version_id, db)
                if not gate["adoptable"]:
                    if gate["formatErrors"]:
                        raise Rejected("结构错误：" + "；".join(gate["formatErrors"]))
                    if gate["reviewRequired"] and gate["review"] is None:
                        raise Rejected("必需独审未执行")
                    if gate["blocking"]:
                        raise Rejected("存在必守设定冲突或硬错误，不能采用")
                    raise Rejected("仍有普通意见未处理")
                changed = db.execute("UPDATE artifact SET adopted_version_id=?, revision=revision+1 WHERE id=? AND "
                                     "adopted_version_id IS ?", (version_id, artifact_id, expected_adopted_version_id))
                if changed.rowcount != 1:
                    raise Conflict("采用版已变化", current=art["adopted_version_id"])
                aid = new_id()
                db.execute("INSERT INTO adoption VALUES(?,?,?,?,?,?,?)",
                           (aid, artifact_id, version_id, expected_adopted_version_id,
                            gate["review"]["id"] if gate["review"] else None, command_id, self.clock()))
                db.execute("UPDATE task SET state='closed' WHERE artifact_id=? AND state IN ('awaiting_decision',"
                           "'rounds_exhausted','active')", (artifact_id,))
                result = dict(adoptionId=aid, adoptedVersionId=version_id)
                result["impacts"] = self._after_adopt(db, art, version_id, expected_adopted_version_id)
                if and_continue and art["stage"] in NEXT:
                    ep = _row(db, "SELECT * FROM episode WHERE id=?", art["episode_id"])
                    result["next"] = dict(stage=NEXT[art["stage"]],
                                          artifactId=self._ensure_artifact(db, ep, NEXT[art["stage"]]))
                return result
            if not command_id:
                raise Rejected("采用需要命令号")
            return self._command(db, command_id, "adopt", payload, run)

    # ------------------------------------------------------------------ stage helpers (overridden by mixins)
    def _ensure_artifact(self, db, ep, stage):
        row = db.execute("SELECT id FROM artifact WHERE episode_id=? AND stage=?", (ep["id"], stage)).fetchone()
        if row:
            return row[0]
        aid = new_id()
        db.execute("INSERT INTO artifact(id,project_id,episode_id,stage) VALUES(?,?,?,?)",
                   (aid, ep["project_id"], ep["id"], stage))
        return aid

    def _adopted_content(self, db, episode_id, stage):
        row = db.execute("SELECT v.content FROM artifact a JOIN version v ON v.id=a.adopted_version_id "
                         "WHERE a.episode_id=? AND a.stage=?", (episode_id, stage)).fetchone()
        return json.loads(row[0]) if row else None

    def _adopted_plan(self, db, episode_id):
        return self._adopted_content(db, episode_id, "plan")

    def _upstream_text(self, db, task):
        prev = next((k for k, v in NEXT.items() if v == task["stage"]), None)
        content = self._adopted_content(db, task["episode_id"], prev) if prev else None
        return render_stage(prev, content) if content else None

    def _extra_creation_inputs(self, db, task):
        return []

    def _isolation_status(self, provider):
        return getattr(self.executors[provider], "isolation", "session")

    def _extra_review_inputs(self, db, task, version):
        return []

    def _after_adopt(self, db, art, version_id, previous_id):
        return []

    def _after_review(self, db, task, run, review_id, result):
        return []

    # ------------------------------------------------------------------ read models
    def episode_view(self, episode_id):
        with connect(self.db_path) as db:
            ep = _loads(self._episode_in(db, episode_id), "leftover_facts")
            kick = db.execute("SELECT * FROM kickoff WHERE episode_id=?", (episode_id,)).fetchone()
            view = dict(episode=ep, kickoff=_loads(dict(kick), "predecessor_ids", "carried_facts", "settings_snapshot",
                                                   "spec", "executor_plan") if kick else None, artifacts=[])
            if kick and kick["source_import_id"]:
                view["sourceImport"] = dict(db.execute(
                    "SELECT id,original_name,sha256,purpose,parsed_text FROM import_record WHERE id=?",
                    (kick["source_import_id"],)).fetchone())
            view["refPacks"] = []
            for pk in db.execute("SELECT DISTINCT p.* FROM ref_pack p WHERE p.episode_id=? OR p.id IN (SELECT pack_id FROM task_pack tp "
                                 "JOIN task t ON t.id=tp.task_id WHERE t.episode_id=?) OR p.id IN (SELECT pack_id FROM shot_bookmark "
                                 "WHERE episode_id=?)", (episode_id, episode_id, episode_id)):
                src = db.execute("SELECT name, sha256 FROM ref_source WHERE id=?", (pk["source_id"],)).fetchone()
                view["refPacks"].append(dict(packId=pk["id"], version=pk["version"], source=src["name"], sha256=src["sha256"],
                                             beats=[r[0] for r in db.execute("SELECT code FROM beat WHERE pack_id=?", (pk["id"],))],
                                             bookmarks=[r[0] for r in db.execute(
                                                 "SELECT s.code FROM shot_bookmark b JOIN ref_shot s ON s.id=b.ref_shot_id "
                                                 "WHERE b.pack_id=? AND b.episode_id=?", (pk["id"], episode_id))]))
            for art in db.execute("SELECT * FROM artifact WHERE episode_id=? ORDER BY stage DESC", (episode_id,)):
                art = dict(art)
                art["versions"] = []
                for v in db.execute("SELECT * FROM version WHERE artifact_id=? ORDER BY display_no", (art["id"],)):
                    v = _loads(dict(v), "content")
                    v["gate"] = self.adoption_gate(v["id"], db)
                    v["reviews"] = []
                    for r in db.execute("SELECT r.*, run.manifest_relpath, run.input_hash AS manifest_hash, "
                                        "run.session_id FROM review r JOIN run ON run.id=r.run_id WHERE version_id=? "
                                        "ORDER BY pass_no", (v["id"],)):
                        r = _loads(dict(r), "observations")
                        r["issues"] = [_loads(dict(i), "loc") for i in db.execute(
                            "SELECT i.*, w.reason AS waiver_reason FROM issue i LEFT JOIN waiver w ON w.issue_id=i.id "
                            "WHERE review_id=? ORDER BY ordinal", (r["id"],))]
                        v["reviews"].append(r)
                    art["versions"].append(v)
                art["tasks"] = [self._task_view(db, dict(t)) for t in db.execute(
                    "SELECT * FROM task WHERE artifact_id=? ORDER BY created_at", (art["id"],))]
                view["artifacts"].append(art)
        return view

    def _task_view(self, db, task):
        task["executor_plan"] = json.loads(task["executor_plan"])
        thread = db.execute("SELECT id FROM thread WHERE task_id=?", (task["id"],)).fetchone()[0]
        task["threadId"] = thread
        task["messages"] = [dict(m) for m in db.execute("SELECT * FROM message WHERE thread_id=? ORDER BY seq", (thread,))]
        task["runs"] = [dict(r) for r in db.execute(
            "SELECT id,purpose,counted_round,provider,requested_model,requested_effort,reported_model,session_id,"
            "session_mode,isolation_level,status,reason,diagnostic,format_attempts,quota_resets_at,input_hash,"
            "manifest_relpath,base_version_id,target_version_id,created_at,ended_at FROM run WHERE task_id=? "
            "ORDER BY created_at", (task["id"],))]
        return task

    def run_manifest(self, run_id):
        with connect(self.db_path) as db:
            run = _row(db, "SELECT manifest_relpath,input_hash FROM run WHERE id=?", run_id)
        manifest, digest = self.store.load_manifest(run["manifest_relpath"])
        return dict(manifest=manifest, fingerprint=digest, matches=digest == run["input_hash"])

    def probe(self):
        return {name: ex.probe() for name, ex in self.executors.items()}


from .delivery import DeliveryMixin          # noqa: E402
from .images import ImagesMixin              # noqa: E402
from .production import ProductionMixin      # noqa: E402
from .projectops import ProjectOpsMixin      # noqa: E402
from .reviews2 import PromptReviewMixin      # noqa: E402


class Workbench(ProductionMixin, ImagesMixin, PromptReviewMixin, DeliveryMixin, ProjectOpsMixin, Core):
    """The application service: core commands plus the production, image, review, delivery and project mixins."""
