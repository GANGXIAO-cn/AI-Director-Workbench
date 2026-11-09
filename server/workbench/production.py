"""Production mixin: threads, compare/combine, plan timing, reference films, shot refs, space, cast, prompts."""
import difflib
import json
from pathlib import Path
import re
import shutil
import subprocess

from . import h3export, imagechoices, promptrefs, readable, shotrefs
from . import stages, timeline, rhythm, acting
from .palette_inputs import stage_card
from .db import connect, dumps, new_id, transaction
from .errors import Conflict, NotFound, Rejected, loads, row
from .images import image_attachment
from .storage import sha256_bytes


# ---------------------------------------------------------------- local media tools (real, no network)
def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise ValueError("不可读：" + out.stderr.strip()[:200])
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("没有视频轨")
    return dict(duration_ms=int(float(data["format"]["duration"]) * 1000),
                has_audio=any(s.get("codec_type") == "audio" for s in streams),
                width=video.get("width"), height=video.get("height"))


def ffmpeg_cuts(path: Path, start_ms: int, end_ms: int, threshold=0.12) -> list[int]:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{start_ms / 1000}", "-to", f"{end_ms / 1000}", "-i", str(path),
                          "-filter:v", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=600)
    return sorted({start_ms + int(float(m) * 1000) for m in re.findall(r"pts_time:([0-9.]+)", out.stderr)})


def line_diff(a: str, b: str) -> list[dict]:
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(dict(op="same", text=a[i1:i2]))
        else:
            if i2 > i1:
                out.append(dict(op="del", text=a[i1:i2]))
            if j2 > j1:
                out.append(dict(op="add", text=b[j1:j2]))
    return out


