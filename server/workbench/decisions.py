from .chatpages import VISIBLE_TASK
"""Episode director decisions. Scope and quote checks use explicit IDs and exact text."""
import hashlib
import json
import re

from . import workflow
from .db import connect, transaction, new_id, dumps
from .errors import Rejected, Conflict, row
from .storage import canonical

START_INSTRUCTIONS = {"topic": "按选题卡起草故事稿", "import": "按导入的原稿起草故事稿"}
def applicable_decisions(db, episode_id, stage):
    stage = "prompts" if stage == "review" else stage
    if stage not in workflow.ORDER:
        return []
    stages = workflow.ORDER[:workflow.ORDER.index(stage)+1]
    return [dict(r, scenes=json.loads(r["scope_value"])) for r in db.execute(
        "SELECT * FROM director_decision WHERE episode_id=? AND state='active' ORDER BY rowid", (episode_id,))
        if r["origin_stage"] in stages]


def decision_entries(db, episode_id, stage, scenes=None, segment=None):
    def applies(record):
        if segment is None or record['origin_kind']!='dialogue':return True
        run=db.execute("SELECT scope FROM continuous_run WHERE id=? AND mode='fix'",(record['origin_ref'],)).fetchone()
        tasks=json.loads(run[0]) if run else []
        task=next((t for t in tasks if t.get('decisionId')==record['id']),None)
        return not task or not task.get('segments') or segment in task['segments']
    items = [dict(code=r["code"], text=r["text"], originStage=r["origin_stage"], scope=r["scope_kind"], scenes=r["scenes"])
             for r in applicable_decisions(db, episode_id, stage)
             if applies(r) and (scenes is None or r["scope_kind"] == "episode" or scenes.intersection(r["scenes"]))]
    return [dict(role="director_decisions", name="导演决定清单.json", text=canonical(items).decode())] if items else []


def _text_fields(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (dict, list)):
        for child in value.values() if isinstance(value, dict) else value:
            yield from _text_fields(child)


def decision_errors(db, art, content, plan):
    decisions = applicable_decisions(db, art["episode_id"], art["stage"])
    return check_decision_notes(decisions, art["stage"], content, plan)


def check_decision_notes(decisions, stage, content, plan):
    errors = []
    notes = content.get("decisionNotes", [])
    if not isinstance(notes, list) or any(not isinstance(n,dict) for n in notes):
        return ["decisionNotes 须为记录数组"]
    segments = content.get("segments", {}) if stage == "prompts" else {}
    for d in decisions:
        code = d.get("code")
        found = [n for n in notes if n.get("decision") == code]
        if not found:
            errors.append(f"决定 {code} 没有落点说明")
            continue
        seen = set()
        for n in found:
            sid = n.get("segmentId") if stage == "prompts" else None
            if sid is not None and (not isinstance(sid, str) or not isinstance(segments, dict) or not isinstance(segments.get(sid), dict)):
                errors.append(f"决定 {code} 的段号不在本稿中")
                continue
            prefix = f"{sid}：" if sid else ""
            if sid in seen or (len(found) > 1 and not sid):
                errors.append(prefix + f"决定 {code} 的落点重复")
                continue
            seen.add(sid)
            values = [segments[sid].get("txt","")] if sid else [
                t for k,v in content.items() if k not in ("decisionNotes","notes") for t in _text_fields(v)]
            body = [re.sub(r'\s+', '', t) for t in values if isinstance(t,str)]
            def field(key):
                return n[key].strip() if isinstance(n.get(key), str) else ""
            if n.get("status") == "not_applicable" and field("reason"):
                continue
            if n.get("status") != "applied" or not field("where"):
                errors.append(prefix + f"决定 {code} 须填写落点或不适用理由")
                continue
            quote = re.sub(r'\s+', '', field("quote"))
            if not quote or not any(quote in t for t in body):
                errors.append(prefix + f"决定 {code} 的引文不在本稿中")
            if d.get("scope_kind", d.get("scope")) == "scenes":
                ids = set() if sid else set(d["scenes"])
                for shot in (plan or {}).get("shots", []):
                    if not isinstance(shot,dict) or (sid and shot.get("segment") != sid):
                        continue
                    scene = (shot.get("origin") or {}).get("scene")
                    if scene in d["scenes"]:
                        ids.add(scene)
                        ids.update([shot.get("id"), shot.get("segment")])
                if not any(re.search(r"(?<![A-Za-z0-9])"+re.escape(i)+r"(?![A-Za-z0-9])",field("where")) for i in ids if i):
                    errors.append(prefix + f"决定 {code} 的落点不在指定场次")
    return errors


