"""Production mixin: threads, compare/combine, plan timing, reference films, shot refs, space, cast, prompts."""
import difflib
import json
from pathlib import Path
import re
import shutil
import subprocess

from . import stages, timeline
from .db import connect, dumps, new_id, transaction
from .errors import Conflict, NotFound, Rejected, loads, row
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


def ffmpeg_cuts(path: Path, start_ms: int, end_ms: int, threshold=0.3) -> list[int]:
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
                    f = fields or {}
                    unknown = sorted(set(json.loads(shot["unknown_fields"])) | {k for k in ("camera", "motion", "sound", "narrative")
                                                                              if f.get(k) in (None, "", "未知")})
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

    def _extra_creation_inputs(self, db, task):
        k = db.execute("SELECT topic_id FROM kickoff WHERE episode_id=?", (task["episode_id"],)).fetchone()
        extra = []
        if task["stage"] == "story":
            for (pack_id,) in db.execute("SELECT pack_id FROM task_pack WHERE task_id=?", (task["id"],)) \
                    if self._has_table(db, "task_pack") else []:
                extra.append(dict(role="reference_pack", name=f"pack-{pack_id}.txt", text=self._pack_text(db, pack_id)))
        for c in db.execute("SELECT k.title,k.body,k.source,c.reason FROM citation c JOIN knowledge k ON k.id=c.knowledge_id "
                            "WHERE c.task_id=?", (task["id"],)):
            extra.append(dict(role="citation", name=c["title"], text=f"来源：{c['source']}\n理由：{c['reason']}\n{c['body']}"))
        if task["stage"] == "prompts":
            plan = self._adopted_content(db, task["episode_id"], "plan") or {}
            downstream = [dict(s, refs=[r for r in s.get("refs", []) if r.get("mode") != "D"]) for s in plan.get("shots", [])]
            extra.append(dict(role="plan_layout", name="layout.json",
                              text=json.dumps(dict(layout=timeline.layout(plan), shots=downstream), ensure_ascii=False)))
        return extra

    def _extra_review_inputs(self, db, task, version):
        if task["stage"] != "story" or not self._has_table(db, "task_pack"):
            return []
        return [dict(role="reference_pack", name=f"pack-{p}.txt", text=self._pack_text(db, p))
                for (p,) in db.execute("SELECT pack_id FROM task_pack WHERE task_id=?", (task["id"],))]

    def _has_table(self, db, name):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    def adapt_from_pack(self, episode_id, pack_id, request, command_id):
        """从节拍写故事: a story task whose inputs include the fixed pack version."""
        with transaction(self.db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS task_pack(task_id TEXT NOT NULL, pack_id TEXT NOT NULL, "
                       "PRIMARY KEY(task_id, pack_id))")
        with connect(self.db_path) as db:
            pack = row(db, "SELECT * FROM ref_pack WHERE id=?", pack_id)
            if pack["state"] != "saved":
                raise Rejected("参考包尚未保存")
            if not self.pack_is_current(db, pack_id):
                raise Rejected("原片或切点已更新，旧参考包证据不能用于新稿")
            if pack["episode_id"] not in (None, episode_id):
                raise Rejected("跨集引用参考包须先在本集收藏")
        res = self.create_task(episode_id, "story", request, command_id=command_id + ":task")
        with transaction(self.db_path) as db:
            db.execute("INSERT OR IGNORE INTO task_pack VALUES(?,?)", (res["taskId"], pack_id))
            db.execute("DELETE FROM run WHERE id=? AND status='queued'", (res["runId"],))
            run_id = self._queue_run(db, res["taskId"], "create", None, request)
        res["runId"] = run_id
        return res

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
    def add_space_item(self, episode_id, label, relation, source, depends, status="pending", count=None,
                       lock_asset_version_id=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                self._episode_in(db, episode_id)
                sid = new_id()
                db.execute("INSERT INTO space_item VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (sid, episode_id, label, relation, count, source, lock_asset_version_id, status,
                            dumps(depends), self.clock()))
                return dict(itemId=sid)
            return self._command(db, command_id, "space_add", dict(e=episode_id, l=label, r=relation, d=depends), run)

    def confirm_space_item(self, item_id, lock_asset_version_id, relation=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                item = row(db, "SELECT * FROM space_item WHERE id=?", item_id)
                db.execute("UPDATE space_item SET status='confirmed', lock_asset_version_id=?, relation=coalesce(?,relation) "
                           "WHERE id=?", (lock_asset_version_id, relation, item_id))
                ep = row(db, "SELECT * FROM episode WHERE id=?", item["episode_id"])
                items = [dict(target=f"prompts:{s}", reason=f"空间锁定「{item['label']}」已确认新锁定图，需复核")
                         for s in json.loads(item["depends"])]
                self._record_impacts(db, dict(project_id=ep["project_id"], episode_id=ep["id"]), items, "space_lock",
                                     f"space:{item_id}")
                return dict(itemId=item_id, recheck=[i["target"] for i in items])
            return self._command(db, command_id, "space_confirm", dict(i=item_id, a=lock_asset_version_id), run)

    def blocked_segments(self, episode_id) -> dict:
        with connect(self.db_path) as db:
            out = {}
            for r in db.execute("SELECT label,depends FROM space_item WHERE episode_id=? AND status='pending'", (episode_id,)):
                for seg in json.loads(r["depends"]):
                    out.setdefault(seg, []).append(f"空间锁定待确认：{r['label']}")
        return out

    def set_cast(self, episode_id, segment_id, role, status, asset_version_id=None, cells=(), note="", command_id=None):
        with transaction(self.db_path) as db:
            def run():
                self._episode_in(db, episode_id)
                db.execute("INSERT INTO cast_cell VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(episode_id,segment_id,role) DO UPDATE "
                           "SET asset_version_id=excluded.asset_version_id, cells=excluded.cells, status=excluded.status, "
                           "note=excluded.note", (new_id(), episode_id, segment_id, role, asset_version_id, dumps(list(cells)),
                                                  status, note))
                return dict(ok=True)
            return self._command(db, command_id, "cast", dict(e=episode_id, s=segment_id, r=role, st=status,
                                                              a=asset_version_id, c=list(cells)), run)

    def cast_matrix(self, episode_id):
        with connect(self.db_path) as db:
            plan = self._adopted_content(db, episode_id, "plan") or {}
            cells = [loads(dict(r), "cells") for r in db.execute("SELECT * FROM cast_cell WHERE episode_id=?", (episode_id,))]
        lay = timeline.layout(plan) if plan else dict(segments=[])
        roles = sorted({c["role"] for c in cells})
        matrix = []
        for seg in lay["segments"]:
            row_ = dict(segment=seg["id"], strategy=seg["strategy"], cells={})
            for c in cells:
                if c["segment_id"] == seg["id"]:
                    entry = dict(status=c["status"], assetVersionId=c["asset_version_id"], cells=c["cells"], note=c["note"])
                    if c["status"] == "unclear":
                        entry["options"] = (["按身份参考修格", "改用 flex"] if seg["strategy"] == "grid"
                                            else ["补身份参考", "改动作让身份可见"])
                    row_["cells"][c["role"]] = entry
            matrix.append(row_)
        return dict(roles=roles, rows=matrix)

    # ============================================================ prompts: refs and sound
    def reorder_refs(self, version_id, segment_id, new_order: list[str], command_id, confirm=False):
        """Reorder references; labels follow asset identity and the TXT is remapped. Preview unless confirm."""
        with transaction(self.db_path) as db:
            v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
            entry = v["content"]["segments"][segment_id]
            by_id = {r["assetVersionId"]: r for r in entry["refs"]}
            if sorted(new_order) != sorted(by_id):
                raise Rejected("新顺序须包含全部且仅包含原有参考")
            refs = [dict(by_id[i]) for i in new_order]
            labels = stages.expected_labels(refs)
            mapping = {by_id[i]["label"]: lab for i, lab in zip(new_order, labels)}
            for r, lab in zip(refs, labels):
                r["label"] = lab
            txt = re.sub(r"(?:图片|视频|音频)\d+", lambda m: "\0" + mapping.get(m.group(0), m.group(0)) + "\0", entry["txt"])
            txt = txt.replace("\0", "")
            unclear = [r["label"] for r in refs if not r.get("role")]
            preview = dict(mapping=mapping, txtDiff=line_diff(entry["txt"], txt), unclearRoles=unclear)
            if not confirm:
                return dict(preview=preview)
            def run():
                content = json.loads(json.dumps(v["content"]))
                content["segments"][segment_id] = dict(entry, refs=refs, txt=txt)
                vid = self._new_version(db, v["artifact_id"], version_id, "manual", content, note=f"重排 {segment_id} 参考")
                return dict(versionId=vid, preview=preview)
            return self._command(db, command_id, "reorder", dict(v=version_id, s=segment_id, o=new_order), run)

    def set_sound(self, version_id, segment_id, change: dict, command_id, confirm=False):
        """change: {bgm: bool} | {silent: true}. Full silence lists conflicts first."""
        with transaction(self.db_path) as db:
            v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
            entry = v["content"]["segments"][segment_id]
            sound = dict(entry.get("sound") or {})
            conflicts = []
            if change.get("silent"):
                if sound.get("dialogue"):
                    conflicts.append(dict(item="对白", detail=sound["dialogue"]))
                if sound.get("ambience"):
                    conflicts.append(dict(item="环境声", detail=sound["ambience"]))
                if self._episode_audio_on(db, v["artifact_id"]):
                    conflicts.append(dict(item="打包参数", detail="本集打包声音开关为开"))
                if conflicts and not confirm:
                    return dict(needsConfirm=True, conflicts=conflicts)
                sound = dict(silent=True, bgm=False, dialogue=[], ambience=[])
            if "bgm" in change:
                sound["bgm"] = bool(change["bgm"])   # dialogue and ambience are untouched
                sound["silent"] = False
            def run():
                content = json.loads(json.dumps(v["content"]))
                content["segments"][segment_id] = dict(entry, sound=sound)
                vid = self._new_version(db, v["artifact_id"], version_id, "manual", content, note=f"{segment_id} 声音")
                return dict(versionId=vid, sound=sound, conflicts=conflicts)
            return self._command(db, command_id, "sound", dict(v=version_id, s=segment_id, c=change, k=confirm), run)

    def _episode_audio_on(self, db, artifact_id):
        ep = db.execute("SELECT episode_id FROM artifact WHERE id=?", (artifact_id,)).fetchone()[0]
        spec = json.loads(db.execute("SELECT spec FROM kickoff WHERE episode_id=?", (ep,)).fetchone()[0])
        return spec.get("audio", True)