class ProductionMixin:
    media_probe = staticmethod(ffprobe)
    cut_detector = staticmethod(ffmpeg_cuts)

    # ============================================================ threads
    def answer_clarification(self, task_id, answer, command_id):
        payload = dict(task_id=task_id, answer=answer)
        with transaction(self.db_path) as db:
            def run():
                task = row(db, "SELECT * FROM task WHERE id=?", task_id)
                if task["state"] != "clarifying":
                    raise Rejected("任务没有待回答的澄清")
                thread = db.execute("SELECT id FROM thread WHERE task_id=?", (task_id,)).fetchone()[0]
                q = db.execute("SELECT text,payload FROM message WHERE thread_id=? AND kind='clarify' ORDER BY seq DESC",
                               (thread,)).fetchone()
                info = json.loads(q["payload"])
                original = db.execute("SELECT text,selection FROM message WHERE thread_id=? AND role='user' AND kind='text' "
                                      "ORDER BY seq DESC", (thread,)).fetchone()
                request = f"{original['text']}\n澄清：{q['text']}\n导演答复：{answer}"
                selection = json.loads(original["selection"]) if original["selection"] else None
                run_id = self._queue_run(db, task_id, "revise" if info.get("baseVersionId") else "create",
                                         info.get("baseVersionId"), request, None)
                mid = self._message(db, thread, "user", answer, selection, None, run_id, kind="answer")
                return dict(messageId=mid, runId=run_id)
            return self._command(db, command_id, "answer", payload, run)

    def stop_run(self, run_id, command_id=None):
        """显式停止: mark stopped, then terminate the matching process group. Saved drafts are kept."""
        with transaction(self.db_path) as db:
            run_ = row(db, "SELECT * FROM run WHERE id=?", run_id)
            if run_["status"] not in ("queued", "running"):
                raise Rejected("该运行已结束")
            db.execute("UPDATE run SET status='stopped', reason='用户停止', ended_at=? WHERE id=?", (self.clock(), run_id))
            self._set_task_run_state(db,run_["task_id"],"awaiting_decision")
        killed = self.executors[run_["provider"]].cancel(run_id) if run_["status"] == "running" else False
        return dict(runId=run_id, state="stopped", processStopped=killed)

    def abandon_task(self, task_id, command_id):
        with transaction(self.db_path) as db:
            def run():
                row(db, "SELECT id FROM task WHERE id=?", task_id)
                db.execute("UPDATE task SET state='abandoned' WHERE id=?", (task_id,))
                return dict(taskId=task_id, state="abandoned")
            return self._command(db, command_id, "abandon", dict(task_id=task_id), run)

    def continue_check(self, task_id):
        """Resuming an old thread: compare its input base with the current adopted version."""
        with connect(self.db_path) as db:
            task = row(db, "SELECT * FROM task WHERE id=?", task_id)
            art = row(db, "SELECT * FROM artifact WHERE id=?", task["artifact_id"])
            last = db.execute("SELECT base_version_id FROM run WHERE task_id=? AND purpose IN ('create','revise') "
                              "ORDER BY created_at DESC", (task_id,)).fetchone()
        base = last[0] if last else None
        return dict(taskBase=base, adopted=art["adopted_version_id"],
                    needsChoice=bool(art["adopted_version_id"] and base != art["adopted_version_id"]))

    # ============================================================ compare / combine
    def _ancestors(self, db, vid):
        out = []
        while vid:
            out.append(vid)
            vid = db.execute("SELECT base_id FROM version WHERE id=?", (vid,)).fetchone()[0]
        return out

    def compare(self, a_id, b_id):
        with connect(self.db_path) as db:
            a = loads(row(db, "SELECT * FROM version WHERE id=?", a_id), "content")
            b = loads(row(db, "SELECT * FROM version WHERE id=?", b_id), "content")
            if a["artifact_id"] != b["artifact_id"]:
                raise Rejected("只能比较同一成果的版本")
            stage = db.execute("SELECT stage FROM artifact WHERE id=?", (a["artifact_id"],)).fetchone()[0]
            anc_a = self._ancestors(db, a_id)
            common = next((v for v in self._ancestors(db, b_id) if v in anc_a), None)
        fields = {}
        for key in sorted(set(a["content"]) | set(b["content"])):
            ta = a["content"].get(key)
            tb = b["content"].get(key)
            if ta != tb:
                sa = ta if isinstance(ta, str) else json.dumps(ta, ensure_ascii=False, indent=1)
                sb = tb if isinstance(tb, str) else json.dumps(tb, ensure_ascii=False, indent=1)
                fields[key] = line_diff(sa or "", sb or "")
        return dict(a=a_id, b=b_id, commonBase=common, stage=stage, fields=fields)

    def combine(self, artifact_id, picks: dict, command_id, note=""):
        """picks: {field: versionId}. Creates a combined draft with its own sources and pending review."""
        payload = dict(artifact_id=artifact_id, picks=picks)
        with transaction(self.db_path) as db:
            def run():
                content, sources = {}, sorted(set(picks.values()))
                for field, vid in picks.items():
                    v = self._version_of(db, vid, artifact_id)
                    content[field] = json.loads(v["content"]).get(field)
                base = sources[0]
                full = dict(json.loads(self._version_of(db, base, artifact_id)["content"]), **content)
                vid = self._new_version(db, artifact_id, base, "combined", full,
                                        note=note or "组合自 " + "、".join(sources))
                db.execute("UPDATE version SET note=? WHERE id=?", (dumps(dict(sources=sources, picks=picks)), vid))
                return dict(versionId=vid, sources=sources)
            return self._command(db, command_id, "combine", payload, run)

    # ============================================================ plan timing
    def restore_version(self, version_id, expected_adopted_version_id, command_id):
        with transaction(self.db_path) as db:
            def run():
                old = loads(row(db,"SELECT * FROM version WHERE id=?",version_id),"content")
                art = row(db,"SELECT * FROM artifact WHERE id=?",old["artifact_id"])
                if art["adopted_version_id"]!=expected_adopted_version_id:
                    raise Conflict("采用版已变化，请刷新后重新比较",current=art["adopted_version_id"])
                vid = self._new_version(db,art["id"],version_id,"manual",old["content"],note=f"恢复完整稿 v{old['display_no']}",baseline_from=version_id)
                if art["stage"]=="publish":
                    source_basis = db.execute("SELECT basis FROM version_publish_basis WHERE version_id=?", (version_id,)).fetchone()
                    if source_basis:
                        db.execute("UPDATE version_publish_basis SET basis=? WHERE version_id=?", (source_basis[0], vid))
                    db.execute("UPDATE version_publish_config SET settings=(SELECT settings FROM version_publish_config WHERE version_id=?) "
                               "WHERE version_id=? AND EXISTS(SELECT 1 FROM version_publish_config WHERE version_id=?)",(version_id,vid,version_id))
                return dict(versionId=vid, sourceVersionId=version_id)
            return self._command(db,command_id,"restore_version",dict(version=version_id,adopted=expected_adopted_version_id),run)

    def plan_preview(self, version_id):
        with connect(self.db_path) as db:
            v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
            art = row(db, "SELECT * FROM artifact WHERE id=?", v["artifact_id"])
            old = self._adopted_content(db, art["episode_id"], "plan")
        return dict(timeline.preview(old, v["content"]), layout=timeline.layout(v["content"]),
                    problems=timeline.check(v["content"]), packagingProblems=timeline.check(v["content"], True))

    def split_segment(self, version_id, segment_id, first_shot_of_second, command_id):
        with transaction(self.db_path) as db:
            def run():
                v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
                new, mapping = timeline.split_segment(v["content"], segment_id, first_shot_of_second)
                vid = self._new_version(db, v["artifact_id"], version_id, "manual", new,
                                        note=dumps(dict(split=mapping)))
                return dict(versionId=vid, mapping=mapping)
            return self._command(db, command_id, "split", dict(v=version_id, s=segment_id, f=first_shot_of_second), run)

    def _record_impacts(self, db, art, items, kind, source_ref):
        out = []
        for item in items:
            iid = new_id()
            db.execute("INSERT INTO impact VALUES(?,?,?,?,?,?,?,?,?)",
                       (iid, art["project_id"], art["episode_id"], kind, source_ref, item["target"], item["reason"],
                        "pending", self.clock()))
            out.append(dict(item, id=iid))
        return out

    def _after_adopt(self, db, art, version_id, previous_id):
        content = json.loads(db.execute("SELECT content FROM version WHERE id=?", (version_id,)).fetchone()[0])
        old = json.loads(db.execute("SELECT content FROM version WHERE id=?", (previous_id,)).fetchone()[0]) \
            if previous_id else None
        ref = f"{art['stage']}:{version_id}"
        if art["stage"] == "story":
            db.execute("UPDATE topic SET status='已成稿' WHERE episode_id=? AND status IN ('开发中','待审稿')",
                       (art["episode_id"],))
            return []
        if art["stage"] == "script" and old:
            changed = {s["id"] for s in content.get("scenes", [])} ^ {s["id"] for s in old.get("scenes", [])}
            olds = {s["id"]: s for s in old.get("scenes", [])}
            changed |= {s["id"] for s in content.get("scenes", []) if olds.get(s["id"]) not in (None, s)}
            plan = self._adopted_content(db, art["episode_id"], "plan") or {}
            items = [dict(target=f"镜头 {sh['id']}", reason=f"引用的场次 {sh.get('origin', {}).get('scene')} 已修改")
                     for sh in plan.get("shots", []) if sh.get("origin", {}).get("scene") in changed]
            return self._record_impacts(db, art, items, "script_change", ref)
        if art["stage"] == "plan":
            pre = timeline.preview(old, content) if old else None
            if not pre:
                return []
            items = [dict(target=f"prompts:{sid}", reason="段内镜头或时长变化，TXT 需复核")
                     for sid in pre["changedSegments"] if sid not in pre["removed"]]
            items += [dict(target=f"joint:{a}-{b}", reason="相邻接点需复核") for a, b in pre["joints"]]
            items += [dict(target=d["target"], reason=d["reason"]) for d in pre["downstream"]
                      if not d["target"].startswith(("提示词", "关键帧"))]
            return self._record_impacts(db, art, items, "plan_change", ref)
        return []

    def impacts(self, episode_id, state="pending"):
        with connect(self.db_path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM impact WHERE episode_id=? AND state=? ORDER BY created_at",
                                                (episode_id, state))]

    def resolve_impact(self, impact_id, command_id):
        with transaction(self.db_path) as db:
            def run():
                row(db, "SELECT id FROM impact WHERE id=?", impact_id)
                db.execute("UPDATE impact SET state='resolved' WHERE id=?", (impact_id,))
                return dict(impactId=impact_id)
            return self._command(db, command_id, "resolve_impact", dict(i=impact_id), run)

    # ============================================================ reference films
    def import_reference(self, project_id, name, data: bytes, command_id=None, replaces_id=None):
        payload = dict(p=project_id, n=name, sha=sha256_bytes(data), r=replaces_id)
        with transaction(self.db_path) as db:
            def run():
                row(db, "SELECT id FROM project WHERE id=?", project_id)
                sid = new_id()
                rel = self.store.import_copy(project_id, "ref-" + sid, name, data)
                try:
                    probe = self.media_probe(self.store.root / rel)
                    state = "readable"
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    probe, state = dict(error=str(exc)), "unreadable"
                same = db.execute("SELECT id,sha256 FROM ref_source WHERE project_id=? AND name=? ORDER BY created_at DESC",
                                  (project_id, Path(name).name)).fetchone()
                note = None
                if same and same["sha256"] != sha256_bytes(data):
                    note = "同名文件内容不同，已另建来源记录"
                db.execute("INSERT INTO ref_source VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (sid, project_id, Path(name).name, sha256_bytes(data), rel, probe.get("duration_ms"),
                            int(bool(probe.get("has_audio"))) if state == "readable" else None, dumps(probe), state,
                            replaces_id or (same["id"] if note else None), self.clock()))
                return dict(sourceId=sid, state=state, probe=probe, note=note)
            return self._command(db, command_id, "import_ref", payload, run)

    def create_pack(self, source_id, episode_id=None, range_ms=None, request="", lineage=None, command_id=None):
        payload = dict(s=source_id, e=episode_id, r=range_ms, q=request, l=lineage)
        with transaction(self.db_path) as db:
            def run():
                src = row(db, "SELECT * FROM ref_source WHERE id=?", source_id, missing="来源不存在")
                if src["state"] != "readable":
                    raise Rejected("原片不可读，不能建参考包")
                if episode_id:
                    self._episode_in(db,episode_id,src["project_id"])
                if lineage and db.execute("SELECT 1 FROM ref_pack WHERE lineage=? AND project_id<>?",
                                          (lineage,src["project_id"])).fetchone():
                    raise Rejected("参考包版本链属于另一个项目")
                start, end = range_ms or (0, src["duration_ms"])
                if not 0 <= start < end <= src["duration_ms"]:
                    raise Rejected("分析区间超出原片时长")
                lin = lineage or new_id()
                version = db.execute("SELECT coalesce(max(version),0)+1 FROM ref_pack WHERE lineage=?", (lin,)).fetchone()[0]
                pid = new_id()
                db.execute("INSERT INTO ref_pack VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (pid, src["project_id"], episode_id, source_id, lin, version, start, end, request,
                            "cutting", self.clock()))
                cuts = [c for c in self.cut_detector(self.store.root / src["relpath"], start, end) if start < c < end]
                bounds = [start, *cuts, end]
                unknown = [] if src["has_audio"] else ["sound"]
                for i in range(len(bounds) - 1):
                    db.execute("INSERT INTO ref_shot(id,pack_id,code,start_ms,end_ms,unknown_fields) VALUES(?,?,?,?,?,?)",
                               (new_id(), pid, f"S{i + 1:02d}", bounds[i], bounds[i + 1], dumps(unknown)))
                return dict(packId=pid, version=version, lineage=lin, shots=len(bounds) - 1)
            return self._command(db, command_id, "create_pack", payload, run)

    def pack_view(self, pack_id):
        with connect(self.db_path) as db:
            pack = row(db, "SELECT * FROM ref_pack WHERE id=?", pack_id)
            pack["source"] = loads(row(db, "SELECT * FROM ref_source WHERE id=?", pack["source_id"]), "probe")
            pack["shots"] = [loads(dict(r), "unknown_fields") for r in db.execute(
                "SELECT * FROM ref_shot WHERE pack_id=? ORDER BY start_ms", (pack_id,))]
            pack["beats"] = [loads(dict(r), "shot_codes") for r in db.execute(
                "SELECT * FROM beat WHERE pack_id=? ORDER BY code", (pack_id,))]
            pack["bookmarks"] = [dict(r) for r in db.execute("SELECT * FROM shot_bookmark WHERE pack_id=?", (pack_id,))]
        return pack

    def _renumber(self, db, pack_id):
        rows = db.execute("SELECT id FROM ref_shot WHERE pack_id=? ORDER BY start_ms", (pack_id,)).fetchall()
        for i, r in enumerate(rows):
            db.execute("UPDATE ref_shot SET code=? WHERE id=?", (f"tmp{i}", r[0]))
        for i, r in enumerate(rows):
            db.execute("UPDATE ref_shot SET code=? WHERE id=?", (f"S{i + 1:02d}", r[0]))

    def edit_cuts(self, pack_id, op, code, at_ms=None, command_id=None, fields=None):
        """op: split (at_ms) | merge (with next) | confirm (fields). Lists affected beats first."""
        payload = dict(p=pack_id, op=op, c=code, at=at_ms, f=fields)
        with transaction(self.db_path) as db:
            def run():
                pack = row(db, "SELECT * FROM ref_pack WHERE id=?", pack_id)
                if pack["state"] == "saved" and op != "confirm":
                    raise Rejected("已保存的参考包不可改切点，请另建版本")
                shot = row(db, "SELECT * FROM ref_shot WHERE pack_id=? AND code=?", pack_id, code, missing="镜号不存在")
                affected = [b["code"] for b in db.execute("SELECT code,shot_codes FROM beat WHERE pack_id=?", (pack_id,))
                            if code in json.loads(b["shot_codes"])]
                if op == "split":
                    if not shot["start_ms"] < at_ms < shot["end_ms"]:
                        raise Rejected("拆分点不在该镜内")
                    db.execute("UPDATE ref_shot SET end_ms=?, confirmed=0 WHERE id=?", (at_ms, shot["id"]))
                    db.execute("INSERT INTO ref_shot(id,pack_id,code,start_ms,end_ms,unknown_fields) VALUES(?,?,?,?,?,?)",
                               (new_id(), pack_id, "new", at_ms, shot["end_ms"], shot["unknown_fields"]))
                    self._renumber(db, pack_id)
                elif op == "merge":
                    nxt = db.execute("SELECT * FROM ref_shot WHERE pack_id=? AND start_ms=?", (pack_id, shot["end_ms"])).fetchone()
                    if not nxt:
                        raise Rejected("没有后一镜可合并")
                    db.execute("UPDATE ref_shot SET end_ms=?, confirmed=0 WHERE id=?", (nxt["end_ms"], shot["id"]))
                    db.execute("DELETE FROM ref_shot WHERE id=?", (nxt["id"],))
                    self._renumber(db, pack_id)
                elif op == "confirm":
                    f = {**shot, **(fields or {})}
                    has_audio = row(db, "SELECT has_audio FROM ref_source WHERE id=?", pack["source_id"])["has_audio"]
                    if not has_audio:
                        f["sound"] = None
                    unknown = sorted(k for k in ("camera", "motion", "sound", "narrative")
                                     if f.get(k) in (None, "", "未知"))
                    db.execute("UPDATE ref_shot SET first_frame=?, last_frame=?, camera=?, motion=?, sound=?, narrative=?, "
                               "unknown_fields=?, confirmed=1 WHERE id=?",
                               (f.get("first_frame"), f.get("last_frame"), f.get("camera"), f.get("motion"), f.get("sound"),
                                f.get("narrative"), dumps(unknown), shot["id"]))
                else:
                    raise Rejected("未知操作")
                return dict(affectedBeats=affected)
            return self._command(db, command_id, "edit_cuts", payload, run)

    def add_beat(self, pack_id, code, shot_codes, event, info_gain, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                have = {r[0] for r in db.execute("SELECT code FROM ref_shot WHERE pack_id=?", (pack_id,))}
                if not shot_codes or set(shot_codes) - have:
                    raise Rejected("节拍引用的镜号不存在")
                bid = new_id()
                db.execute("INSERT INTO beat VALUES(?,?,?,?,?,?,1)", (bid, pack_id, code, dumps(shot_codes), event, info_gain))
                return dict(beatId=bid)
            return self._command(db, command_id, "beat", dict(p=pack_id, c=code, s=shot_codes, e=event), run)

    def bookmark(self, episode_id, pack_id, shot_code, reason, conditions="", command_id=None):
        with transaction(self.db_path) as db:
            def run():
                pack = row(db, "SELECT * FROM ref_pack WHERE id=?", pack_id)
                ep = self._episode_in(db, episode_id, pack["project_id"])
                shot = row(db, "SELECT * FROM ref_shot WHERE pack_id=? AND code=?", pack_id, shot_code)
                bid = new_id()
                db.execute("INSERT INTO shot_bookmark VALUES(?,?,?,?,?,?,?)",
                           (bid, ep["id"], pack_id, shot["id"], reason, conditions, self.clock()))
                return dict(bookmarkId=bid)
            return self._command(db, command_id, "bookmark", dict(e=episode_id, p=pack_id, s=shot_code, r=reason), run)

    def save_pack(self, pack_id, command_id=None):
        """「保存参考包并退出」: ends this analysis without creating any story task."""
        with transaction(self.db_path) as db:
            def run():
                pack = row(db, "SELECT * FROM ref_pack WHERE id=?", pack_id)
                if db.execute("SELECT count(*) FROM ref_shot WHERE pack_id=? AND confirmed=0", (pack_id,)).fetchone()[0]:
                    raise Rejected("还有未确认的镜头")
                db.execute("UPDATE ref_pack SET state='saved' WHERE id=?", (pack_id,))
                return dict(packId=pack_id, state="saved")
            return self._command(db, command_id, "save_pack", dict(p=pack_id), run)

    def pack_is_current(self, db, pack_id):
        pack = row(db, "SELECT * FROM ref_pack WHERE id=?", pack_id)
        newer = db.execute("SELECT 1 FROM ref_source WHERE replaces_id=?", (pack["source_id"],)).fetchone()
        later = db.execute("SELECT 1 FROM ref_pack WHERE lineage=? AND version>?", (pack["lineage"], pack["version"])).fetchone()
        return not newer and not later

    def _pack_text(self, db, pack_id):
        p = self.pack_view(pack_id)
        lines = [f"参考包 {pack_id} v{p['version']} · 原片 {p['source']['name']} sha256={p['source']['sha256']}"]
        lines += [f"{s['code']} {s['start_ms']}–{s['end_ms']}ms 机位:{s['camera'] or '未知'} 运动:{s['motion'] or '未知'} "
                  f"声音:{s['sound'] or '未知'} 叙事:{s['narrative'] or '未知'}" for s in p["shots"]]
        lines += [f"节拍 {b['code']}（{'、'.join(b['shot_codes'])}）：{b['event']}；观众新知道：{b['info_gain']}" for b in p["beats"]]
        return "\n".join(lines)

    def _shot_ref_version(self, db, vid, segment):
        r = row(db, "SELECT v.content,v.artifact_id,a.episode_id FROM version v JOIN artifact a ON a.id=v.artifact_id "
                    "WHERE v.id=? AND a.stage='prompts'", vid)
        content = json.loads(r["content"])
        if segment not in content.get("segments", {}):
            raise Rejected("该提示词版本没有所选段")
        plan = self._adopted_plan(db,r["episode_id"])
        return r,content,plan

    def shot_reference_view(self, version_id, segment):
        with connect(self.db_path) as db:
            r, content, plan = self._shot_ref_version(db,version_id,segment)
            if not shotrefs.plan_enabled(plan):
                return dict(enabled=False)
            try:
                packet = shotrefs.segment_packet(self,db,r["episode_id"],plan,self._cast_matrix(db,r["episode_id"]),
                                                 segment,content["segments"][segment], draft=True)
                suggested = shotrefs.suggestions(packet["files"].__getitem__,packet["manifest"])[segment]
                return dict(enabled=True, packet=packet, suggestions=suggested)
            except (ValueError, KeyError, TypeError) as exc:
                raise Rejected(str(exc)) from exc

    def save_shot_observation(self, version_id, segment, asset_version_id, expected_sha256, shots, observed,
                              prominent_props, conflicts, combined=False, other_uses=None, command_id=None, draft=None):
        args = dict(version=version_id,segment=segment,asset=asset_version_id,sha=expected_sha256,shots=shots,
                    observed=observed,props=prominent_props,conflicts=conflicts,combined=combined,other=other_uses)
        if draft is not None:
            args["draft"] = draft
        with transaction(self.db_path) as db:
            def run():
                r,content,plan = self._shot_ref_version(db,version_id,segment)
                content["segments"][segment] = promptrefs.edited_entry(content["segments"][segment], draft)
                if not shotrefs.plan_enabled(plan):
                    raise Rejected("请先在采用的制作方案中填写逐镜出镜与道具")
                image = self.asset_version(asset_version_id,db)
                ref = next((ref for ref in content["segments"][segment]["refs"] if ref["assetVersionId"]==asset_version_id),None)
                if not ref or image["kind"]!="image" or expected_sha256!=image["sha256"]:
                    raise Rejected("当前提示词引用的图片与所看原图不一致")
                if not isinstance(observed,str) or not observed.strip() or not isinstance(conflicts,list) or any(
                    not isinstance(c,str) or not c.strip() for c in conflicts):
                    raise Rejected("请填写实际看图观察，冲突须逐条说明")
                if not isinstance(prominent_props,dict) or any(not re.fullmatch("[a-z][a-z0-9-]*",k) or
                       not isinstance(v,str) or not v.strip() for k,v in prominent_props.items()):
                    raise Rejected("显眼道具须逐项填写道具ID和状态")
                if type(combined) is not bool or (other_uses is not None and (not isinstance(other_uses,dict)
                        or any(not isinstance(v,str) or not v.strip() for v in other_uses.values()))):
                    raise Rejected("组合图的其他用途须逐镜填写道具ID")
                details = dict(sha256=image["sha256"],observed=observed.strip(),prominent_props=prominent_props,
                               conflicts=conflicts,combined=combined)
                if other_uses:
                    details["other_uses"] = other_uses
                ident = new_id()
                db.execute("INSERT INTO shot_image_observation VALUES(?,?,?,?,?)",(ident,r["episode_id"],segment,asset_version_id,dumps(details)))
                ref.update(visualReviewId=ident, shots=shots)
                try:
                    packet = shotrefs.segment_packet(self,db,r["episode_id"],plan,self._cast_matrix(db,r["episode_id"]),
                                                     segment,content["segments"][segment], draft=True)
                    if not isinstance(shots,list) or not shots or any(not isinstance(k,str) or k not in packet["windowShots"] for k in shots) or len(set(shots))!=len(shots):
                        raise ValueError("请选择实际作用镜头")
                except (ValueError, TypeError, KeyError) as exc:
                    raise Rejected(str(exc)) from exc
                vid = self._new_version(db,r["artifact_id"],version_id,"manual",content,note="登记实际看图观察与作用镜头")
                return dict(versionId=vid,observationId=ident)
            return self._command(db,command_id,"shot_image_observation",args,run)

    def draft_prompts(self, episode_id):
        from .promptdraft import draft
        return draft(self, episode_id)

    def acting_review(self, version_id):
        return self.preview_acting(version_id, {})

    def preview_acting(self, version_id, changes):
        with connect(self.db_path) as db:
            v = row(db, "SELECT v.content FROM version v JOIN artifact a ON a.id=v.artifact_id "
                        "WHERE v.id=? AND a.stage='plan'", version_id)
        plan = json.loads(v["content"])
        shots = {s["id"]: s for s in plan["shots"]}
        if not isinstance(changes, dict) or changes.keys() - shots.keys():
            raise Rejected("表演调整须引用本版方案中的镜号")
        for sid, edit in changes.items():
            if not isinstance(edit, dict) or edit.keys() - {"performance", "reaction", "sound", "dialogueCues"}:
                raise Rejected("表演调整字段无效")
            shots[sid].update(edit)
        return acting.report(plan)

    def rhythm_review(self, version_id):
        with connect(self.db_path) as db:
            version=row(db,"SELECT a.stage,a.episode_id,v.content FROM version v JOIN artifact a ON a.id=v.artifact_id WHERE v.id=?",version_id)
            stage=version["stage"]
            if stage not in ("prompts","plan"):
                raise Rejected("请选择制作方案或提示词版本")
            eid=version["episode_id"]
            content=json.loads(version["content"])
            plan=content if stage=="plan" else self._adopted_plan(db,eid)
            prompts=content if stage=="prompts" else self._adopted_content(db,eid,"prompts")
        result=dict(markdown="",errors=[],report=None,legacy=[],hasPrompts=bool(prompts))
        if plan:
            try:
                result["boardRows"] = rhythm.plan_table(plan)
                result["markdown"]=plan.get("rhythmTable") or rhythm.markdown(rhythm.plan_rows(plan))
                if prompts:
                    checked=rhythm.check(plan,prompts.get("segments",{}))
                    result.update(report=checked["report"],legacy=checked["legacy"])
            except (KeyError,ValueError,TypeError) as exc:
                result["errors"].append(str(exc))
        return result

    def _stage_reference_entries(self, episode_id, stage):
        extra = []
        for ref in self.episode_project_sections(episode_id, stage):
            extra.append(dict(role="citation", name=ref["title"], text=dumps(ref)))
        config = self.step_config(episode_id, stage)
        if config:
            from .enhancements import entries, SCOPE
            if config.get("enhancements"):
                extra.append(dict(role="stage_contract", name="增强方法适用范围", text=SCOPE))
                extra.extend(entries(config["enhancements"]))
            extra.append(dict(role="step_requirements", name="本步需求", text=dumps({k:v for k,v in config.items() if k != "references"})))
            for ref in config["references"]:
                extra.append(dict(role="citation", name=ref["title"], text=dumps({k:v for k,v in ref.items() if k != "metadata"})))
        if stage == "prompts":
            for card in self.episode_palettes(episode_id, "prompts")["cards"]:
                extra.append(dict(role="visual_reference", name=card["name"], text=dumps(stage_card(card, "prompts"))))
        return extra

    def _extra_creation_inputs(self, db, task):
        extra = self._source_entries(db, task["episode_id"], task["stage"])
        if task["stage"] == "prompts":
            rules = imagechoices.requirements(db, task["episode_id"])
            if rules:
                extra.append(dict(role="required_images", name="必用图与目标段.json", text=dumps(rules)))
            records = self.product_placements(task["episode_id"])["records"]
            if records:
                extra.append(dict(role="product_placement", name="本集商品与镜头用途.json",
                                  text=dumps([dict(code=p["code"],name=p["product"]["name"],shot=p["shot_id"],
                                                   segment=p["segment"],intent=p["intent"],source=p["product"]["source"],
                                                   sha256=p["product"]["sha256"]) for p in records if p["segment"]])))
        extra += self._stage_reference_entries(task["episode_id"], task["stage"])
        if task["stage"] == "story":
            pictures = db.execute("SELECT v.sha256 FROM asset a JOIN asset_version v ON v.asset_id=a.id "
                                  "WHERE a.project_id=? AND a.episode_id=? AND a.origin='kickoff_reference' "
                                  "AND v.version_no=1 ORDER BY a.rowid", (task["project_id"], task["episode_id"]))
            for i, picture in enumerate(pictures, 1):
                extra.append(image_attachment(f"开工参考{i}", self.store.read_blob(picture["sha256"])))
            manuscript = self._episode_manuscript(db, task["episode_id"])
            if manuscript:
                extra.append(dict(role="citation", name="导演导入的本集稿件 · "+manuscript["original_name"],
                                  text=dumps(dict(importId=manuscript["id"], sha256=manuscript["sha256"],
                                                  filename=manuscript["original_name"], text=manuscript["parsed_text"]))))
            for (pack_id,) in db.execute("SELECT pack_id FROM task_pack WHERE task_id=?", (task["id"],)) \
                    if self._has_table(db, "task_pack") else []:
                extra.append(dict(role="reference_pack", name=f"pack-{pack_id}.txt", text=self._pack_text(db, pack_id)))
                units = [dict(code=b["code"],original=b["event"]) for b in db.execute(
                    "SELECT code,event FROM beat WHERE pack_id=? ORDER BY code",(pack_id,))]
                if not units:
                    units = [dict(code=s["code"],original=s["narrative"] or "叙事作用待判断") for s in db.execute(
                        "SELECT code,narrative FROM ref_shot WHERE pack_id=? ORDER BY start_ms",(pack_id,))]
                extra.append(dict(role="adaptation_source",name=f"adaptation-{pack_id}.json",
                                  text=dumps(dict(pack=pack_id,units=units))))
        for c in db.execute("SELECT k.title,k.body,k.source,c.reason FROM citation c JOIN knowledge k ON k.id=c.knowledge_id "
                            "WHERE c.task_id=?", (task["id"],)):
            extra.append(dict(role="citation", name=c["title"], text=f"来源：{c['source']}\n理由：{c['reason']}\n{c['body']}"))
        if task["stage"] == "plan":
            bookmarks = self.episode_bookmarks(task["episode_id"])
            extra.append(dict(role="bookmarks", name="bookmarks.json", text=dumps(bookmarks)))
        if task["stage"] == "publish":
            basis = self._publish_basis(db, task["episode_id"])
            extra.append(dict(role="publish_basis", name="本集发布与音乐对位依据.json", text=dumps(basis)))
            extra.append(dict(role="publish_settings", name="publish-settings.json",
                              text=dumps(self.publish_settings(task["project_id"]) or {})))
        if task["stage"] == "prompts":
            plan = self._adopted_content(db, task["episode_id"], "plan") or {}
            if any(shot.get("dialogueCues") for shot in plan.get("shots", [])):
                extra.append(dict(role="dialogue_timing", name="台词读秒.json", text=dumps(acting.report(plan))))
            assets = [dict(assetVersionId=a["id"], name=a["name"], kind=a["kind"], category=a["category"],
                           grid=bool(a["grid"]), marks=a["marks"]) for a in self.episode_assets(task["episode_id"])]
            extra.append(dict(role="episode_assets", name="assets.json", text=dumps(assets)))
            extra.append(dict(role="cast_bindings", name="cast.json", text=dumps(self.cast_matrix(task["episode_id"]))))
            binding = db.execute("SELECT include_images FROM prompt_draft_task WHERE task_id=?", (task["id"],)).fetchone()
            if binding:
                scaffold = self.draft_prompts(task["episode_id"])["segments"]
                extra.append(dict(role="prompt_scaffold", name="本集逐段参考与时间骨架.json", text=dumps(scaffold)))
                extra.append(dict(role="shot_rhythm",name="镜头充分性联合表.json",text=dumps(rhythm.plan_table(plan))))
                if binding[0]:
                    index = {a["id"]:a for a in self.episode_assets(task["episode_id"])}
                    seen = set()
                    for segment in scaffold.values():
                        for ref in segment["refs"]:
                            vid = ref["assetVersionId"]
                            if vid not in seen and index[vid]["kind"] == "image":
                                seen.add(vid)
                                extra.append(image_attachment(vid, self.store.read_blob(index[vid]["sha256"])))
            downstream = [dict(s, refs=[r for r in s.get("refs", []) if r.get("mode") != "D"]) for s in plan.get("shots", [])]
            extra.append(dict(role="plan_layout", name="layout.json",
                              text=json.dumps(dict(layout=timeline.layout(plan), shots=downstream,
                                                   rhythmTable=plan.get("rhythmTable","")), ensure_ascii=False)))
        return extra

    def _extra_review_inputs(self, db, task, version):
        entries = self._source_entries(db, task["episode_id"], task["stage"])
        if task["stage"] == "story":
            for source in self._version_materials(db,version,{"citation","step_requirements","reference_pack","adaptation_source"}):
                entries.append(dict(role=source["role"],name=source["name"],text=source["text"]))
        return entries

    def _has_table(self, db, name):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    def adapt_from_pack(self, episode_id, pack_id, request, command_id):
        """从节拍写故事: a story task whose inputs include the fixed pack version."""
        payload = dict(episode=episode_id,pack=pack_id,request=request)
        with transaction(self.db_path) as db:
            def start():
                pack = row(db,"SELECT * FROM ref_pack WHERE id=?",pack_id)
                ep = self._episode_in(db,episode_id,pack["project_id"])
                if pack["episode_id"] not in (None,episode_id):
                    raise Rejected("整份参考包属于另一集，可在本集收藏单个拍法")
                # Older clients recorded only the preliminary create_task command. Recover its
                # already frozen, pack-backed run on replay, never start a second adaptation.
                legacy = self._existing_command(db,command_id+":task","create_task",
                    dict(episode_id=episode_id,stage="story",goal=request,auto=False,ex=None,sel=None))
                if legacy:
                    for run in db.execute("SELECT id,manifest_relpath FROM run WHERE task_id=? AND purpose='create' "
                                          "ORDER BY created_at,rowid",(legacy["taskId"],)):
                        manifest,_ = self.store.load_manifest(run["manifest_relpath"])
                        if any(i["role"]=="reference_pack" and i["name"]==f"pack-{pack_id}.txt" for i in manifest["inputs"]):
                            return dict(legacy,runId=run["id"])
                    raise Conflict("旧改编命令缺少对应的参考包运行，请核对任务记录")
                if pack["state"] != "saved":
                    raise Rejected("参考包尚未保存")
                if not self.pack_is_current(db,pack_id):
                    raise Rejected("原片或切点已更新，旧参考包证据不能用于新稿")
                kickoff = self._kickoff(db,episode_id)
                aid = row(db,"SELECT id FROM artifact WHERE episode_id=? AND stage='story'",episode_id)["id"]
                tid,thread = self._draft_thread(db,ep["project_id"],episode_id,aid,"story",request,kickoff["executor_plan"])
                db.execute("INSERT INTO task_pack VALUES(?,?)",(tid,pack_id))
                rid = self._queue_run(db,tid,"create",None,request)
                return dict(taskId=tid,threadId=thread,runId=rid,artifactId=aid)
            return self._command(db,command_id,"adapt_from_pack",payload,start)

    def check_plan_refs(self, episode_id, plan: dict) -> list[str]:
        problems = []
        with connect(self.db_path) as db:
            for shot in plan.get("shots", []):
                refs = shot.get("refs") or []
                if not refs and not shot.get("origin"):
                    problems.append(f"{shot['id']}：原创镜需关联剧本出处")
                for ref in refs:
                    if ref.get("mode") not in ("A", "B", "C", "D"):
                        problems.append(f"{shot['id']}：参考用法须为 A/B/C/D")
                    bm = db.execute("SELECT * FROM shot_bookmark WHERE id=?", (ref.get("bookmarkId"),)).fetchone()
                    if not bm:
                        problems.append(f"{shot['id']}：参考收藏不存在")
                    elif bm["episode_id"] != episode_id:
                        problems.append(f"{shot['id']}：不能直接引用其他集的参考收藏")
                    elif not self.pack_is_current(db, bm["pack_id"]):
                        problems.append(f"{shot['id']}：参考包已有新版本，需确认新边界后更新引用")
        return problems

    def trace_shot(self, episode_id, shot_id):
        """Reverse lookup: plan shot → bookmark → ref shot → pack version / source, or the script line."""
        with connect(self.db_path) as db:
            plan = self._adopted_content(db, episode_id, "plan") or {}
            shot = next((s for s in plan.get("shots", []) if s["id"] == shot_id), None)
            if not shot:
                raise NotFound("镜头不存在")
            refs = []
            for ref in shot.get("refs", []):
                bm = row(db, "SELECT * FROM shot_bookmark WHERE id=?", ref["bookmarkId"])
                rs = row(db, "SELECT * FROM ref_shot WHERE id=?", bm["ref_shot_id"])
                pack = row(db, "SELECT * FROM ref_pack WHERE id=?", bm["pack_id"])
                src = row(db, "SELECT name,sha256 FROM ref_source WHERE id=?", pack["source_id"])
                refs.append(dict(mode=ref["mode"], refShot=rs["code"], start_ms=rs["start_ms"], end_ms=rs["end_ms"],
                                 packVersion=pack["version"], source=src, reason=bm["reason"]))
        return dict(shot=shot, refs=refs, origin=shot.get("origin"))

    # ============================================================ space and cast
    def _check_lock_image(self, db, episode_id, version_id):
        ep = self._episode_in(db, episode_id)
        if not version_id:
            raise Rejected("请选择已登记的空间锁定图")
        image = self.asset_version(version_id, db)
        if image["kind"] != "image" or image["project_id"] != ep["project_id"] or image["episode_id"] not in (None, episode_id):
            raise Rejected("空间锁定图须为本项目、本集可用的图像版本")

    def space_locks(self, episode_id):
        with connect(self.db_path) as db:
            return self._space_locks(db, episode_id)

    def _space_locks(self, db, episode_id):
        rows = db.execute("SELECT s.*,v.sha256,a.name,v.version_no FROM space_item s "
                          "JOIN episode e ON e.id=s.episode_id LEFT JOIN asset_version v ON v.id=s.lock_asset_version_id "
                          "LEFT JOIN asset a ON a.id=v.asset_id AND a.project_id=e.project_id AND a.kind='image' "
                          "AND (a.episode_id=e.id OR a.episode_id IS NULL) WHERE s.episode_id=? ORDER BY s.created_at,s.id", (episode_id,))
        return [dict(id=r["id"],label=r["label"],relation=r["relation"],segments=json.loads(r["depends"]),status=r["status"],
                     versionId=r["lock_asset_version_id"] if r["name"] else None, name=r["name"],
                     version=r["version_no"] if r["name"] else None, sha256=r["sha256"] if r["name"] else None) for r in rows]

    def add_space_item(self, episode_id, label, relation, source, depends, status="pending", count=None,
                       lock_asset_version_id=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                self._episode_in(db, episode_id)
                if not isinstance(depends, list) or any(not isinstance(s, str) or not s.strip() for s in depends):
                    raise Rejected("空间依赖段须为段号列表")
                if status not in ("confirmed", "pending"):
                    raise Rejected("空间锁定状态无效")
                if lock_asset_version_id or status == "confirmed":
                    self._check_lock_image(db, episode_id, lock_asset_version_id)
                sid = new_id()
                db.execute("INSERT INTO space_item VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (sid, episode_id, label, relation, count, source, lock_asset_version_id, status,
                            dumps(depends), self.clock()))
                return dict(itemId=sid)
            payload = dict(e=episode_id, l=label, r=relation, d=depends)
            if lock_asset_version_id or status != "pending" or count is not None:
                payload.update(lock=lock_asset_version_id, status=status, count=count)
            return self._command(db, command_id, "space_add", payload, run)

    def confirm_space_item(self, item_id, lock_asset_version_id, relation=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                item = row(db, "SELECT * FROM space_item WHERE id=?", item_id)
                self._check_lock_image(db, item["episode_id"], lock_asset_version_id)
                db.execute("UPDATE space_item SET status='confirmed', lock_asset_version_id=?, relation=coalesce(?,relation) "
                           "WHERE id=?", (lock_asset_version_id, relation, item_id))
                ep = row(db, "SELECT * FROM episode WHERE id=?", item["episode_id"])
                items = [dict(target=f"prompts:{s}", reason=f"空间锁定「{item['label']}」已确认新锁定图，需复核")
                         for s in json.loads(item["depends"])]
                self._record_impacts(db, dict(project_id=ep["project_id"], episode_id=ep["id"]), items, "space_lock",
                                     f"space:{item_id}")
                return dict(itemId=item_id, recheck=[i["target"] for i in items])
            payload = dict(i=item_id, a=lock_asset_version_id)
            if relation is not None:
                payload["relation"] = relation
            return self._command(db, command_id, "space_confirm", payload, run)

    def blocked_segments(self, episode_id) -> dict:
        with connect(self.db_path) as db:
            out = {}
            for r in db.execute("SELECT label,depends FROM space_item WHERE episode_id=? AND status='pending'", (episode_id,)):
                for seg in json.loads(r["depends"]):
                    out.setdefault(seg, []).append(f"空间锁定待确认：{r['label']}")
        return out

    def set_cast(self, episode_id, segment_id, role, status, asset_version_id=None, cells=(), note="", command_id=None, opening_confirmed=False):
        if not isinstance(role, str) or not role.strip() or status not in ("ok","absent","unclear"):
            raise Rejected("请填写角色及有效的出镜状态")
        role = role.strip()
        if type(opening_confirmed) is not bool or not isinstance(note, str):
            raise Rejected("开场核对须为布尔值，对照观察须为文字")
        if not isinstance(cells, (tuple, list)) or any(type(i) != int or not 1 <= i <= 9 for i in cells) or len(cells) != len(set(cells)):
            raise Rejected("格位须为 1 至 9 的不重复整数")
        if status == "absent" and (asset_version_id or cells or opening_confirmed):
            raise Rejected("未出镜角色不能绑定身份图或格位")
        with transaction(self.db_path) as db:
            def run():
                self._episode_in(db, episode_id)
                plan = self._adopted_content(db, episode_id, "plan") or {}
                if segment_id not in {s["id"] for s in plan.get("segments", [])}:
                    raise Rejected("请选用已采用制作方案中的生成段")
                if cells and not asset_version_id:
                    raise Rejected("选择格位前须选择九宫格版本")
                if opening_confirmed and not asset_version_id:
                    raise Rejected("确认开场前须选择本段状态图或九宫格")
                if status == "ok" and not asset_version_id:
                    raise Rejected("请明确选择证明本段角色身份的图片版本")
                if asset_version_id:
                    av = self.asset_version(asset_version_id, db)
                    if av["episode_id"] != episode_id or av["kind"] != "image":
                        raise Rejected("角色身份必须引用本集的图片版本")
                    if cells and not av["grid"]:
                        raise Rejected("格位只适用于九宫格版本")
                    if status == "ok" and not opening_confirmed:
                        prior = db.execute("SELECT segment_id FROM cast_opening WHERE episode_id=? AND sha256=?",
                                           (episode_id,av["sha256"])).fetchone()
                        if prior and prior[0] != segment_id:
                            raise Rejected(f"这张状态图已登记为 {prior[0]} 的开场，请核对所选版本")
                    if opening_confirmed:
                        if av["category"] not in ("state","grid") or not note.strip():
                            raise Rejected("核对本段开场须选状态图或九宫格并填写实际观察")
                        # A role's neutral portrait can span the episode. A picture
                        # of a specific opening belongs to that segment, even when re-imported.
                        other = db.execute("SELECT c.segment_id FROM cast_cell c JOIN asset_version v ON v.id=c.asset_version_id "
                                           "WHERE c.episode_id=? AND c.segment_id<>? AND v.sha256=? AND c.status='ok'",
                                           (episode_id,segment_id,av["sha256"])).fetchall()
                        active = {s["id"] for s in plan["segments"]}
                        if any(r[0] in active for r in other):
                            raise Rejected("同一张含角色的状态图不能登记为不同生成段的开场")
                        db.execute("INSERT INTO cast_opening VALUES(?,?,?) ON CONFLICT(episode_id,sha256) DO UPDATE SET "
                                   "segment_id=excluded.segment_id", (episode_id,av["sha256"],segment_id))
                    if status == "ok" and av["category"] == "identity":
                        prod = self.project_role_assets(av["project_id"])
                        baseline = next((item for item in prod["roles"] if item["id"].casefold() == role.casefold()
                                         or (item.get("name") or "").casefold() == role.casefold()), None)
                        if baseline and av["sha256"] != baseline["sha256"]:
                            raise Rejected("固定角色不匹配现行角色基准，请选择对应身份原图")
                db.execute("INSERT INTO cast_cell VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(episode_id,segment_id,role) DO UPDATE "
                           "SET asset_version_id=excluded.asset_version_id, cells=excluded.cells, status=excluded.status, "
                           "note=excluded.note", (new_id(), episode_id, segment_id, role, asset_version_id, dumps(list(cells)),
                                                  status, note))
                return dict(ok=True)
            payload = dict(e=episode_id, s=segment_id, r=role, st=status, a=asset_version_id, c=list(cells))
            if note:
                payload["note"] = note
            if opening_confirmed:
                payload["opening"] = True
            return self._command(db, command_id, "cast", payload, run)

    def set_role_baseline(self, episode_id, role, asset_version_id, appearance, command_id):
        if not isinstance(role, str) or not role.strip() or not isinstance(appearance, str) or not appearance.strip():
            raise Rejected("请填写角色和可用于核对的外观短描述")
        role, appearance = role.strip(), appearance.strip()
        with connect(self.db_path) as db:
            project = self._episode_in(db, episode_id)["project_id"]
        fixed = self.project_role_assets(project)["roles"]
        with transaction(self.db_path) as db:
            def run():
                asset = self.asset_version(asset_version_id, db)
                if asset["episode_id"] != episode_id or asset["kind"] != "image" or asset["category"] != "identity" or asset["grid"]:
                    raise Rejected("请选择本集的单角色身份原图版本")
                for item in fixed:
                    matches_role = role.casefold() in {item["id"].casefold(), (item.get("name") or "").casefold()}
                    if matches_role != (asset["sha256"] == item["sha256"]):
                        raise Rejected("固定角色不匹配现行角色基准")
                if db.execute("SELECT 1 FROM episode_role_baseline b JOIN asset_version a ON a.id=b.asset_version_id "
                              "WHERE b.episode_id=? AND b.role<>? AND a.sha256=?", (episode_id,role,asset["sha256"])).fetchone():
                    raise Rejected("两个角色不能共用同一张身份原图")
                db.execute("INSERT INTO episode_role_baseline VALUES(?,?,?,?) ON CONFLICT(episode_id,role) DO UPDATE SET "
                           "asset_version_id=excluded.asset_version_id,appearance=excluded.appearance",
                           (episode_id,role,asset_version_id,appearance))
                return dict(role=role, assetVersionId=asset_version_id, appearance=appearance)
            return self._command(db, command_id, "set_role_baseline", [episode_id,role,asset_version_id,appearance], run)

    def cast_matrix(self, episode_id):
        with connect(self.db_path) as db:
            return self._cast_matrix(db, episode_id)

    def _cast_matrix(self, db, episode_id):
        plan = self._adopted_content(db, episode_id, "plan") or {}
        story = self._adopted_content(db, episode_id,"story") or {}
        cells = [loads(dict(r), "cells") for r in db.execute(
            "SELECT c.*,o.segment_id AS opening FROM cast_cell c LEFT JOIN asset_version v ON v.id=c.asset_version_id "
            "LEFT JOIN cast_opening o ON o.episode_id=c.episode_id AND o.sha256=v.sha256 WHERE c.episode_id=?", (episode_id,))]
        baselines = db.execute("SELECT b.role,b.asset_version_id,b.appearance,a.sha256 FROM episode_role_baseline b "
                               "JOIN asset_version a ON a.id=b.asset_version_id WHERE b.episode_id=?", (episode_id,)).fetchall()
        lay = timeline.layout(plan) if plan else dict(segments=[])
        from .storycheck import cast_records
        try:
            roster = cast_records(story.get("cast",""))
        except ValueError:
            roster = {}
        for b in baselines:
            roster.setdefault(b["role"], {}).update(assetVersionId=b["asset_version_id"], appearance=b["appearance"], sha256=b["sha256"])
        active_segments = {s["id"] for s in lay["segments"]}
        for c in cells:
            if c["segment_id"] in active_segments:
                roster.setdefault(c["role"], {})
        roles = sorted(roster)
        matrix = []
        for seg in lay["segments"]:
            row_ = dict(segment=seg["id"], strategy=seg["strategy"], cells={})
            for c in cells:
                if c["segment_id"] == seg["id"]:
                    entry = dict(status=c["status"], assetVersionId=c["asset_version_id"], cells=c["cells"], note=c["note"])
                    if c["opening"]:
                        entry["depictsMoment"] = dict(segment=c["opening"], at="start")
                    if c["status"] == "unclear":
                        entry["options"] = (["按身份参考修格", "改用 flex"] if seg["strategy"] == "grid"
                                            else ["补身份参考", "改动作让身份可见"])
                    row_["cells"][c["role"]] = entry
            matrix.append(row_)
        return dict(roles=roles, roster=roster, rows=matrix)

    def _cast_prompt_errors(self, db, episode_id, segment, entry):
        rows = db.execute(
            "SELECT b.role,b.asset_version_id AS baseline,b.appearance,c.status,c.note,c.asset_version_id AS bound,"
            "a.category,o.segment_id AS opening FROM episode_role_baseline b LEFT JOIN cast_cell c ON c.episode_id=b.episode_id "
            "AND c.role=b.role AND c.segment_id=? LEFT JOIN asset_version v ON v.id=c.asset_version_id "
            "LEFT JOIN asset a ON a.id=v.asset_id LEFT JOIN cast_opening o ON o.episode_id=b.episode_id AND o.sha256=v.sha256 "
            "WHERE b.episode_id=?", (segment, episode_id))
        errors = []
        uploaded = {r["assetVersionId"] for r in entry["refs"] if isinstance(r, dict) and isinstance(r.get("assetVersionId"), str)}
        for r in rows:
            prefix = segment + " · " + r["role"] + "："
            if r["status"] == "absent":
                continue
            if r["status"] != "ok":
                errors.append(prefix + "请核对本段身份或明确标记未出镜")
                continue
            if r["bound"] not in uploaded:
                errors.append(prefix + "身份来源未列入本段实际上传")
            if r["bound"] != r["baseline"]:
                if r["category"] not in ("state", "grid") or not (r["note"] or "").strip():
                    errors.append(prefix + "使用其他图片须选本段状态图或九宫格并填写与基准的对照观察")
                elif r["opening"] != segment:
                    errors.append(prefix + "身份来源尚未核对为本段开场")
            if r["appearance"] not in (entry.get("txt") or ""):
                errors.append(prefix + "提示词缺少已登记的本集外观短描述")
        return errors

    # ============================================================ prompts: refs and sound
    def edit_prompt_reference(self, version_id, segment_id, action, command_id, asset_id=None,
                              previous_id=None, purpose=None, draft=None):
        payload = dict(version=version_id, segment=segment_id, action=action, asset=asset_id,
                       previous=previous_id, purpose=purpose, draft=draft)
        required_ids = {"add": (True, False), "replace": (True, True), "remove": (False, True)}
        if required_ids.get(action) != (bool(asset_id), bool(previous_id)):
            raise Rejected("请选择本段要添加、替换或移出的参考")
        # Media probing may take seconds. Hold a write lock only while publishing the new version.
        with connect(self.db_path) as db:
            prior = self._existing_command(db, command_id, "edit_prompt_reference", payload)
            if prior is not None:
                return prior
            version = loads(row(db, "SELECT v.*,a.episode_id FROM version v JOIN artifact a ON a.id=v.artifact_id "
                                "WHERE v.id=? AND a.stage='prompts'", version_id), "content")
            eid = version["episode_id"]
            plan = self._adopted_plan(db, eid) or {}
            segment = next((s for s in plan.get("segments", []) if s["id"] == segment_id), None)
            entries = version["content"]["segments"]
            if not segment or segment_id not in entries:
                raise Rejected("提示词与已采用方案须包含该生成段")
            entry = promptrefs.edited_entry(entries[segment_id], draft)
            refs = [dict(r) for r in entry["refs"]]
            grid = segment["strategy"] == "grid"
            if grid and action != "replace":
                raise Rejected("九宫格段只能替换本段九宫格，其他段可增减或替换参考")
            old = next((r for r in refs if r["assetVersionId"] == previous_id), None)
            if action != "add" and old is None:
                raise Rejected("原参考不在本段")
            if action == "remove":
                if old["kind"] == "image" and sum(r["kind"] == "image" for r in refs) <= 1:
                    raise Rejected("本段须至少保留一张图片")
                refs.remove(old)
                entry["txt"] = promptrefs.without_reference(entry, old, refs)
            else:
                asset = self.asset_version(asset_id, db)
                kind = asset["kind"]
                if (asset["episode_id"] != eid or kind not in stages.KIND_LABEL
                        or (grid and not asset["grid"]) or (old and old["kind"] != kind)):
                    raise Rejected("须选择本集同类型参考，九宫格段只能使用九宫格")
                peers = [self.asset_version(r["assetVersionId"], db) for r in refs if r is not old]
                if any(a["sha256"] == asset["sha256"] for a in peers):
                    raise Rejected("本段已引用相同文件内容")
                matching = [a for a in peers if a["kind"] == kind]
                if len(peers) >= 12 or len(matching) >= {"image":9, "video":1, "audio":3}[kind]:
                    raise Rejected("本段最多 12 份参考，其中图片 9 张、视频 1 段、音频 3 段")
                if kind != "image":
                    try:
                        duration = sum(h3export.media_duration(self.store.blob_path(a["sha256"]), kind, a["name"])
                                       for a in matching + [asset])
                    except ValueError as exc:
                        raise Rejected(str(exc)) from exc
                    if duration > 15:
                        raise Rejected("本段同类型音视频参考的总时长须在 15 秒以内")
                if old is None:
                    if not isinstance(purpose, str) or not purpose.strip() or len(purpose.strip().splitlines()) != 1:
                        raise Rejected("请用一行填写参考用途")
                    ref = dict(assetVersionId=asset_id, kind=kind, role=purpose.strip(), grid=bool(asset["grid"]))
                    refs.append(ref)
                    ref["label"] = stages.expected_labels(refs)[-1]
                    entry["txt"] = promptrefs.add_definition(entry, ref["label"], ref["role"])
                else:
                    old.update(assetVersionId=asset_id, grid=bool(asset["grid"]))
                    old.pop("visualReviewId", None)
            entries[segment_id] = dict(entry, refs=refs)
        with transaction(self.db_path) as db:
            def save():
                if self._adopted_plan(db, eid) != plan:
                    raise Conflict("制作方案已变更，请重新核对本段")
                vid = self._new_version(db, version["artifact_id"], version_id, "manual", version["content"],
                                        note=f"调整 {segment_id} 参考素材")
                return dict(versionId=vid)
            return self._command(db, command_id, "edit_prompt_reference", payload, save)

    def reorder_refs(self, version_id, segment_id, new_order: list[str], command_id, confirm=False, draft=None):
        """Reorder references; labels follow asset identity and the TXT is remapped. Preview unless confirm."""
        with transaction(self.db_path) as db:
            v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
            entry = v["content"]["segments"][segment_id]
            entry = promptrefs.edited_entry(entry, draft)
            by_id = {r["assetVersionId"]: r for r in entry["refs"]}
            if sorted(new_order) != sorted(by_id):
                raise Rejected("新顺序须包含全部且仅包含原有参考")
            refs = [dict(by_id[i]) for i in new_order]
            txt, mapping = promptrefs.renumber(refs, entry["txt"])
            unclear = [r["label"] for r in refs if not r.get("role")]
            preview = dict(mapping=mapping, txtDiff=line_diff(entry["txt"], txt), unclearRoles=unclear)
            if not confirm:
                return dict(preview=preview)
            def run():
                content = json.loads(json.dumps(v["content"]))
                content["segments"][segment_id] = dict(entry, refs=refs, txt=txt)
                vid = self._new_version(db, v["artifact_id"], version_id, "manual", content, note=f"重排 {segment_id} 参考")
                return dict(versionId=vid, preview=preview)
            return self._command(db, command_id, "reorder", dict(v=version_id, s=segment_id, o=new_order, **({"draft":draft} if draft is not None else {})), run)

    def set_sound(self, version_id, segment_id, change: dict, command_id, confirm=False, draft=None):
        """change: {bgm: bool} | {silent: true}. Full silence lists conflicts first."""
        if not isinstance(change, dict) or ("bgm" in change) == ("silent" in change):
            raise Rejected("请指定一个声音开关")
        if type(confirm) is not bool or any(type(change[k]) is not bool for k in ("bgm", "silent") if k in change):
            raise Rejected("声音开关须为布尔值")
        with transaction(self.db_path) as db:
            v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
            entry = promptrefs.edited_entry(v["content"]["segments"][segment_id], draft)
            sound = dict(entry.get("sound") or {})
            structured = entry.get("prompt_format") == "readable_v3"
            strategy = "grid" if entry["txt"].lstrip().startswith(readable.GRID[0]) else "flex"
            conflicts = []
            retained = None
            if change.get("silent"):
                if sound.get("dialogue"):
                    conflicts.append(dict(item="对白", detail=sound["dialogue"]))
                if sound.get("ambience"):
                    conflicts.append(dict(item="环境声", detail=sound["ambience"]))
                if structured:
                    heading = "声音与音乐：" if strategy == "grid" else "【声音与文字】"
                    body = readable.regions(entry["txt"], strategy).get(heading, "")
                    if body.strip():
                        conflicts.append(dict(item="原提示词声音栏", detail=body.strip()))
                    eid = row(db, "SELECT episode_id FROM artifact WHERE id=?", v["artifact_id"])["episode_id"]
                    sources = self._source_entries(db, eid, "prompts")
                    rules = next((json.loads(r["text"])["blocks"] for r in sources if r["role"] == "promptStandard"), {})
                    fixed = [block["zh"] for name, block in rules.items()
                             if name in ("cats_silent", "frame_text") and block.get("zh") and block["zh"] in body]
                    retained = change.get("retained_notes", "\n".join(fixed))
                    if not isinstance(retained, str) or any(line not in retained for line in fixed):
                        raise Rejected("静音后仍须保留本段已有的项目固定说明")
                    cues = re.findall(r"<d>[^<]+</d>", entry["txt"], re.I)
                    if cues:
                        conflicts.append(dict(item="正文台词（采用前需复核）", detail=cues))
                if self._episode_audio_on(db, v["artifact_id"]):
                    conflicts.append(dict(item="打包参数", detail="本集打包声音开关为开"))
                if conflicts and not confirm:
                    return dict(needsConfirm=True, conflicts=conflicts, retainedNotes=retained)
                sound = dict(silent=True, bgm=False, dialogue=[], ambience=[])
            elif "silent" in change:
                sound["silent"] = False
            if "bgm" in change:
                sound["bgm"] = change["bgm"]   # dialogue and ambience are untouched
                sound["silent"] = False
            def run():
                content = json.loads(json.dumps(v["content"]))
                updated = dict(entry, sound=sound)
                if structured:
                    settings = entry.get("generation_settings")
                    if isinstance(settings, dict) and "audio" in settings:
                        if sound.get("silent") or sound.get("bgm") or (entry.get("sound") or {}).get("silent"):
                            updated["generation_settings"] = dict(settings, audio=not sound.get("silent"))
                    music = change.get("music_description")
                    if sound.get("bgm") and "bgm" not in change:
                        match = readable.MUSIC_LINE.search(entry["txt"])
                        music = match[1] if match else None
                    if sound.get("bgm") and (not isinstance(music, str) or not music.strip()
                                            or music.strip() == "N/A" or len(music.strip().splitlines()) != 1):
                        raise Rejected("请填写本段具体配乐描述")
                    try:
                        txt = readable.edit_sound_fields(entry["txt"], strategy,
                                                         music.strip() if sound.get("bgm") else "N/A", retained)
                        readable.check_h3_sound(dict(id=segment_id, prompt_format="readable_v3", reference_strategy=strategy,
                                                     native_bgm="generate" if sound.get("bgm") else "none",
                                                     full_silence=sound.get("silent", False)), txt)
                        readable.sections(txt, strategy)
                        updated["txt"] = txt
                    except ValueError as exc:
                        raise Rejected(str(exc)) from exc
                content["segments"][segment_id] = updated
                vid = self._new_version(db, v["artifact_id"], version_id, "manual", content, note=f"{segment_id} 声音")
                return dict(versionId=vid, sound=sound, conflicts=conflicts)
            args = dict(v=version_id, s=segment_id, c=change, k=confirm)
            if draft is not None:
                args["draft"] = draft
            return self._command(db, command_id, "sound", args, run)

    def _episode_audio_on(self, db, artifact_id):
        ep = db.execute("SELECT episode_id FROM artifact WHERE id=?", (artifact_id,)).fetchone()[0]
        spec = json.loads(db.execute("SELECT spec FROM kickoff WHERE episode_id=?", (ep,)).fetchone()[0])
        return spec.get("audio", True)

    # ============================================================ adoption preview (采用前影响)
    def adoption_preview(self, version_id):
        with connect(self.db_path) as db:
            v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
            art = row(db, "SELECT * FROM artifact WHERE id=?", v["artifact_id"])
            old = self._adopted_content(db, art["episode_id"], art["stage"])
            plan = self._adopted_content(db, art["episode_id"], "plan") or {}
            prompts_ = self._adopted_content(db, art["episode_id"], "prompts") or {}
            downstream_adopted = {s: bool(self._adopted_content(db, art["episode_id"], s))
                                  for s in ("script", "plan", "prompts", "publish")}
        items, kept = [], []
        if art["stage"] == "plan":
            pre = timeline.preview(old, v["content"])
            items += [dict(target=f"生成段 {s}", reason="镜头或时长变化，TXT 需复核", kind="recheck") for s in pre["changedSegments"]]
            items += [dict(target=f"{a} → {b} 接点", reason="相邻接点复核", kind="recheck") for a, b in pre["joints"]]
            items += [dict(target=f"{x['segment']} 起点", reason=f"{x['startBefore'] / 1000:g} → {x['startAfter'] / 1000:g} 秒",
                           kind="auto") for x in pre["shifted"]]
            if pre["totalBefore"] is not None and pre["totalBefore"] != pre["totalAfter"]:
                items.append(dict(target="全片时长", reason=f"{pre['totalBefore'] / 1000:g} → {pre['totalAfter'] / 1000:g} 秒",
                                  kind="auto"))
            items += [dict(target=d["target"], reason=d["reason"], kind="recheck") for d in pre["downstream"]
                      if not d["target"].startswith(("提示词", "关键帧"))]
            kept = [s["id"] for s in timeline.layout(v["content"])["segments"] if s["id"] not in pre["changedSegments"]]
        elif art["stage"] == "script" and old:
            olds = {s["id"]: s for s in old.get("scenes", [])}
            changed = {s["id"] for s in v["content"].get("scenes", []) if olds.get(s["id"]) != s}
            for sh in plan.get("shots", []):
                if sh.get("origin", {}).get("scene") in changed:
                    items.append(dict(target=f"制作方案 {sh['id']}", reason=f"引用场次 {sh['origin']['scene']}", kind="recheck"))
                    if sh["segment"] in (prompts_.get("segments") or {}):
                        items.append(dict(target=f"提示词 {sh['segment']}", reason=f"含镜头 {sh['id']}", kind="recheck"))
            kept = sorted(set(olds) - changed)
        elif art["stage"] == "prompts" and old:
            for seg, e in (v["content"].get("segments") or {}).items():
                if (old.get("segments") or {}).get(seg) != e:
                    items.append(dict(target=f"两遍独审 {seg}", reason="TXT 或参考变化，需重审该段及接点", kind="recheck"))
        elif art["stage"] == "story" and downstream_adopted["script"]:
            items.append(dict(target="分场剧本", reason="故事变化，按实际依赖复核", kind="recheck"))
        dedup = []
        for it in items:
            if it not in dedup:
                dedup.append(it)
        return dict(stage=art["stage"], items=dedup, unaffected=kept)

    # ============================================================ frames for 拉片 cards and cut review (real ffmpeg)
    def ref_frames(self, pack_id, around_ms=None):
        """Midpoint frame per shot, or frames 500 ms before/after a candidate cut. Cached as blobs."""
        with connect(self.db_path) as db:
            pack = row(db, "SELECT * FROM ref_pack WHERE id=?", pack_id)
            src = row(db, "SELECT * FROM ref_source WHERE id=?", pack["source_id"])
            shots = [dict(r) for r in db.execute("SELECT code,start_ms,end_ms FROM ref_shot WHERE pack_id=? ORDER BY start_ms",
                                                 (pack_id,))]
        path = self.store.root / src["relpath"]
        points = ({"before": max(0, around_ms - 500), "after": around_ms + 500} if around_ms is not None
                  else {s["code"]: (s["start_ms"] + s["end_ms"]) // 2 for s in shots})
        out = {}
        for key, ms in points.items():
            proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{ms / 1000}", "-i", str(path), "-frames:v", "1",
                                   "-vf", "scale=240:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True,
                                  timeout=60)
            out[key] = dict(ms=ms, sha256=self.store.put_blob(proc.stdout) if proc.returncode == 0 and proc.stdout else None)
        return out

    def episode_bookmarks(self, episode_id):
        with connect(self.db_path) as db:
            return [dict(r) for r in db.execute(
                "SELECT b.*, s.code AS shot_code, p.version AS pack_version FROM shot_bookmark b JOIN ref_shot s ON s.id=b.ref_shot_id "
                "JOIN ref_pack p ON p.id=b.pack_id WHERE b.episode_id=? ORDER BY b.created_at", (episode_id,))]

    def open_stage(self, episode_id, stage):
        """Open the next stage once its upstream is adopted (also after a plain 「只采用」)."""
        from .stages import NEXT
        prev = next((k for k, v in NEXT.items() if v == stage), None)
        with transaction(self.db_path) as db:
            ep = row(db, "SELECT * FROM episode WHERE id=?", episode_id)
            if prev and not self._adopted_content(db, episode_id, prev):
                raise Rejected(f"上一步（{prev}）尚未采用")
            return dict(artifactId=self._ensure_artifact(db, ep, stage))

    # ============================================================ 拉片问答：这一刻是不是切镜
    def ask_cut(self, pack_id, at_ms, question, provider, command_id=None):
        """The executor looks at the frames 500 ms before/after `at_ms` and explains. Evidence frames are stored."""
        from . import contracts
        from .executors.base import ExecRequest
        self.ask_cut_precheck(pack_id, at_ms, question, provider)
        frames = self.ref_frames(pack_id, around_ms=at_ms)
        if not all(f["sha256"] for f in frames.values()):
            raise Rejected("取不到该时点的画面")
        pv = self.pack_view(pack_id)
        qid = new_id()
        text = (f"参考片 {pv['source']['name']}（参考包 v{pv['version']}）。导演问：{question}\n"
                f"附两张图：图1 为 {frames['before']['ms']} 毫秒，图2 为 {frames['after']['ms']} 毫秒。"
                "只根据这两张图判断 {at} 毫秒处是切镜（cut）、镜内动作（in_shot）还是无法确定（uncertain），"
                "说明依据，evidence 逐条写看到的画面及其毫秒时点。").replace("{at}", str(at_ms))
        digest = sha256_bytes(dumps(dict(q=text, f=[frames["before"]["sha256"], frames["after"]["sha256"]])).encode())
        with transaction(self.db_path) as db:
            db.execute("INSERT INTO ref_question VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (qid, pack_id, at_ms, question, provider, "running", None, None, None, dumps(frames), digest, None,
                        self.clock()))
        workdir = self.store.root / "runs" / f"ask-{qid}"
        workdir.mkdir(parents=True, exist_ok=True)
        images = []
        for k in ("before", "after"):
            path = workdir / f"{k}.png"
            path.write_bytes(self.store.read_blob(frames[k]["sha256"]))
            images.append(path)
        trailer = f"\n运行号：{qid}\n输入指纹：{digest}\n回包为 kind=cut_explanation，atMs 填 {at_ms}。"
        outcome = self.executors[provider].start(ExecRequest(
            run_id=qid, purpose="ask", kind="cut_explanation", prompt=text + trailer,
            schema=contracts.branch_schema("cut_explanation"), session_mode="ephemeral", workdir=workdir, images=images,
            timeout=int(self.get_setting("executor_timeout", 600))))
        res = outcome.structured if outcome.ok else None
        problems = contracts.validate(res) if res else [outcome.error_class or "failed"]
        if res and not problems and (res.get("kind") != "cut_explanation" or res["runId"] != qid
                                     or res["inputFingerprint"] != digest or res["atMs"] != at_ms):
            problems = ["回包不属于本次提问"]
        with transaction(self.db_path) as db:
            if problems:
                db.execute("UPDATE ref_question SET state=?, diagnostic=? WHERE id=?",
                           ("waiting_quota" if outcome.error_class == "quota" else "failed", "; ".join(problems)[:300], qid))
            else:
                db.execute("UPDATE ref_question SET state='answered', judgement=?, reason=?, evidence=? WHERE id=?",
                           (res["judgement"], res["reason"], dumps(res["evidence"]), qid))
        return dict(questionId=qid)

    def ask_cut_precheck(self, pack_id, at_ms, question, provider, command_id=None):
        """Synchronous checks, so the HTTP layer can refuse before handing the run to the worker pool."""
        if provider not in self.executors:
            raise Rejected("未配置的执行器")
        if not getattr(self.executors[provider], "reads_images", True):
            raise Rejected(f"{provider} 在当前隔离配置下不能读图，请选择能读图的执行器")
        if not str(question).strip():
            raise Rejected("先写下要问的问题")
        self.pack_view(pack_id)

    def ref_questions(self, pack_id):
        with connect(self.db_path) as db:
            return [loads(dict(r), "evidence", "frames") for r in db.execute(
                "SELECT * FROM ref_question WHERE pack_id=? ORDER BY created_at DESC", (pack_id,))]
