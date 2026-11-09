"""Workbench commands. Models only submit results; adoption, waivers and settings stay with the user."""
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import time

from . import kickoffchecks, contracts, prompts, acting, shotrefs, imagechoices, attachments
from .db import dumps, initialize, new_id, transaction, connect
from .executors.base import ExecRequest
from .executors.cli import redact
from .manuscripts import extract_manuscript
from .freechat import FreeChatMixin
from .episodecontact import EpisodeContactMixin
from .custommethods import CustomMethodsMixin, pin_at_kickoff
from .methodpack import load_config, load_pack, method_entries
from .storage import Store, canonical, sha256_bytes
from .stages import NEXT, WRITABLE, check_content, normalize, render as render_stage
from .specs import validate_spec, grid_dimensions, check_generation_aspect
from .story_skill import historical_method
from .storycheck import adaptation_errors, render_story

EDITOR_GUARD = ContextVar("editor_guard", default=None)
ROUND_LIMIT = 2
REVIEW_PASSES = {"review": "story", "cold_read": "cold_read", "comparison": "comparison"}


from .errors import Conflict, NotFound, Rejected, row as _row, loads as _loads


COOPERATION = {
    "beginner": "推荐最短的下一步，只在缺关键输入时提问。导演指定试写或返回时按要求继续。",
    "advanced": "按导演当前目标推进，允许跳步与下游试写。列明草稿来源、未定项和假设，避免停下要求选择菜单。",
    "expert": "直接处理导演指定的内容，简述必要冲突与取舍，省略基础讲解。试写列明依据与假设。",
}