def revised_segment_notes(previous, submitted, changed, current):
    """Keep the notes attached to unchanged segments when applying a local revision."""
    if not isinstance(submitted, list) or any(not isinstance(n, dict) for n in submitted):
        raise ValueError("decisionNotes 须为记录数组")
    replacements = []
    for note in submitted:
        sid = note.get("segmentId")
        if sid and sid not in changed:
            continue
        if not sid and len(changed) == 1:
            note = dict(note, segmentId=next(iter(changed)))
        elif not sid and any(n.get("segmentId") and n.get("decision")==note.get("decision") for n in previous):
            raise ValueError("同时修订多段时，请在每条决定落点填写 segmentId")
        replacements.append(note)
    keys = {(n.get("decision"), n.get("segmentId")) for n in replacements}
    replaced_codes = {code for code, _ in keys}
    saved = [n for n in previous if (n.get("segmentId") in current and
             (n.get("decision"), n.get("segmentId")) not in keys) or
             (not n.get("segmentId") and n.get("decision") not in replaced_codes)]
    return saved + replacements


class DecisionMixin:
    def _decision_impact(self, db, d, verb):
        stages = workflow.ORDER[workflow.ORDER.index(d["origin_stage"]):]
        ep = self._episode_in(db, d["episode_id"])
        reason = f"导演决定 {d['code']} 已{verb}，须核对"
        items = [dict(target="workflow:"+r[0], reason=reason) for r in db.execute(
            "SELECT stage FROM artifact WHERE episode_id=? AND adopted_version_id IS NOT NULL", (ep["id"],)) if r[0] in stages]
        self._mark_deliveries_stale(db, ep["id"], reason)
        self._record_impacts(db, dict(episode_id=ep["id"], project_id=ep["project_id"]), items, "decision_change", d["id"])

    def _new_decision(self, db, eid, text, stage, kind, ref, scope, scenes, replaces=None):
        if stage not in workflow.ORDER or not isinstance(text,str) or not text.strip():
            raise Rejected("须填写决定原话及有效来源阶段")
        if scope not in ("episode","scenes") or not isinstance(scenes,list) or any(not isinstance(s,str) for s in scenes):
            raise Rejected("决定范围格式无效")
        if scope == "scenes":
            valid = {s["id"] for s in (self._adopted_content(db,eid,"script") or {}).get("scenes",[])}
            if not scenes or not set(scenes) <= valid:
                raise Rejected("请选择采用剧本中的场号")
        code = "D%02d" % (1+db.execute("SELECT count(*) FROM director_decision WHERE episode_id=?", (eid,)).fetchone()[0])
        did = new_id()
        db.execute("INSERT INTO director_decision(id,episode_id,code,text,origin_stage,origin_kind,origin_ref,scope_kind,scope_value,replaces_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (did,eid,code,text.strip(),stage,kind,ref,scope,dumps(list(dict.fromkeys(scenes)) if scope=="scenes" else []),replaces,self.clock()))
        self._decision_impact(db, dict(episode_id=eid,code=code,id=did,origin_stage=stage),"新增")
        return dict(id=did,code=code)

    def _retire_decision(self, db, d):
        if d["state"] != "active":
            raise Conflict("该决定已撤销，请刷新")
        db.execute("UPDATE director_decision SET state='revoked',revoked_at=? WHERE id=?",(self.clock(),d["id"]))
        self._decision_impact(db, d,"撤销")

    def add_decision(self, episode_id, text, origin_stage, scope_kind, scope_value, command_id):
        with transaction(self.db_path) as db:
            return self._command(db, command_id, "add_decision", [episode_id,text,origin_stage,scope_kind,scope_value],
                lambda:self._new_decision(db,episode_id,text,origin_stage,"manual","",scope_kind,scope_value))

    def revoke_decision(self, decision_id, command_id):
        with transaction(self.db_path) as db:
            def run():
                self._retire_decision(db,row(db,"SELECT * FROM director_decision WHERE id=?",decision_id))
                return dict(id=decision_id)
            return self._command(db,command_id,"revoke_decision",dict(id=decision_id),run)

    def replace_decision(self, decision_id, text, scope_kind, scope_value, command_id):
        with transaction(self.db_path) as db:
            def run():
                d = row(db,"SELECT * FROM director_decision WHERE id=?",decision_id)
                self._retire_decision(db,d)
                return self._new_decision(db,d["episode_id"],text,d["origin_stage"],d["origin_kind"],d["origin_ref"],scope_kind,scope_value,d["id"])
            return self._command(db,command_id,"replace_decision",[decision_id,text,scope_kind,scope_value],run)

    def _sync_keep(self, db, eid, stage, old, new):
        if stage not in workflow.ORDER:
            return
        previous, current = [dict.fromkeys(s.strip() for s in text.splitlines() if s.strip()) for text in (old,new)]
        for line in [*previous.keys()-current.keys(), *(s for s in current if s not in previous)]:
            ref = "keep:"+hashlib.sha256(line.encode()).hexdigest()[:16]
            if line in current:
                self._new_decision(db,eid,line,stage,"keep",ref,"episode",[])
            else:
                d = db.execute("SELECT * FROM director_decision WHERE episode_id=? AND origin_stage=? AND origin_ref=? AND state='active'", (eid,stage,ref)).fetchone()
                if d:
                    self._retire_decision(db,d)

    def director_decisions(self, episode_id):
        with connect(self.db_path) as db:
            versions = [dict(v,notes=json.loads(v["content"]).get("decisionNotes",[])) for v in db.execute(
                "SELECT v.id,v.content,a.stage FROM artifact a JOIN version v ON a.adopted_version_id=v.id WHERE a.episode_id=?", (episode_id,))]
            return [dict(d, scenes=json.loads(d["scope_value"]), placements=[
                dict(n,stage=v["stage"],versionId=v["id"]) for v in versions for n in v["notes"]
                if n["decision"]==d["code"]]) for d in db.execute("SELECT * FROM director_decision WHERE episode_id=? ORDER BY rowid",(episode_id,))]

    def decision_candidates(self, artifact_id):
        """Unregistered messages, including questions. The director chooses what to record."""
        with connect(self.db_path) as db:
            art = row(db, "SELECT * FROM artifact WHERE id=?", artifact_id)
            return [dict(r) for r in db.execute(
                """SELECT m.id AS messageId,m.text,m.created_at AS createdAt FROM message m
                   JOIN thread h ON m.thread_id=h.id JOIN task t ON h.task_id=t.id
                   WHERE t.artifact_id=? AND m.role='user' AND m.kind='text'
                   AND """+VISIBLE_TASK+"""
                   AND NOT EXISTS (SELECT 1 FROM director_decision d WHERE d.episode_id=?
                                   AND d.origin_ref='message:'||m.id)
                   ORDER BY m.created_at,m.rowid""", (artifact_id, art["episode_id"]))]

    def record_message_decision(self, episode_id, message_id, command_id):
        with transaction(self.db_path) as db:
            def run():
                msg = self._decision_message(db, episode_id, message_id)
                ref = "message:" + message_id
                existing = db.execute("SELECT id,code FROM director_decision WHERE episode_id=? AND origin_ref=?",
                                      (episode_id, ref)).fetchone()
                if existing:
                    return dict(existing)
                return self._new_decision(db, episode_id, msg["message_text"], msg["stage"], "dialogue", ref, "episode", [])
            return self._command(db, command_id, "record_message_decision", [episode_id, message_id], run)

    def record_decisions(self, episode_id, items, dismissed, command_id):
        with transaction(self.db_path) as db:
            def run():
                for data in items:
                    ref = data["originRef"]
                    if not ref.startswith("message:") or data["originKind"]!="dialogue":
                        raise Rejected("须引用本步用户消息")
                    msg = self._decision_message(db,episode_id,ref[8:])
                    if msg["stage"] != data["originStage"]:
                        raise Rejected("决定来源阶段不符")
                    if db.execute("SELECT 1 FROM director_decision WHERE episode_id=? AND origin_ref=?",(episode_id,ref)).fetchone():
                        continue
                    self._new_decision(db,episode_id,data["text"],msg["stage"],"dialogue",ref,data["scopeKind"],data["scopeValue"])
                groups = {}
                for mid in dismissed:
                    art = self._decision_message(db,episode_id,mid)
                    groups.setdefault(art["id"],set()).add(mid)
                for aid, ids in groups.items():
                    key = "decision-dismissed:"+aid
                    old = db.execute("SELECT value FROM app_setting WHERE key=?",(key,)).fetchone()
                    db.execute("INSERT OR REPLACE INTO app_setting VALUES(?,?)",(key,dumps(sorted(ids.union(json.loads(old[0]) if old else [])))))
                return dict(ok=True)
            return self._command(db,command_id,"record_decisions",[episode_id,items,dismissed],run)

    def _decision_message(self, db, eid, mid):
        return row(db,"SELECT a.*,m.text AS message_text FROM message m JOIN thread h ON h.id=m.thread_id JOIN task t ON t.id=h.task_id JOIN artifact a ON a.id=t.artifact_id WHERE m.id=? AND m.role='user' AND m.kind='text' AND a.episode_id=? AND "+VISIBLE_TASK,mid,eid)