class Core(kickoffchecks.KickoffChecks):
    def __init__(self, data_dir: Path, executors: dict, clock=time.time, image_providers=None):
        self.store = Store(data_dir)
        self.db_path = self.store.db_path
        self.executors = executors
        self.image_providers_map = image_providers or {}
        self.session_api_keys = {}
        self.clock = clock
        initialize(self.db_path)
        self.mark_interrupted_requests()
        with transaction(self.db_path) as db:   # text runs left running by a previous process: retryable, no paid side effect
            db.execute("UPDATE expert_auth SET state='stopped', stop_reason=? WHERE state='running'",
                       (dumps(dict(reason="interrupted", token=new_id(), detail="服务重启，请核对进度后恢复")),))
            db.execute("UPDATE kickoff_check SET state='failed',error='服务重启时中断，可手动重试' WHERE state='running'")
            db.execute("UPDATE free_turn SET status='failed',reason='服务重启时中断，可核对后重试' WHERE status IN ('running','queued')")
            for run in db.execute("SELECT * FROM run WHERE status='running'").fetchall():
                reason, detail = self._interrupted_result(db, run)
                self._finish(run["id"], "failed", reason=reason, diagnostic=detail, db=db)
                db.execute("UPDATE closure SET state='failed' WHERE run_id=? AND state='proposing'", (run["id"],))
            db.execute("UPDATE task SET state='failed' WHERE state='running' AND "
                       "EXISTS(SELECT 1 FROM run WHERE task_id=task.id AND reason='服务重启时中断') AND "
                       "NOT EXISTS(SELECT 1 FROM run WHERE task_id=task.id AND status IN ('running','queued')) "
                       "AND (SELECT status FROM run WHERE task_id=task.id ORDER BY created_at DESC,rowid DESC LIMIT 1)='failed'")

    def _interrupted_result(self, db, run):
        reason = "服务重启时中断"
        output = self.store.run_dir(run["id"])/"result.json"
        if not output.is_file():
            return reason, None
        try:
            result = json.loads(output.read_bytes())
            if run["purpose"] in ("create","revise","parallel"):
                task = _row(db,"SELECT * FROM task WHERE id=?",run["task_id"])
                manifest, _ = self.store.load_manifest(run["manifest_relpath"])
                if manifest.get("stageDialogue"):
                    _, problems = self._check_stage_dialogue(run,task,manifest["stageDialogue"],result)
                else:
                    problems = self._precheck(run,task,"draft",result)
                if problems:
                    return "已收到回包，未通过入库校验", redact("；".join(problems),500)
        except Exception as exc:
            return "已收到回包，恢复时无法核验", redact(str(exc),500)
        return "已收到回包，入库完成前服务中断", None

    # ------------------------------------------------------------------ idempotency
    def _existing_command(self, db, command_id, kind, payload):
        if not command_id:
            return None
        row = db.execute("SELECT kind,payload_hash,result FROM command WHERE command_id=?", (command_id,)).fetchone()
        if row:
            if row["kind"] != kind or row["payload_hash"] != sha256_bytes(canonical(payload)):
                raise Conflict("同一命令号已用于不同内容")
            return json.loads(row["result"])
        return None

    def _command(self, db, command_id, kind, payload, fn):
        if not command_id:
            return fn()
        prior = self._existing_command(db, command_id, kind, payload)
        if prior is not None:
            return prior
        result = fn()
        db.execute("INSERT INTO command VALUES(?,?,?,?,?)", (command_id, kind, sha256_bytes(canonical(payload)), dumps(result), self.clock()))
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

    def available_configs(self):
        from .methodpack import list_configs
        return list_configs()

    def export_project_folder(self, project_id, folder):
        from . import projectfolder
        return projectfolder.export(self,project_id,folder)

    def rename_project(self, project_id, name, expected_name, command_id=None):
        if not isinstance(name, str) or not name.strip():
            raise Rejected("项目名称不能为空")
        name = name.strip()
        payload = dict(project_id=project_id, name=name, expected_name=expected_name)
        with transaction(self.db_path) as db:
            def run():
                current = _row(db, "SELECT name FROM project WHERE id=?", project_id)["name"]
                if current != expected_name:
                    raise Conflict("项目名称已更改，请刷新后重试", current=current)
                db.execute("UPDATE project SET name=?,revision=revision+1 WHERE id=?", (name, project_id))
                return dict(projectId=project_id, name=name)
            return self._command(db, command_id, "rename_project", payload, run)

    def set_recent_visibility(self, project_id, visible, command_id=None):
        if not isinstance(visible, bool):
            raise Rejected("请选择项目是否显示在最近列表")
        with transaction(self.db_path) as db:
            def run():
                _row(db, "SELECT id FROM project WHERE id=?", project_id)
                db.execute("INSERT INTO project_recent(project_id,visible) VALUES(?,?) ON CONFLICT(project_id) DO UPDATE "
                           "SET visible=excluded.visible", (project_id, visible))
                return dict(projectId=project_id, visible=visible)
            return self._command(db, command_id, "set_recent_visibility",
                                 dict(project_id=project_id, visible=visible), run)

    def visit_project(self, project_id):
        with transaction(self.db_path) as db:
            _row(db, "SELECT id FROM project WHERE id=?", project_id)
            db.execute("INSERT INTO project_recent(project_id,last_opened) VALUES(?,?) ON CONFLICT(project_id) "
                       "DO UPDATE SET last_opened=excluded.last_opened", (project_id, self.clock()))
        return dict(projectId=project_id)

    def episode_owner(self, episode_id):
        with connect(self.db_path) as db:
            return _row(db, "SELECT project_id FROM episode WHERE id=?", episode_id)

    def list_projects(self):
        with connect(self.db_path) as db:
            projects = {r["id"]:dict(r, recent=[], deliveries=[]) for r in db.execute(
                "SELECT p.id,p.name,p.summary,p.config_id,COALESCE(pr.visible,1) AS recentVisible,COALESCE(pr.last_opened,p.created_at) AS recentAt,(SELECT count(*) FROM episode e WHERE e.project_id=p.id) AS episodes,"
                "(SELECT count(*) FROM setting_entry s WHERE s.project_id=p.id AND retired_revision IS NULL) AS settingsCount,"
                "(SELECT v.sha256 FROM asset a JOIN asset_version v ON v.asset_id=a.id WHERE a.project_id=p.id AND a.kind='image' "
                "ORDER BY v.grid,v.created_at DESC LIMIT 1) AS cover FROM project p LEFT JOIN project_recent pr ON pr.project_id=p.id ORDER BY p.created_at DESC")}
            for r in db.execute("SELECT e.id,e.project_id,e.title,e.display_code,e.created_at,e.state FROM episode e JOIN kickoff k "
                                "ON k.episode_id=e.id ORDER BY e.created_at DESC"):
                recent = projects[r["project_id"]]["recent"]
                if len(recent)<5:
                    recent.append(dict(r))
            for r in db.execute("SELECT d.id,d.episode_id,d.scope,d.created_at,e.title,e.display_code,e.project_id "
                                "FROM delivery d JOIN episode e ON e.id=d.episode_id ORDER BY d.created_at DESC"):
                projects[r["project_id"]]["deliveries"].append(dict(r))
            return list(projects.values())

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
        if provided_source == "episode_story":
            if not episode_id or purpose not in ("story_source","script_source"):
                raise Rejected("请选择本集故事稿或剧本")
            payload["forStory"] = True
        parsed = extract_manuscript(name, data) if purpose in ("story_source", "script_source") else None
        with transaction(self.db_path) as db:
            _row(db, "SELECT id FROM project WHERE id=?", project_id, missing="项目不存在")
            if episode_id:
                self._episode_in(db, episode_id, project_id)
            if provided_source == "episode_story":
                self._kickoff(db, episode_id)
            def run():
                iid = new_id()
                rel = self.store.import_copy(project_id, iid, name, data)
                status, text, diag, method = parsed or ("parsed", None, None, None)
                db.execute("INSERT INTO import_record VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (iid, project_id, episode_id, Path(name).name, provided_source, purpose,
                            sha256_bytes(data), len(data), rel, status, text, diag, self.clock()))
                if method:
                    db.execute("INSERT INTO import_extraction VALUES(?,?)", (iid, method))
                return dict(importId=iid, parseStatus=status, diagnostic=diag, extractionMethod=method, preview=(text or "")[:800])
            return self._command(db, command_id, "import", payload, run)

    def _episode_manuscript(self, db, eid):
        return db.execute("SELECT id,original_name,sha256,parsed_text FROM import_record WHERE episode_id=? "
                          "AND provided_source='episode_story' AND parse_status='parsed' ORDER BY copied_at DESC,rowid DESC LIMIT 1",
                          (eid,)).fetchone()

    def read_import(self, import_id):
        with connect(self.db_path) as db:
            rec = _row(db, "SELECT i.*,x.method AS extractionMethod FROM import_record i LEFT JOIN import_extraction x ON x.import_id=i.id WHERE i.id=?", import_id)
        data = (self.store.root / rec["relpath"]).read_bytes()
        rec["intact"] = sha256_bytes(data) == rec["sha256"]
        return rec

    def _episode_in(self, db, episode_id, project_id=None):
        ep = _row(db, "SELECT * FROM episode WHERE id=?", episode_id, missing="集不存在")
        if project_id and ep["project_id"] != project_id:
            raise Rejected("对象不属于该项目")
        return ep

    # ------------------------------------------------------------------ kickoff
    def kickoff_preview(self, episode_id=None, relation="independent", predecessor_ids=(), project_id=None, display_code=""):
        with connect(self.db_path) as db:
            ep = self._episode_in(db, episode_id) if episode_id else dict(id="", project_id=project_id, display_code=display_code, leftover_facts="[]")
            project = _loads(_row(db, "SELECT * FROM project WHERE id=?", ep["project_id"]), "spec_preset")
            presets = list(self._project_presets(db,ep["project_id"]).values())
            revision, settings = self._settings(db, ep["project_id"])
            others = [dict(r) for r in db.execute(
                "SELECT id,display_code,leftover_facts FROM episode WHERE project_id=? AND id<>? ORDER BY display_code",
                (ep["project_id"], episode_id or ""))]
            carried = self._carried(db, ep, relation, predecessor_ids)
            imports = [dict(r) for r in db.execute(
                "SELECT id,original_name,purpose,parse_status FROM import_record WHERE project_id=? AND "
                "(episode_id=? OR episode_id IS NULL) AND purpose IN ('story_source','script_source')",
                (ep["project_id"], episode_id))]
        return dict(episode=_loads(ep, "leftover_facts"), settingsRevision=revision, settings=settings,
                    spec=project["spec_preset"], presets=presets, predecessorsAvailable=[_loads(o, "leftover_facts") for o in others],
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
                      command_id=None, project_id=None, display_code=None, leftover_facts=(), preset_name="default", preset_revision=None, use_story_skill=False, files=(), cooperation_mode=None):
        payload = dict(project_id=project_id, display_code=display_code, facts=list(leftover_facts), episode_id=episode_id, source_type=source_type, plan=executor_plan, imp=source_import_id,
                       idea=idea_text, topic=topic_id, goal=goal, relation=relation, pre=list(predecessor_ids),
                       spec=spec_overrides)
        if cooperation_mode is not None:
            if not isinstance(cooperation_mode, str) or cooperation_mode not in COOPERATION:
                raise Rejected("未知合作档位")
            payload["cooperationMode"] = cooperation_mode
        _, pictures = attachments.prepare(files, images_only=True)
        if pictures:
            payload["pictures"] = pictures
        if use_story_skill:
            payload["useStorySkill"] = True  # compatibility with saved kickoff command receipts
        if preset_name!="default" or preset_revision is not None:
            payload.update(presetName=preset_name, presetRevision=preset_revision)
        for role in ("create", "review"):
            selection = executor_plan.get(role) or {}
            self.validate_selection(selection.get("provider"), selection.get("model"), selection.get("effort"))
        new_episode = episode_id is None
        if new_episode:
            if not project_id or not str(display_code or "").strip():
                raise Rejected("开工前须填写项目和集号")
            episode_id = new_id()
        with transaction(self.db_path) as db:
            def run():
                if new_episode:
                    _row(db, "SELECT id FROM project WHERE id=?", project_id)
                    if db.execute("SELECT 1 FROM episode WHERE project_id=? AND display_code=?", (project_id,display_code)).fetchone():
                        raise Conflict(f"项目内已有集号 {display_code}")
                    db.execute("INSERT INTO episode(id,project_id,display_code,title,leftover_facts,created_at) VALUES(?,?,?,?,?,?)",
                               (episode_id,project_id,display_code,"",dumps(list(leftover_facts)),self.clock()))
                ep = self._episode_in(db, episode_id, project_id)
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
                project = _row(db, "SELECT spec_preset,method_pack FROM project WHERE id=?", ep["project_id"])
                preset = self._project_presets(db, ep["project_id"]).get(preset_name)
                if preset is None:
                    raise Rejected("所选预设不属于本项目")
                if preset_revision is not None and preset_revision != preset["rev"]:
                    raise Conflict("开工预览使用的预设已更新，请刷新后重新核对")
                spec = validate_spec(dict(preset["values"], **(spec_overrides or {})))
                revision, settings = self._settings(db, ep["project_id"])
                if db.execute("SELECT 1 FROM kickoff_check WHERE project_id=? AND display_code=? LIMIT 1",
                              (ep["project_id"], ep["display_code"])).fetchone():
                    item = kickoffchecks.material(self, db, ep["project_id"], ep["display_code"], episode_id,
                                                  source_type, idea_text, goal, source_import_id, topic_id)
                    check = kickoffchecks.snapshot(db, item)
                    if check["state"] == "running":
                        raise Conflict("本集开工资料正在核对，请等待结果")
                    if check["blocked"]:
                        raise Conflict("必守设定与本集要求存在冲突，请修改要求或核对引文")
                self._freeze_sources(db, episode_id, ep["project_id"])
                kid = new_id()
                db.execute("INSERT INTO kickoff VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (kid, episode_id, source_type, source_import_id, topic_id, idea_text, goal, relation,
                            dumps(list(predecessor_ids)), dumps(carried), revision, dumps(settings), dumps(spec),
                            dumps(executor_plan), self.clock()))
                db.execute("INSERT INTO kickoff_preset VALUES(?,?,?,?)",
                           (episode_id,preset_name,preset["rev"],dumps(preset["values"])))
                db.execute("INSERT INTO kickoff_method VALUES(?,?)", (episode_id, project["method_pack"]))
                pin_at_kickoff(db, ep["project_id"], episode_id)
                if cooperation_mode is not None:
                    db.execute("INSERT INTO episode_cooperation VALUES(?,?)", (episode_id, cooperation_mode))
                for picture, (_, data) in zip(pictures, files):
                    self.register_asset(ep["project_id"], picture["name"], data, "reference", episode_id=episode_id,
                                        origin="kickoff_reference", db=db)
                aid = new_id()
                db.execute("INSERT INTO artifact(id,project_id,episode_id,stage) VALUES(?,?,?,?)",
                           (aid, ep["project_id"], episode_id, "story"))
                db.execute("UPDATE episode SET state='in_production', revision=revision+1 WHERE id=?", (episode_id,))
                return dict(episodeId=episode_id, kickoffId=kid, storyArtifactId=aid, carriedFacts=carried)
            return self._command(db, command_id, "start_episode", payload, run)

    def _cooperation_mode(self, db, episode_id):
        result = db.execute("SELECT mode FROM episode_cooperation WHERE episode_id=?", (episode_id,)).fetchone()
        return result[0] if result else "advanced"

    def set_cooperation_mode(self, episode_id, mode, expected_mode):
        if not isinstance(mode, str) or mode not in COOPERATION:
            raise Rejected("未知合作档位")
        with transaction(self.db_path) as db:
            self._kickoff(db, episode_id)
            current = self._cooperation_mode(db, episode_id)
            if current not in (mode, expected_mode):
                raise Conflict("本集合作档位已变化，请刷新后再选")
            db.execute("INSERT INTO episode_cooperation VALUES(?,?) ON CONFLICT(episode_id) DO UPDATE SET mode=excluded.mode",
                       (episode_id, mode))
            if current != mode and "expert" in (current, mode):
                db.execute("UPDATE artifact SET revision=revision+1 WHERE episode_id=? AND adopted_version_id IS NOT NULL",
                           (episode_id,))
            return dict(mode=mode)

    def _check_editor_guard(self, db, artifact_id):
        guard = EDITOR_GUARD.get()
        if guard is None:
            return
        art = _row(db, "SELECT * FROM artifact WHERE id=?", artifact_id)
        if guard != [artifact_id,art["revision"]] or self._edit_state(db,art,self._cooperation_mode(db,art["episode_id"])) == "protected":
            raise Conflict("阶段或采用稿已变化，请刷新并打开当前稿再修改")

    def _change_edit_state(self, db, artifact_id, editing, revision):
        revision += 1
        db.execute("UPDATE artifact SET revision=? WHERE id=?", (revision, artifact_id))
        if editing:
            db.execute("INSERT INTO stage_edit_session VALUES(?,?) ON CONFLICT(artifact_id) "
                       "DO UPDATE SET revision=excluded.revision", (artifact_id, revision))
        return dict(artifactId=artifact_id, editing=editing, revision=revision)

    def _edit_state(self, db, art, mode):
        if not art["adopted_version_id"] or mode == "expert":
            return "open"
        session = db.execute("SELECT revision FROM stage_edit_session WHERE artifact_id=?", (art["id"],)).fetchone()
        return "editing" if session and session[0] == art["revision"] else "protected"

    def set_stage_edit(self, artifact_id, editing, expected_revision, command_id=None):
        if type(editing) is not bool or type(expected_revision) is not int:
            raise Rejected("请指定改稿状态和当前阶段版本")
        with transaction(self.db_path) as db:
            def change():
                art = _row(db, "SELECT * FROM artifact WHERE id=?", artifact_id)
                if art["revision"] != expected_revision:
                    raise Conflict("阶段状态已变化，请刷新后再修改")
                if not art["adopted_version_id"] or self._cooperation_mode(db, art["episode_id"]) == "expert":
                    raise Rejected("本阶段可以直接编辑")
                return self._change_edit_state(db, artifact_id, editing, expected_revision)
            return self._command(db, command_id, "set_stage_edit", [artifact_id,editing,expected_revision], change)

    def set_optional_stage(self, episode_id, stage, skipped, reason="", expected_reason=""):
        if stage not in ("space", "cast") or type(skipped) is not bool:
            raise Rejected("只能跳过或恢复空间锁定、角色出镜")
        if not isinstance(reason, str) or (skipped and not reason.strip()):
            raise Rejected("请填写跳过本步的原因")
        reason = reason.strip() if skipped else ""
        with transaction(self.db_path) as db:
            self._kickoff(db, episode_id)
            record = db.execute("SELECT reason FROM optional_stage_choice WHERE episode_id=? AND stage=?",
                                (episode_id, stage)).fetchone()
            current = record[0] if record else ""
            if current not in (expected_reason, reason):
                raise Conflict("本步安排已变化，请刷新后再操作")
            db.execute("INSERT INTO optional_stage_choice VALUES(?,?,?) ON CONFLICT(episode_id,stage) "
                       "DO UPDATE SET reason=excluded.reason", (episode_id, stage, reason))
            return dict(stage=stage, reason=reason)

    def _kickoff(self, db, episode_id):
        k = _row(db, "SELECT * FROM kickoff WHERE episode_id=?", episode_id, missing="本集尚未开工")
        return _loads(k, "predecessor_ids", "carried_facts", "settings_snapshot", "spec", "executor_plan")

    def _source_text(self, db, kickoff):
        if kickoff["source_type"] == "import":
            return db.execute("SELECT parsed_text FROM import_record WHERE id=?",
                              (kickoff["source_import_id"],)).fetchone()[0]
        if kickoff["source_type"] == "topic":
            t = db.execute("SELECT * FROM topic WHERE id=?", (kickoff["topic_id"],)).fetchone()
            return f"选题卡 {t['code']}：{t['premise']}\n机制：{t['mechanism']}\n角色动机：{t['motivation']}" + self._original_card(db,t)
        return kickoff["idea_text"]

    def _writing_inputs(self, db, task, pack, profile, settings_text, kickoff_text, base_content, request, upstream):
        entries = prompts.creation_inputs(pack, settings_text, kickoff_text, task["stage"], base_content,
                                          request, upstream, profile)
        disabled = self.get_setting(f"method-options:{task['episode_id']}:{task['stage']}", [])
        entries = [e for e in entries if e["role"] != "method" or e["name"] not in disabled]
        entries.extend(self._custom_method_entries(db, task))
        entries.extend(self._extra_creation_inputs(db, task))
        return entries

    def _stage_chat_inputs(self, db, eid, stage, source_version, thread_id):
        kickoff = self._kickoff(db, eid)
        pack, profile = self._episode_method(db, eid)
        task = dict(episode_id=eid, project_id=self._episode_in(db,eid)["project_id"], stage=stage, id=thread_id)
        entries = self._writing_inputs(db,task,pack,profile,prompts.render_settings(kickoff["settings_snapshot"]),
                                       prompts.render_kickoff(kickoff,self._source_text(db,kickoff)),None,
                                       "按本条消息讨论或试写。交代未定项与假设，试写不改变已采用成果。",None)
        basis = dict(versionId=None, label="本集开工资料与导演本条要求")
        if source_version:
            source = _row(db, "SELECT v.*,a.stage,a.adopted_version_id FROM version v JOIN artifact a ON a.id=v.artifact_id "
                          "WHERE a.episode_id=? AND v.id=?", eid, source_version, missing="指定稿件不属于本集")
            label = "已采用" if source["adopted_version_id"] == source_version else "未采用"
            basis = dict(versionId=source_version, stage=source["stage"], number=source["display_no"], label=label)
            entries.append(dict(role="trial_source",name=f"{source['stage']} R{source['display_no']:02d} · {label}",
                                text=render_stage(source["stage"],json.loads(source["content"]))))
        mode = self._cooperation_mode(db, eid)
        entries.append(dict(role="cooperation",name="本集合作方式",text=COOPERATION[mode]))
        header = dict(episodeId=eid, stage=stage, stageBasis=basis, creator=kickoff["executor_plan"]["create"])
        return header, entries

    # ------------------------------------------------------------------ tasks, threads, runs
    def create_task(self, episode_id, stage, goal, auto_revise=False, executor=None, command_id=None, selection=None,
                    prompt_images=None, files=(), trial_turn_id=None, workspace_revision=None, base_version_id=None):
        payload = dict(episode_id=episode_id, stage=stage, goal=goal, auto=auto_revise, ex=executor, sel=selection)
        extra, refs = attachments.prepare(files)
        if workspace_revision is not None:
            payload["workspaceRevision"] = workspace_revision
        if base_version_id is not None:
            payload["baseVersion"] = base_version_id
        if trial_turn_id is not None:
            payload["trialTurnId"] = trial_turn_id
        if refs:
            payload["attachments"] = refs
        if prompt_images is not None:
            if stage != "prompts" or type(prompt_images) is not bool:
                raise Rejected("发送绑定参考仅适用于提示词阶段")
            payload["prompt_images"] = prompt_images
        with transaction(self.db_path) as db:
            def run():
                ep = self._episode_in(db, episode_id)
                kickoff = self._kickoff(db, episode_id)
                plan = dict(kickoff["executor_plan"])
                if executor:
                    for role, selected in executor.items():
                        if role not in ("create", "review") or not isinstance(selected, dict):
                            raise Rejected("任务执行分工无效")
                        self.validate_selection(selected.get("provider"), selected.get("model"), selected.get("effort"))
                    plan.update({role:self.checked_connection(slot) for role,slot in executor.items()})
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
                if trial_turn_id:
                    turn = _row(db, "SELECT t.reply,t.input_hash,t.manifest_relpath FROM free_turn t JOIN episode_stage_thread s "
                                "ON s.thread_id=t.thread_id WHERE t.id=? AND s.episode_id=? AND s.stage=? AND t.status='succeeded'",
                                trial_turn_id, episode_id, stage, missing="请选择本集本阶段已经完成的试写")
                    manifest, digest = self.store.load_manifest(turn["manifest_relpath"])
                    if digest != turn["input_hash"]:
                        raise Rejected("试写输入清单指纹不符")
                    extra.append(dict(role="trial_reference", name="导演选用的试写 · "+trial_turn_id,
                                      text="试写依据："+dumps(manifest["stageBasis"])+"\n输入指纹："+digest+"\n"+turn["reply"]))
                aid = db.execute("SELECT id FROM artifact WHERE episode_id=? AND stage=?", (episode_id, stage)).fetchone()[0]
                tid, thread = self._draft_thread(db, ep["project_id"], episode_id, aid, stage, goal, plan, auto_revise, selection, refs)
                if prompt_images is not None:
                    db.execute("INSERT INTO prompt_draft_task VALUES(?,?)", (tid, int(prompt_images)))
                base = db.execute("SELECT adopted_version_id FROM artifact WHERE id=?", (aid,)).fetchone()[0] \
                    or self._latest_version(db, aid)
                if base_version_id:
                    base = self._version_of(db, base_version_id, aid)["id"]
                run_id = self._queue_run(db, tid, "revise" if base else "create", base, goal, selection,
                                         message_entries=extra, workspace_revision=workspace_revision)
                if trial_turn_id:
                    art = _row(db,"SELECT * FROM artifact WHERE id=?",aid)
                    if self._edit_state(db,art,self._cooperation_mode(db,episode_id)) == "protected":
                        self._change_edit_state(db,aid,True,art["revision"])
                return dict(taskId=tid, threadId=thread, runId=run_id, artifactId=aid)
            return self._command(db, command_id, "create_task", payload, run)

    def _draft_thread(self, db, project_id, episode_id, artifact_id, stage, goal, plan, auto_revise=False, selection=None, refs=None):
        tid, thread = new_id(), new_id()
        db.execute("INSERT INTO task(id,project_id,episode_id,artifact_id,stage,goal,auto_revise,max_rounds,"
                   "executor_plan,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (tid, project_id, episode_id, artifact_id, stage, goal, int(auto_revise), ROUND_LIMIT,
                    dumps(plan), self.clock()))
        db.execute("INSERT INTO thread VALUES(?,?,?)", (thread, tid, f"{stage}:{artifact_id}"))
        self._message(db, thread, "user", goal, selection, None, payload={"attachments":refs} if refs else None)
        return tid, thread

    def _snapshot_parts(self, run):
        manifest, digest = self.store.load_manifest(run["manifest_relpath"])
        if digest != run["input_hash"]:
            raise Rejected("原运行的输入清单指纹不符")
        header = {k:v for k,v in manifest.items() if k not in ("inputs", "runId")}
        entries = []
        for item in manifest["inputs"]:
            raw = self.store.read_blob(item["sha256"])
            value = {"data":raw} if item.get("binary") else {"text":raw.decode("utf-8")}
            entries.append(dict(role=item["role"], name=item["name"], **value))
        return header, entries

    def start_story_comparison(self, version_id, command_id, after_round=0, request=""):
        """Create both drafts atomically; two tabs asking for the same next round share its runs."""
        if type(after_round) is not int or after_round < 0:
            raise Rejected("并排轮次无效")
        if not isinstance(request,str) or len(request)>4000:
            raise Rejected("共同创作要求应为 4000 字以内的文字")
        goal = request.strip() or "以指定故事稿为共同基版，按本步要求写出一份可独立阅读和送审的完整故事稿。"
        next_round = after_round + 1
        with transaction(self.db_path) as db:
            def start():
                source = _row(db, "SELECT a.id AS aid,a.stage,a.project_id,a.episode_id FROM version v JOIN artifact a "
                                  "ON a.id=v.artifact_id WHERE v.id=?", version_id)
                if source["stage"] != "story":
                    raise Rejected("并排出稿需要一份故事稿")
                previous = db.execute("SELECT c.run_id,t.goal FROM story_candidate c JOIN run r ON r.id=c.run_id JOIN task t ON t.id=r.task_id "
                                      "WHERE c.source_version_id=? AND c.round_no=? ORDER BY c.side",
                                      (version_id,next_round)).fetchall()
                if previous:
                    if previous[0]["goal"] != goal:
                        raise Conflict("本轮已按另一份创作要求提交，请刷新后另起一轮")
                    return dict(runIds=[r[0] for r in previous], sourceVersionId=version_id, roundNo=next_round)
                latest = db.execute("SELECT coalesce(max(round_no),0) FROM story_candidate WHERE source_version_id=?",(version_id,)).fetchone()[0]
                if latest != after_round:
                    raise Conflict("并排轮次已变化，请刷新后重试")
                eid, aid = source["episode_id"], source["aid"]
                plan = self._kickoff(db, eid)["executor_plan"]
                version = self._version_of(db,version_id,aid)
                source_tasks = [r["task_id"] for r in self._version_creation_runs(db, version)]
                ids = []
                for side, role in (("A","create"), ("B","review")):
                    slot = plan[role]
                    self.validate_selection(slot["provider"], slot.get("model"), slot.get("effort"))
                    tid, _ = self._draft_thread(db, source["project_id"], eid, aid, "story", goal, dict(plan, create=slot))
                    for source_task in source_tasks:
                        db.execute("INSERT OR IGNORE INTO task_pack SELECT ?,pack_id FROM task_pack WHERE task_id=?", (tid,source_task))
                        citations = db.execute("SELECT knowledge_id,reason FROM citation WHERE task_id=?",(source_task,)).fetchall()
                        for citation in citations:
                            db.execute("INSERT OR IGNORE INTO citation(id,task_id,knowledge_id,reason,created_at) VALUES(?,?,?,?,?)",
                                       (new_id(),tid,citation["knowledge_id"],citation["reason"],self.clock()))
                    if side == "A":
                        rid = self._queue_run(db,tid,"revise",version_id,goal,extra_header={"storyComparisonSource":version_id})
                        header, entries = self._snapshot_parts(_row(db,"SELECT * FROM run WHERE id=?",rid))
                    else:
                        header.update(taskId=tid,provider=slot["provider"],requestedModel=slot.get("model"),
                                      requestedEffort=slot.get("effort"))
                        rid = self._enqueue_snapshot(db,header,entries,False)
                    db.execute("INSERT INTO story_candidate VALUES(?,?,?,?)", (version_id, next_round, side, rid))
                    ids.append(rid)
                return dict(runIds=ids, sourceVersionId=version_id, roundNo=next_round)
            return self._command(db, command_id, "story_comparison", dict(source=version_id,after=after_round,goal=goal), start)

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

    def post_message(self, task_id, text, command_id, selection=None, base_version_id=None, create_executor=None, files=(), workspace_revision=None):
        """A user message asks for a revision. It never resets the task's round counter."""
        if not text.strip():
            raise Rejected("消息为空")
        payload = dict(task_id=task_id, text=text, selection=selection, base=base_version_id)
        extra, refs = attachments.prepare(files)
        if refs:
            payload["attachments"] = refs
        if workspace_revision is not None:
            payload["workspaceRevision"] = workspace_revision
        if create_executor is not None:
            self.validate_selection(create_executor.get("provider"), create_executor.get("model"), create_executor.get("effort"))
            payload["create_executor"] = create_executor
        with transaction(self.db_path) as db:
            def run():
                task = _row(db, "SELECT * FROM task WHERE id=?", task_id, missing="任务不存在")
                if task["state"] in ("closed", "abandoned"):
                    raise Rejected("任务已结束")
                if create_executor is not None:
                    plan = json.loads(task["executor_plan"])
                    plan["create"] = self.checked_connection(create_executor)
                    db.execute("UPDATE task SET executor_plan=? WHERE id=?", (dumps(plan), task_id))
                thread = db.execute("SELECT id FROM thread WHERE task_id=?", (task_id,)).fetchone()[0]
                base = base_version_id or self._latest_version(db, task["artifact_id"])
                if base:
                    self._version_of(db, base, task["artifact_id"])
                run_id = self._queue_run(db, task_id, "revise" if base else "create", base, text, selection, message_entries=extra, workspace_revision=workspace_revision)
                mid = self._message(db, thread, "user", text, selection, None, run_id, payload={"attachments":refs} if refs else None)
                return dict(messageId=mid, runId=run_id, usedRounds=task["used_rounds"])
            return self._command(db, command_id, "post_message", payload, run)

    def _version_of(self, db, version_id, artifact_id):
        v = _row(db, "SELECT * FROM version WHERE id=?", version_id, missing="版本不存在")
        if v["artifact_id"] != artifact_id:
            raise Rejected("版本不属于该成果")
        return v

    def _episode_method(self, db, episode_id):
        record = _row(db, "SELECT p.config_id,k.method_pack FROM episode e JOIN project p ON p.id=e.project_id "
                      "LEFT JOIN kickoff_method k ON k.episode_id=e.id WHERE e.id=?", episode_id)
        return load_pack(record["method_pack"] or "short-film@v1"), record["config_id"]

    def stage_methods(self, episode_id, stage):
        with connect(self.db_path) as db:
            pack, profile = self._episode_method(db, episode_id)
            sections = ([*method_entries(pack, "cold_read"), *method_entries(pack, "comparison", profile)]
                        if stage == "review" else method_entries(pack, stage, profile))
            disabled = self.get_setting(f"method-options:{episode_id}:{stage}", [])
            guides = method_entries(pack,"h3_guides") if stage in {"prompts","review"} else []
            return dict(ref=pack["id"], sections=sections, disabled=disabled, **({"guides":guides} if guides else {}))

    def set_stage_methods(self, episode_id, stage, disabled):
        allowed_stages = {"story","script","plan","prompts","publish"}
        if stage not in allowed_stages or not isinstance(disabled,list) or not all(isinstance(n,str) for n in disabled):
            raise Rejected("只可选择本阶段的附加方法")
        contents = self.stage_methods(episode_id,stage)
        optional = {e["name"] for e in contents["sections"] if e["methodSource"]["path"].startswith("sections/")}
        if set(disabled) - optional:
            raise Rejected("所选小节不存在，或属于必用的项目约束与结构要求")
        self.put_setting(f"method-options:{episode_id}:{stage}",sorted(set(disabled)))
        return self.stage_methods(episode_id,stage)

    def _queue_run(self, db, task_id, purpose, base_version_id, request, selection=None, counted=False,
                   target_version_id=None, provider=None, model=None, effort=None, entries=None, extra_header=None, message_entries=(), workspace_revision=None):
        task = _row(db, "SELECT * FROM task WHERE id=?", task_id)
        plan = json.loads(task["executor_plan"])
        self._check_editor_guard(db,task["artifact_id"])
        role = plan["review"] if purpose in REVIEW_PASSES else plan["create"]
        provider = provider or role["provider"]
        if provider not in self.executors:
            raise Rejected(f"未配置的执行器：{provider}")
        if role["provider"] == provider:
            if model is None:
                model = role.get("model")
            if effort is None and model == role.get("model"):
                effort = role.get("effort")
        connection = self.run_connection(provider,model,effort)
        model, effort = connection["model"], connection["effort"]
        kickoff = self._kickoff(db, task["episode_id"])
        pack, profile = self._episode_method(db, task["episode_id"])
        settings_text = prompts.render_settings(kickoff["settings_snapshot"])
        kickoff_text = prompts.render_kickoff(kickoff, self._source_text(db, kickoff))
        if entries is not None:
            entries = method_entries(pack, purpose, None if purpose == "cold_read" else profile) + entries
        elif purpose == "review":
            version = self._version_of(db, target_version_id, task["artifact_id"])
            entries = prompts.review_inputs(pack, settings_text, kickoff_text,
                                            render_stage(task["stage"], json.loads(version["content"])), profile)
            entries += self._extra_review_inputs(db, task, version)
        else:
            base_content = json.loads(self._version_of(db, base_version_id, task["artifact_id"])["content"]) \
                if base_version_id else None
            adopted_story = self._upstream_text(db, task)
            req_text = request if not selection else f"{request}\n（选区：{json.dumps(selection, ensure_ascii=False)}）"
            entries = self._writing_inputs(db,task,pack,profile,settings_text,kickoff_text,base_content,req_text,adopted_story)
            if task["stage"] == "story" and base_version_id:
                base = self._version_of(db,base_version_id,task["artifact_id"])
                prior = self._version_materials(db,base,{"reference_pack","adaptation_source"})
                # The same pack may have been annotated since this version was written.
                # Revisions and both candidates must read the source the story actually used.
                frozen_names = {(e["role"],e["name"]) for e in prior}
                entries = [e for e in entries if (e["role"],e["name"]) not in frozen_names]
                entries.extend(dict(role=e["role"],name=e["name"],text=e["text"]) for e in prior)
        if purpose in ("create", "revise", "parallel"):
            mode = self._cooperation_mode(db, task["episode_id"])
            entries.append(dict(role="cooperation", name="本集合作方式",
                                text=COOPERATION[mode] + " 新稿另存，由导演采用。"))
        entries.extend(message_entries)
        header = dict(schemaVersion=1, purpose=purpose, projectId=task["project_id"], episodeId=task["episode_id"],
                      taskId=task_id, artifactId=task["artifact_id"], stage=task["stage"], provider=provider, executorConnection=connection,
                      requestedModel=model, requestedEffort=effort, methodPack=dict(id=pack["id"], files={e["name"]: e["methodSource"] for e in entries if "methodSource" in e}),
                      settingsRevision=kickoff["settings_revision"], baseVersionId=base_version_id,
                      reviewedVersionId=target_version_id,
                      sessionMode="ephemeral" if purpose in REVIEW_PASSES else "new", allowClarify=bool(selection),
                      isolationLevel="session" if purpose in REVIEW_PASSES else None, **(extra_header or {}))
        if workspace_revision is not None:
            art = _row(db, "SELECT * FROM artifact WHERE id=?", task["artifact_id"])
            if type(workspace_revision) is not int or art["revision"] != workspace_revision:
                raise Conflict("阶段状态已经变化，请刷新后再发送")
            if self._edit_state(db, art, mode) == "protected":
                header["stageDialogue"] = dict(revision=workspace_revision, request=request)
                turns = db.execute("SELECT m.role,m.text FROM message m JOIN thread h ON h.id=m.thread_id "
                                   "WHERE h.task_id=? AND m.kind='text' AND m.role IN ('user','assistant') ORDER BY m.seq",
                                   (task_id,)).fetchall()
                if any(m["role"]=="assistant" for m in turns):
                    entries.append(dict(role="conversation",name="此前的阶段讨论",text=dumps([dict(m) for m in turns])))
        return self._enqueue_snapshot(db, header, entries, counted)

    def _enqueue_snapshot(self, db, header, entries, counted):
        provider = header["provider"]
        if provider not in self.executors:
            raise Rejected(f"未配置的执行器：{provider}")
        if any("data" in e for e in entries) and not getattr(self.executors[provider], "reads_images", False):
            raise Rejected(f"{provider} 当前配置不支持本次原图输入")
        if header["sessionMode"] == "ephemeral" and self._isolation_status(provider) != "session":
            raise Rejected(f"{provider} 的会话隔离未验证，必需审核等待")
        run_id = new_id()
        fingerprint, rel = self.store.freeze(run_id, header, entries)
        db.execute("INSERT INTO run(id,task_id,purpose,counted_round,provider,requested_model,requested_effort,"
                   "session_mode,isolation_level,base_version_id,target_version_id,input_hash,manifest_relpath,"
                   "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, header["taskId"], header["purpose"], int(counted), provider, header["requestedModel"],
                    header["requestedEffort"], header["sessionMode"], header["isolationLevel"], header["baseVersionId"],
                    header["reviewedVersionId"], fingerprint, rel, self.clock()))
        self._set_task_run_state(db,header["taskId"],"queued")

        return run_id

    def retry_run(self, run_id, provider=None, model=None, effort=None, command_id=None):
        """Explicit retry (e.g. after waiting for quota or switching executor). Never automatic."""
        payload = dict(run_id=run_id, provider=provider, model=model, effort=effort)
        with transaction(self.db_path) as db:
            def run():
                old = _row(db, "SELECT * FROM run WHERE id=?", run_id, missing="运行不存在")
                if old["status"] not in ("failed", "waiting_quota", "stopped"):
                    raise Rejected("只有失败、等待额度或已停的运行可以重试")
                header, entries = self._snapshot_parts(old)
                if old["purpose"] in ("cold_read","comparison"):
                    pending = self._active_prompt_reviews(db,header["artifactId"])
                    if (header["scope"],old["purpose"],header["scopeHash"]) in pending:
                        raise Conflict("该段相同输入的审查已经排队或正在执行")
                if header.get("storyComparisonSource") and not db.execute(
                        "SELECT 1 FROM story_candidate WHERE run_id=?", (run_id,)).fetchone():
                    raise Conflict("这份候选稿已经发起重试")
                target = provider or old["provider"]
                if old["status"] == "waiting_quota" and target != old["provider"] and self.get_setting("quota_retry_policy") == "same_cli":
                    raise Rejected("限流策略只允许使用原执行器重试，可在我的模型服务中调整")
                if model is None and target == old["provider"]:
                    model_for_retry = old["requested_model"]
                else:
                    model_for_retry = model
                effort_for_retry = effort
                if effort is None and target == old["provider"] and model_for_retry == old["requested_model"]:
                    effort_for_retry = old["requested_effort"]
                if provider is not None or model is not None or effort is not None:
                    self.validate_selection(target,model_for_retry,effort_for_retry)
                if target == old["provider"]:
                    original = header.get("executorConnection",dict(proxy="system"))
                    connection = dict(original,model=model_for_retry,effort=effort_for_retry)
                else:
                    connection = self.run_connection(target,model_for_retry,effort_for_retry)
                header.update(provider=target,requestedModel=connection["model"],requestedEffort=connection["effort"],
                              executorConnection=connection)
                new_run = self._enqueue_snapshot(db, header, entries, old["counted_round"])
                if header.get("storyComparisonSource"):
                    db.execute("UPDATE story_candidate SET run_id=? WHERE run_id=?", (new_run,run_id))
                if old["purpose"] == "proposal":
                    changed = db.execute("UPDATE closure SET run_id=?,state='proposing' WHERE run_id=?",
                                         (new_run, run_id)).rowcount
                    if not changed:
                        raise Conflict("收尾提议已有更新的运行")
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
        try:
            if run["purpose"] == "proposal":
                with connect(self.db_path) as db:
                    closure = _row(db, "SELECT id FROM closure WHERE run_id=?", run_id)
                self._run_proposal(run_id, closure["id"])
                return []
            return self._execute_claimed(run, task)
        except Exception as exc:
            with transaction(self.db_path) as db:
                if _row(db,"SELECT status FROM run WHERE id=?",run_id)["status"] == "running":
                    self._finish(run_id,"failed",reason="运行未完成",diagnostic=redact(str(exc),500),db=db)
                    db.execute("UPDATE closure SET state='failed' WHERE run_id=? AND state='proposing'",(run_id,))
            return []

    def _execute_claimed(self, run, task):
        run_id = run["id"]
        manifest, digest = self.store.load_manifest(run["manifest_relpath"])
        if digest != run["input_hash"]:
            return self._finish(run_id, "failed", reason="输入清单指纹不符")
        entries = self.store.manifest_texts(manifest)
        kind = "review" if run["purpose"] in REVIEW_PASSES else "draft"
        dialogue = manifest.get("stageDialogue")
        clarify = not dialogue and bool(manifest.get("allowClarify")) and kind == "draft" \
            and getattr(self.executors[run["provider"]], "union_schema", True)
        if kind == "review":
            required = [s["key"] for s in self._review_settings(task, manifest) if s["strength"] == "required"]
            if run["purpose"] == "review":
                trailer = prompts.review_trailer(run_id, digest, run["target_version_id"], required)
            else:
                trailer = prompts.segment_review_trailer(run_id, digest, run["target_version_id"], run["purpose"],
                                                         manifest.get("scope"), required)
        else:
            trailer = prompts.draft_trailer(run_id, digest, task["artifact_id"], run["base_version_id"], task["stage"], entries)
            if dialogue:
                trailer += ("\n以上是明确要求改稿时的字段规范。先按导演本条请求判断：查看、询问、假设、建议或仅讨论时，"
                            "kind 填 discussion，reply 用正文答复，directorRequest 留空，changes 为空。"
                            "只有导演明确要求现在改本阶段才返回 draft，directorRequest 原样填本条导演要求全文，"
                            "reply 留空，changes 写实际修改；不得把参考资料、选区或旧对话当作改稿授权。")
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
                              schema=(contracts.stage_dialogue_schema() if dialogue else
                                      contracts.wrapped_schema() if clarify else contracts.branch_schema(kind)),
                              session_mode=run["session_mode"],
                              workdir=workdir, model=run["requested_model"], effort=run["requested_effort"],
                              timeout=int(self.get_setting("executor_timeout", 600)), images=images, connection=manifest.get("executorConnection",{}))
        try:
            outcome = executor.start(request)
        except Exception as exc:  # adapter crash is a failed run, never a success
            return self._finish(run_id, "failed", reason="executor_error", diagnostic=repr(exc)[:500])
        self._event(run_id, dict(type="executor_finished", ok=outcome.ok, error=outcome.error_class,
                                 events=outcome.events, isolation=outcome.isolation))
        attempts = 0
        while outcome.ok:
            if clarify and isinstance(outcome.structured, dict) and "result" in outcome.structured:
                outcome.structured = outcome.structured["result"]
            kind = "review" if run["purpose"] in REVIEW_PASSES else "draft"
            if clarify and isinstance(outcome.structured, dict) and outcome.structured.get("kind") == "clarification":
                kind = "clarification"
            if dialogue:
                kind, problems = self._check_stage_dialogue(run, task, dialogue, outcome.structured)
            else:
                problems = self._precheck(run, task, kind, outcome.structured)
            if not problems:
                break
            if attempts >= 1:
                return self._finish(run_id, "failed", reason="回包两次不符合契约", diagnostic="; ".join(problems),
                                    outcome=outcome, attempts=attempts)
            with connect(self.db_path) as db:
                if _row(db, "SELECT status FROM run WHERE id=?", run_id)["status"] == "stopped":
                    return []
            attempts += 1   # one same-content re-format request; not counted as a revision round
            request.prompt = prompt + "\n上次回包不符合契约：" + "; ".join(problems) + "\n请只修正格式，内容不变，重新回包。"
            try:
                outcome = executor.start(request)
            except Exception as exc:
                return self._finish(run_id,"failed",reason="执行器修正回包时中断",diagnostic=redact(str(exc),500),attempts=attempts)
        if not outcome.ok:
            status = "waiting_quota" if outcome.error_class == "quota" else "failed"
            return self._finish(run_id, status, reason=outcome.error_class, diagnostic=outcome.diagnostic,
                                outcome=outcome, attempts=attempts)
        (self.store.run_dir(run_id) / "result.json").write_text(json.dumps(outcome.structured, ensure_ascii=False, indent=2))
        try:
            return self._ingest(run_id, kind, outcome, attempts, dialogue)
        except (Rejected, ValueError, TypeError) as exc:
            return self._finish(run_id, "failed", reason="回包无法入库", diagnostic=str(exc), outcome=outcome, attempts=attempts)

    def _check_stage_dialogue(self, run, task, dialogue, result):
        problems = [e.message for e in contracts.Draft202012Validator(contracts.stage_dialogue_schema()).iter_errors(result)]
        if problems:
            return "discussion", problems[:5]
        kind = result["kind"]
        if result["runId"] != run["id"] or result["inputFingerprint"] != run["input_hash"] or result["baseVersionId"] != run["base_version_id"]:
            return kind, ["回复与本次请求或阅读版本不符"]
        if kind == "discussion":
            if result["changes"] or result["directorRequest"] or not result["reply"].strip():
                return kind, ["讨论只填写回复正文"]
            return kind, []
        if result["directorRequest"] != dialogue["request"] or not result["changes"]:
            return kind, ["改稿须给出当前导演明确的要求原文与实际修改"]
        draft = {key:value for key,value in result.items() if key not in ("reply","directorRequest")}
        return kind, self._precheck(run, task, kind, draft)

    def _review_settings(self, task, manifest):
        if manifest["purpose"] == "cold_read":
            return []
        with connect(self.db_path) as db:
            return self._settings(db, task["project_id"], manifest["settingsRevision"])[1]

    def _event(self, run_id, payload):
        with transaction(self.db_path) as db:
            seq = db.execute("SELECT coalesce(max(seq),0)+1 FROM event WHERE run_id=?", (run_id,)).fetchone()[0]
            db.execute("INSERT INTO event(run_id,seq,payload) VALUES(?,?,?)", (run_id, seq, dumps(payload)))

    def _set_task_run_state(self, db, task_id, fallback, only_active=False):
        # Multiple segment reviews can run under one task. Keep active siblings visible.
        pending = db.execute("SELECT status FROM run WHERE task_id=? AND status IN ('running','queued') "
                             "ORDER BY (status='running') DESC LIMIT 1",(task_id,)).fetchone()
        sql = "UPDATE task SET state=? WHERE id=?"
        if only_active:
            sql += " AND state IN ('running','queued')"
        db.execute(sql,(pending[0] if pending else fallback,task_id))

    def _finish(self, run_id, status, reason=None, diagnostic=None, outcome=None, attempts=0, result_hash=None, db=None):
        def apply(db):
            fields = dict(status=status, reason=reason, diagnostic=diagnostic, ended_at=self.clock(),
                          format_attempts=attempts, result_hash=result_hash)
            if outcome:
                fields.update(session_id=outcome.session_id, reported_model=outcome.reported_model,
                              reported_effort=outcome.reported_effort, quota_resets_at=outcome.quota_resets_at,
                              executable=outcome.executable, executor_version=outcome.version)
            changed = db.execute("UPDATE run SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=? AND status<>'stopped'",
                                 (*fields.values(), run_id))
            if not changed.rowcount:
                return
            task_id = db.execute("SELECT task_id FROM run WHERE id=?", (run_id,)).fetchone()[0]
            state = {"waiting_quota": "waiting_quota", "failed": "failed"}.get(status, "awaiting_decision")
            self._set_task_run_state(db,task_id,state,only_active=True)
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
            if bad:
                return [f"越界修改：{', '.join(bad)}"]
            try:
                for change in result["changes"]:
                    normalize(task["stage"], change["field"], change["value"])
            except (ValueError, TypeError) as exc:
                return [f"{change['field']} 的值不能按阶段契约解析：{exc}"]
            if task["stage"] == "story":
                manifest,_ = self.store.load_manifest(run["manifest_relpath"])
                sources = [json.loads(self.store.read_blob(e["sha256"])) for e in manifest["inputs"]
                           if e["role"] == "adaptation_source"]
                if sources:
                    value = next((normalize("story","adaptation",c["value"]) for c in result["changes"]
                                  if c["field"]=="adaptation"),None)
                    if "adaptation" not in fields and run["base_version_id"]:
                        with connect(self.db_path) as db:
                            value = json.loads(self._version_of(db,run["base_version_id"],task["artifact_id"])["content"]).get("adaptation")
                    errors = adaptation_errors(value,sources)
                    if errors:
                        return errors
            if task["stage"] == "prompts":
                with connect(self.db_path) as db:
                    try:
                        value = normalize("prompts", "segments", result["changes"][0]["value"])
                        errors = self._prompt_reference_errors(db, task["episode_id"], value, require_observations=False)
                        if errors:
                            return errors
                        manifest, _ = self.store.load_manifest(run["manifest_relpath"])
                        scaffold = next((e for e in manifest["inputs"] if e["role"] == "prompt_scaffold"), None)
                        if scaffold:
                            bound = json.loads(self.store.read_blob(scaffold["sha256"]))
                            if value.keys() != bound.keys():
                                return ["提示词段号须与本次冻结的方案一致"]
                            from . import readable
                            timing = next(i for i in manifest["inputs"] if i["role"] == "plan_layout")
                            frozen_plan = json.loads(self.store.read_blob(timing["sha256"]))
                            frozen_layout = frozen_plan["layout"]["segments"]
                            layout = {s["id"]:s for s in frozen_layout}
                            for sid, segment in bound.items():
                                if value[sid].get("prompt_format") != "readable_v3":
                                    errors.append(f"{sid} 须声明 prompt_format=readable_v3")
                                else:
                                    try:
                                        readable.validate(value[sid], layout[sid]["strategy"], layout[sid]["duration_ms"])
                                    except (ValueError, TypeError, KeyError) as exc:
                                        errors.append(f"{sid}：{exc}")
                                ids = [r["assetVersionId"] for r in segment["refs"]]
                                if [r["assetVersionId"] for r in value[sid]["refs"]] != ids:
                                    errors.append(f"{sid} 的参考版本和顺序须与本次绑定一致")
                                elif any(r.get("shots") != fresh.get("shots") for r, fresh in zip(segment["refs"], value[sid]["refs"])):
                                    errors.append(f"{sid} 的作用镜头须与选图时冻结的镜号一致")
                            if not errors:
                                from . import rhythm
                                plan = dict(segments=[dict(s,order=i) for i,s in enumerate(frozen_layout)],
                                            shots=frozen_plan["shots"],rhythmTable=frozen_plan.get("rhythmTable",""))
                                try:
                                    rhythm.check(plan,value)
                                except (KeyError,ValueError,TypeError) as exc:
                                    errors.append(str(exc))
                        return errors
                    except (ValueError, TypeError, IndexError):
                        return ["segments 须为有效 JSON 对象"]
            return []
        manifest, _ = self.store.load_manifest(run["manifest_relpath"])
        snapshot = self._review_settings(task, manifest)
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
        if expected_pass in ("cold_read", "comparison"):
            locations = [*result["observations"], *(issue["location"] for issue in result["issues"])]
            if any(loc["versionId"] != vid or loc["objectId"] != manifest.get("scope")
                   or loc["unit"] not in ("segment", "grid_cell") for loc in locations):
                problems.append("分段审查观察与意见须对应本次送审段及版本")
        covered = {o["objectId"] for o in result["observations"]}
        if expected_pass == "story" and (missing := sorted(required - covered)):
            problems.append("未逐条核对必守设定：" + "、".join(missing))
        if result["decision"] == "pass" and result["issues"]:
            problems.append("pass 不能带未解决问题")
        if result["decision"] != "pass" and not result["issues"]:
            problems.append("return/uncertain 必须有对应意见")
        return problems

    def _ingest(self, run_id, kind, outcome, attempts, dialogue=None):
        result = outcome.structured
        body = canonical(result)
        follow = []
        with transaction(self.db_path) as db:
            run = _row(db, "SELECT * FROM run WHERE id=?", run_id)
            if run["status"] == "stopped":
                return []
            task = _row(db, "SELECT * FROM task WHERE id=?", run["task_id"])
            if kind == "discussion":
                thread = _row(db, "SELECT id FROM thread WHERE task_id=?", task["id"])
                self._message(db,thread["id"],"assistant",result["reply"],None,None,run_id)
                self._finish(run_id,"succeeded",outcome=outcome,attempts=attempts,result_hash=sha256_bytes(body),db=db)
            elif kind == "clarification":
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
                if dialogue:
                    art = _row(db, "SELECT * FROM artifact WHERE id=?", task["artifact_id"])
                    if art["revision"] == dialogue["revision"]:
                        self._change_edit_state(db, art["id"], True, art["revision"])
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
                snapshot = {s["key"]: s for s in self._review_settings(task, manifest)}
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
                              f"{len(result['issues'])} 条意见", None, None, run_id, kind="review")
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

    def _new_version(self, db, artifact_id, base_id, origin, content, run_id=None, note="", baseline_from=None):
        self._check_editor_guard(db, artifact_id)
        if "segments" in content and isinstance(content["segments"], dict):
            from . import readable
            for entry in content["segments"].values():
                if isinstance(entry, dict) and entry.get("prompt_format") == "readable_v3":
                    try:
                        text = entry.get("txt", "")
                        entry["blocks"] = readable.sections(text, "grid" if text.lstrip().startswith(readable.GRID[0]) else "flex")
                    except (ValueError, AttributeError, TypeError):
                        entry.pop("blocks", None)  # A malformed manual draft remains editable and fails adoption.
        no = db.execute("SELECT coalesce(max(display_no),0)+1 FROM version WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
        vid = new_id()
        blob = self.store.put_blob(canonical(content))
        db.execute("INSERT INTO version VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (vid, artifact_id, no, base_id, origin, run_id, dumps(content), blob, note, self.clock()))
        art = _row(db, "SELECT stage,project_id,episode_id FROM artifact WHERE id=?", artifact_id)
        if art["stage"] == "prompts":
            self._record_prompt_sources(db,vid,art["episode_id"],run_id,baseline_from)
        if art["stage"] == "publish":
            cfg = self.publish_settings(art["project_id"])
            basis = self._publish_basis(db, art["episode_id"])
            if run_id:
                path = _row(db,"SELECT manifest_relpath FROM run WHERE id=?",run_id)["manifest_relpath"]
                manifest,_ = self.store.load_manifest(path)
                entry = next((e for e in manifest["inputs"] if e["role"]=="publish_settings"), None)
                if entry:
                    from .publishing import settings
                    cfg = settings(json.loads(self.store.read_blob(entry["sha256"])))
                sent = next((e for e in manifest["inputs"] if e["role"]=="publish_basis"), None)
                if sent:
                    basis = json.loads(self.store.read_blob(sent["sha256"]))
            db.execute("INSERT INTO version_publish_config VALUES(?,?)", (vid, dumps(cfg)))
            db.execute("INSERT INTO version_publish_basis VALUES(?,?)", (vid, dumps(basis)))
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
                merged = dict(base, **content)
                if art["stage"] == "plan" and "rhythmTable" in content and "shots" not in content:
                    merged = shotrefs.apply_table_edit(merged)
                vid = self._new_version(db, artifact_id, base_version_id, origin, merged, note=note)
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
                    creation = self._version_creation_runs(db,version)
                    if creation:
                        tid = creation[0]["task_id"]
                    else:
                        row = db.execute("SELECT id FROM task WHERE artifact_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                         (art["id"],)).fetchone()
                        if not row:
                            raise Rejected("请先在本集建立故事任务")
                        tid = row[0]
                run_id = self._queue_run(db, tid, "review", None, "", target_version_id=version_id,
                                         provider=provider, model=model, effort=effort)
                if art["stage"] == "story" and not self.adoption_gate(version_id,db)["formatErrors"]:
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

    def _prompt_reference_errors(self, db, episode_id, segments, *, require_observations=True):
        if not isinstance(segments, dict):
            return ["segments 须以本集段号为键"]
        errors = []
        readable_segments = any(isinstance(e,dict) and e.get("prompt_format") == "readable_v3" for e in segments.values())
        standards = {e["role"]:json.loads(e["text"]) for e in self._source_entries(db, episode_id, "prompts")
                     if e["role"] in ("characterStandard", "promptStandard")} if readable_segments else {}
        plan = self._adopted_plan(db, episode_id)
        if plan:
            errors.extend(acting.prompt_errors(plan, segments))
        strategies = {s["id"]:s["strategy"] for s in (plan or {}).get("segments", [])}
        kickoff = db.execute("SELECT spec FROM kickoff WHERE episode_id=?", (episode_id,)).fetchone()
        aspect = json.loads(kickoff[0]).get("aspectRatio") if kickoff else None
        for sid, entry in segments.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("refs"), list):
                errors.append(f"{sid} 的 refs 须为有序数组")
                continue
            if not isinstance(entry.get("sound", {}), dict):
                errors.append(f"{sid} 的 sound 须为对象，含 bgm、ambience、dialogue 字段")
            if isinstance(entry.get("txt"), str):
                errors.extend(self._cast_prompt_errors(db, episode_id, sid, entry))
            if "generation_settings" in entry:
                settings = entry["generation_settings"]
                if not isinstance(settings, dict):
                    errors.append(f"{sid} 的生成设置须为对象")
                else:
                    try:
                        check_generation_aspect(settings, aspect)
                    except ValueError as exc:
                        errors.append(f"{sid}：{exc}")
            if require_observations and (shotrefs.plan_enabled(plan) or any(isinstance(r,dict) and r.get("visualReviewId") for r in entry["refs"])):
                try:
                    packet = shotrefs.segment_packet(self,db,episode_id,plan,self._cast_matrix(db, episode_id),sid,entry)
                    shotrefs.check_shot_references(packet["files"].__getitem__,packet["manifest"])
                except (ValueError, TypeError, KeyError, NotFound, Rejected) as exc:
                    errors.append(f"{sid}：{exc}")
            labels_by_hash = {}
            for ref in entry["refs"]:
                if not isinstance(ref, dict):
                    errors.append(f"{sid} 的参考项须为对象")
                    continue
                vid = ref.get("assetVersionId")
                asset = db.execute("SELECT a.kind,v.grid,v.sha256 FROM asset_version v JOIN asset a ON a.id=v.asset_id "
                                   "JOIN episode e ON e.id=a.episode_id AND e.project_id=a.project_id "
                                   "WHERE v.id=? AND e.id=?", (vid, episode_id)).fetchone() if isinstance(vid, str) else None
                if asset is None:
                    errors.append(f"{sid} 素材版本 {vid} 不在本集，请从 assets.json 精确复制 assetVersionId")
                elif asset["kind"] != ref.get("kind") or ("grid" in ref and bool(asset["grid"]) != bool(ref["grid"])):
                    errors.append(f"{sid} 素材类型或九宫格标记与登记版本不符")
                elif asset["sha256"] in labels_by_hash:
                    errors.append(f"{sid} 同段不能重复引用字节相同的素材")
                else:
                    labels_by_hash[asset["sha256"]] = ref.get("label")
                    if strategies.get(sid) == "grid" and asset["grid"]:
                        try:
                            raw = self.store.read_blob(asset["sha256"])
                            grid_dimensions(raw, aspect)
                        except (ValueError,TypeError,OSError) as exc:
                            errors.append(f"{sid} 无法核对九宫格原图：{exc}")
            if standards and entry.get("prompt_format") == "readable_v3" and isinstance(entry.get("txt"), str):
                from . import readable
                present = {r[0].casefold() for r in db.execute("SELECT role FROM cast_cell WHERE episode_id=? AND segment_id=? AND status='ok'",
                                                                (episode_id,sid))}
                try:
                    readable.check_project_blocks(entry, strategies.get(sid,"flex"), standards.get("promptStandard",{}).get("blocks",{}),
                                                  standards.get("characterStandard",{}), present, labels_by_hash)
                except ValueError as exc:
                    errors.append(f"{sid}：{exc}")
        return errors

    def adoption_gate(self, version_id, db=None):
        def compute(db):
            version = _row(db, "SELECT * FROM version WHERE id=?", version_id, missing="版本不存在")
            art = _row(db, "SELECT * FROM artifact WHERE id=?", version["artifact_id"])
            content = json.loads(version["content"])
            gate = dict(versionId=version_id, formatErrors=check_content(art["stage"], content,
                                                                         self._adopted_plan(db, art["episode_id"])),
                        review=None,
                        blocking=[], unresolved=[], reviewRequired=art["stage"] == "story")
            if art["stage"] == "plan" and not gate["formatErrors"] and any(s.get("dialogueCues") for s in content["shots"]):
                production = json.loads(_row(db, "SELECT production FROM project WHERE id=?", art["project_id"])["production"])
                silent = {name.casefold() for name in production.get("silentRoles", [])}
                for shot in content["shots"]:
                    for cue in shot.get("dialogueCues", []):
                        if cue["speaker"].strip().casefold() in silent:
                            gate["formatErrors"].append(f"{shot['id']} · {cue['speaker']} 是项目设定中的无对白角色")
            if art["stage"] == "publish" and not gate["formatErrors"]:
                try:
                    self.compose_publish(art["episode_id"],version_id)
                except Rejected as exc:
                    gate["formatErrors"].append(str(exc))
            if art["stage"] == "prompts":
                gate["formatErrors"].extend(self._prompt_reference_errors(db, art["episode_id"], content.get("segments")))
            if art["stage"] == "story":
                if db.execute("SELECT 1 FROM task_pack p JOIN task t ON t.id=p.task_id WHERE t.artifact_id=? LIMIT 1",
                              (art["id"],)).fetchone():
                    sources = [json.loads(e["text"]) for e in self._version_materials(db,version,{"adaptation_source"})]
                    gate["formatErrors"].extend(adaptation_errors(content.get("adaptation"),sources))
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
        return self._adopt(artifact_id, version_id, expected_adopted_version_id, command_id, and_continue)

    def _adopt(self, artifact_id, version_id, expected_adopted_version_id, command_id, and_continue, expert=None):
        payload = dict(artifact_id=artifact_id, version_id=version_id, expected=expected_adopted_version_id,
                       cont=and_continue)
        with transaction(self.db_path) as db:
            def run():
                art = _row(db, "SELECT * FROM artifact WHERE id=?", artifact_id, missing="成果不存在")
                if expert:
                    self._expert_guard(db, expert, art["episode_id"])
                    if self._latest_version(db, artifact_id) != version_id:
                        raise Conflict("草稿已更新，请核对新版本")
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
        if not prev:
            return None
        version = db.execute(
            "SELECT v.id, v.content, a.id AS artifact_id FROM artifact a JOIN version v ON v.id=a.adopted_version_id "
            "WHERE a.episode_id=? AND a.stage=?", (task["episode_id"], prev)).fetchone()
        if not version:
            return None
        adoption = db.execute(
            "SELECT d.id, r.id AS review_id, r.original_decision FROM adoption d LEFT JOIN review r ON r.id=d.review_id "
            "WHERE d.artifact_id=? AND d.version_id=? ORDER BY d.rowid DESC LIMIT 1",
            (version["artifact_id"], version["id"])).fetchone()
        status = [f"工作台采用版本：{version['id']}"]
        if adoption:
            status.append(f"采用记录：{adoption['id']}")
            if adoption["review_id"]:
                status.append(f"采用时关联的独审记录：{adoption['review_id']}；审查原结论：{adoption['original_decision']}。")
            else:
                status.append("此次采用记录没有关联独审。")
        else:
            status.append("历史版本缺少对应采用记录，独审状态未知。")
        status.append("正文保留作者起草时的原话。引用当前采用与独审状态时，以以上工作台记录为准。")
        return "\n".join(status) + "\n\n" + render_stage(prev, json.loads(version["content"]))

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
    def story_markdown(self, version_id):
        with connect(self.db_path) as db:
            version = _row(db, "SELECT v.content FROM version v JOIN artifact a ON a.id=v.artifact_id "
                           "WHERE v.id=? AND a.stage='story'", version_id, missing="故事版本不存在")
        return render_story(json.loads(version["content"]))

    def episode_view(self, episode_id):
        with connect(self.db_path) as db:
            ep = _loads(self._episode_in(db, episode_id), "leftover_facts")
            kick = db.execute("SELECT * FROM kickoff WHERE episode_id=?", (episode_id,)).fetchone()
            view = dict(episode=ep, kickoff=_loads(dict(kick), "predecessor_ids", "carried_facts", "settings_snapshot",
                                                   "spec", "executor_plan") if kick else None, artifacts=[])
            view["cooperationMode"] = self._cooperation_mode(db, episode_id)
            view["skippedStages"] = dict(db.execute(
                "SELECT stage, reason FROM optional_stage_choice WHERE episode_id=? AND reason<>''", (episode_id,)))
            view["sourceFiles"] = [dict(r) for r in db.execute("SELECT kind,relpath,sha256 FROM episode_source WHERE episode_id=? ORDER BY kind", (episode_id,))]
            if kick and kick["source_import_id"]:
                view["sourceImport"] = dict(db.execute(
                    "SELECT id,original_name,sha256,purpose,parsed_text FROM import_record WHERE id=?",
                    (kick["source_import_id"],)).fetchone())
            manuscript = self._episode_manuscript(db, episode_id)
            if manuscript:
                view["storyManuscript"] = dict(manuscript)
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
                art["editState"] = self._edit_state(db, art, view["cooperationMode"])
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
                if art["stage"] == "story":
                    art["comparisons"] = [dict(c) for c in db.execute(
                        "SELECT c.source_version_id,c.round_no,c.side,c.run_id,t.goal,r.status,r.reason,r.provider,r.requested_model,r.requested_effort,"
                        "r.reported_model,r.reported_effort,v.id AS version_id FROM story_candidate c "
                        "JOIN version s ON s.id=c.source_version_id JOIN run r ON r.id=c.run_id JOIN task t ON t.id=r.task_id "
                        "LEFT JOIN version v ON v.source_run_id=r.id WHERE s.artifact_id=? ORDER BY s.display_no DESC,c.round_no DESC,c.side",
                        (art["id"],))]
                view["artifacts"].append(art)
            spaces = [r[0] for r in db.execute("SELECT status FROM space_item WHERE episode_id=?", (episode_id,))]
            packaged = db.execute("SELECT 1 FROM package JOIN delivery ON delivery.id=package.delivery_id WHERE delivery.episode_id=? LIMIT 1", (episode_id,)).fetchone()
        matrix = self.cast_matrix(episode_id)
        roles = matrix["roles"]
        cast_done = bool(roles and matrix["rows"]) and all(
            segment["cells"].get(role, {}).get("status") in ("ok","absent")
            for segment in matrix["rows"] for role in roles)
        prompt_art = next((a for a in view["artifacts"] if a["stage"]=="prompts"), {})
        adopted_id = prompt_art.get("adopted_version_id")
        statuses = self.prompt_review_status(adopted_id) if adopted_id else {}
        prompt = next((v["content"] for v in prompt_art.get("versions",[]) if v["id"]==adopted_id), {})
        entries = prompt.get("segments",{})
        assets = {asset["id"]: asset for asset in self.episode_assets(episode_id)}
        required = [a["requirement"] for a in assets.values() if "requirement" in a]
        used = {}
        for segment_id, entry in entries.items():
            for ref in entry.get("refs", []):
                asset = assets.get(ref.get("assetVersionId"))
                if asset and asset["kind"] == "image":
                    used.setdefault(asset["sha256"], set()).add(segment_id)
        plan_art=next((a for a in view["artifacts"] if a["stage"]=="plan"),{})
        adopted_plan=next((v["content"] for v in plan_art.get("versions",[]) if v["id"]==plan_art.get("adopted_version_id")), {})
        expected={s["id"] for s in adopted_plan.get("segments",[])}
        missing_images = imagechoices.check_scope(required, adopted_plan, "full", expected, used)[1] if expected else []
        complete=bool(expected) and expected==set(statuses)
        view["productionStatus"] = dict(space=bool(spaces) and all(s=="confirmed" for s in spaces),
                                        cast=cast_done, assets=complete and all(entries.get(s,{}).get("refs") for s in statuses) and not missing_images,
                                        review=complete and all(s["ok"] for s in statuses.values()), package=bool(packaged))
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

    def run_methods(self, run_id):
        snapshot = self.run_manifest(run_id)
        if not snapshot["matches"]:
            raise Rejected("运行输入清单与登记指纹不一致")
        manifest = snapshot["manifest"]
        pack = manifest.get("methodPack") or {}
        sources = pack.get("files", {})
        result = []
        for entry in manifest["inputs"]:
            legacy = historical_method(entry, manifest)
            if not legacy and entry["role"] not in ("method", "project_method"):
                continue
            record = legacy or sources.get(entry["name"], {})
            if record.get("sha256",entry["sha256"]) != entry["sha256"]:
                raise Rejected("方法来源与实际输入指纹不一致：" + entry["name"])
            try:
                raw = self.store.read_blob(entry["sha256"])
                text = raw.decode("utf-8")
            except (OSError, ValueError) as exc:
                raise Rejected("无法读取经过指纹核对的方法原文：" + entry["name"]) from exc
            result.append(dict(name=entry["name"],role=entry["role"],sha256=entry["sha256"],text=text,
                               source=record.get("source"), path=record.get("path")))
        return dict(ref=pack.get("id"), inputHash=snapshot["fingerprint"], sections=result)

    def _version_creation_runs(self, db, version):
        pending, seen, result = [version["id"]], set(), []
        while pending:
            vid = pending.pop()
            if vid in seen:
                continue
            seen.add(vid)
            item = self._version_of(db, vid, version["artifact_id"])
            if item["source_run_id"]:
                result.append(_row(db, "SELECT * FROM run WHERE id=?", item["source_run_id"]))
                continue
            parents = [item["base_id"]] if item["base_id"] else []
            if item["origin"] == "combined":
                try:
                    recorded = json.loads(item["note"])
                except (ValueError,TypeError):
                    recorded = {}
                if isinstance(recorded,dict) and isinstance(recorded.get("sources"),list):
                    parents.extend(recorded["sources"])
            pending.extend(reversed(parents))
        return result

    def _version_materials(self, db, version, roles):
        materials = {}
        for run in self._version_creation_runs(db,version):
            manifest, digest = self.store.load_manifest(run["manifest_relpath"])
            if digest != run["input_hash"]:
                raise Rejected("创作输入清单指纹不符")
            for item in manifest["inputs"]:
                if item["role"] not in roles:
                    continue
                key = (item["role"],item["name"],item["sha256"])
                if key not in materials:
                    materials[key] = dict(item,text=self.store.read_blob(item["sha256"]).decode(),sourceRuns=[])
                if run["id"] not in materials[key]["sourceRuns"]:
                    materials[key]["sourceRuns"].append(run["id"])
        return list(materials.values())

    def version_sources(self, version_id, reference_only=False):
        with connect(self.db_path) as db:
            v = _row(db, "SELECT v.*, a.stage, a.episode_id FROM version v JOIN artifact a ON a.id=v.artifact_id "
                         "WHERE v.id=?", version_id)
            roles = {"reference_pack","adaptation_source"}
            if not reference_only:
                roles.update({"citation","visual_reference","project_method","method","step_requirements","trial_reference"})
                if v["stage"] == "script":
                    roles.add("adopted_story")
            inputs = self._version_materials(db,v,roles)
            for item in inputs:
                if item["role"] == "adopted_story":
                    header = item["text"].partition("\n")[0]
                    if header.startswith("工作台采用版本："):
                        story = db.execute(
                            "SELECT v.id, v.display_no FROM version v JOIN artifact a ON a.id=v.artifact_id "
                            "WHERE v.id=? AND a.episode_id=? AND a.stage='story'",
                            (header.removeprefix("工作台采用版本："), v["episode_id"])).fetchone()
                        if story:
                            item["storyVersion"] = dict(story)
        return dict(origin=v["origin"],base=v["base_id"],run=v["source_run_id"],inputs=inputs)

    def segment_palette_trace(self, version_id, segment):
        with connect(self.db_path) as db:
            current = _row(db,"SELECT v.*,a.stage FROM version v JOIN artifact a ON a.id=v.artifact_id WHERE v.id=?",version_id)
            if current["stage"]!="prompts" or segment not in json.loads(current["content"]).get("segments",{}):
                raise Rejected("请选择提示词版本中的生成段")
            origin = current
            while not origin["source_run_id"] and origin["base_id"]:
                origin = self._version_of(db,origin["base_id"],current["artifact_id"])
                if segment not in json.loads(origin["content"]).get("segments",{}):
                    return None
            if not origin["source_run_id"]:
                return None
        run = self.run_manifest(origin["source_run_id"])
        if not run["matches"]:
            raise Rejected("创作输入清单指纹不符")
        cards = [json.loads(self.store.read_blob(i["sha256"])) for i in run["manifest"]["inputs"]
                 if i["role"]=="visual_reference"]
        return dict(cards=cards,run=origin["source_run_id"],version=origin["id"],
                    direct=origin["id"]==version_id,displayNo=origin["display_no"])

    def probe(self):
        from .executors.cli import CliExecutor
        result = {}
        for name, executor in self.executors.items():
            info = executor.probe(self.run_connection(name)) if isinstance(executor,CliExecutor) else executor.probe()
            simulated = info.get("structured_result") == "simulated"
            result[name] = dict(info, status="simulated" if simulated else info.get("status", "unknown"),
                                isolation=self._isolation_status(name), checkedAt=self.clock(),
                                configured=self.get_setting(name + "_path"))
        return result


from .delivery import DeliveryMixin          # noqa: E402
from .images import ImagesMixin              # noqa: E402
from .production import ProductionMixin      # noqa: E402
from .connections import ConnectionsMixin
from .projectops import ProjectOpsMixin      # noqa: E402
from .reviews2 import PromptReviewMixin      # noqa: E402


from .sourcefiles import SourceFilesMixin


from .resources import ResourcesMixin


from .providers import ProvidersMixin


class Workbench(ConnectionsMixin, FreeChatMixin, EpisodeContactMixin, CustomMethodsMixin, ProvidersMixin, ResourcesMixin, SourceFilesMixin, ProductionMixin, ImagesMixin, PromptReviewMixin, DeliveryMixin, ProjectOpsMixin, Core):
    """The application service: core commands plus the production, image, review, delivery and project mixins."""
