"""Adopt individual project rules and opt an in-progress episode into the changed block."""
import json
import re

from .baselineview import block_by_key, replace_block, segment_use, source_blocks
from .db import new_id, transaction
from .errors import Conflict, Rejected, row
from .storage import canonical, sha256_bytes


class BaselineRevisionMixin:
    def _episode_block_history(self, db, eid, source):
        previous = source["sha256"]
        changes = []
        for rec in db.execute("""SELECT b.block_key,c.sha256 FROM episode_baseline_change c
                JOIN baseline_revision b ON b.id=c.revision_id WHERE c.episode_id=? AND b.kind=? ORDER BY c.rowid""",
                              (eid,source["kind"])):
            key = rec["block_key"]
            before, after = [block_by_key(source["kind"], self.store.read_blob(digest), key)
                             for digest in (previous,rec["sha256"])]
            changes.append((key,before,after))
            previous = rec["sha256"]
        return changes

    def _prompt_version_sources(self, db, vid, eid):
        frozen = db.execute("SELECT sources FROM version_baseline_source WHERE version_id=?", (vid,)).fetchone()
        if frozen:
            return json.loads(frozen[0])
        return {r[0]:r[1] for r in db.execute("SELECT kind,sha256 FROM episode_source WHERE episode_id=? AND kind IN ('promptStandard','characterStandard','soundStandard')", (eid,))}

    def _record_prompt_sources(self, db, vid, eid, run_id, restored_from):
        if restored_from:
            sources = self._prompt_version_sources(db,restored_from,eid)
        elif run_id:
            path = row(db,"SELECT manifest_relpath FROM run WHERE id=?",run_id)["manifest_relpath"]
            manifest,_ = self.store.load_manifest(path)
            sources = {i["role"]:i["sha256"] for i in manifest["inputs"] if i["role"] in ("promptStandard","characterStandard","soundStandard")}
        else:
            sources = {r["kind"]:sha256_bytes(self._effective_bytes(db,eid,r)) for r in db.execute(
                "SELECT * FROM episode_source WHERE episode_id=? AND kind IN ('promptStandard','characterStandard','soundStandard')", (eid,))}
        db.execute("INSERT INTO version_baseline_source VALUES(?,?)",(vid,json.dumps(sources)))

    def _effective_bytes(self, db, eid, source):
        last = db.execute("""SELECT c.sha256 FROM episode_baseline_change c
            JOIN baseline_revision b ON b.id=c.revision_id WHERE c.episode_id=? AND b.kind=?
            ORDER BY c.rowid DESC LIMIT 1""", (eid, source["kind"])).fetchone()
        return self.store.read_blob(last[0] if last else source["sha256"])

    def _cast_names(self, db, eid):
        source = db.execute("SELECT * FROM episode_source WHERE episode_id=? AND kind='characterStandard'", (eid,)).fetchone()
        if not source:
            return {}
        return {k: v.get("name", k) for k,v in json.loads(self._effective_bytes(db, eid, source)).get("roles", {}).items()}

    def _baseline_impact(self, db, eid, key, before, after):
        plan = self._adopted_content(db, eid, "plan") or {}
        prompts = self._adopted_content(db, eid, "prompts") or {}
        names = self._cast_names(db, eid)
        cast = {}
        for sid, role in db.execute("SELECT segment_id,role FROM cast_cell WHERE episode_id=? AND status='ok'", (eid,)):
            cast.setdefault(sid, set()).add(role.casefold())
        result = []
        for segment in plan.get("segments", []):
            sid = segment["id"]
            entry = prompts.get("segments", {}).get(sid, {})
            use = segment_use(key, before, after, segment, entry, cast.get(sid, set()), names)
            if use:
                result.append(dict(segment=sid, reason=use, hasPrompts=bool(entry)))
        return result

    def _revision_blocks(self, rec):
        return [block_by_key(rec["kind"], self.store.read_blob(rec[field]), rec["block_key"])
                for field in ("base_sha256", "sha256")]

    def _review_baselines(self, segment, entry, cells, names, sources):
        """Replay only applicable changes over the kickoff files used by older review hashes."""
        present = {role.casefold() for role, cell in cells.items() if cell["status"] == "ok"}
        result = []
        for source, changes in sources:
            data = self.store.read_blob(source["sha256"])
            for key, old, new in changes:
                if segment_use(key, old["text"], new["text"], segment, entry, present, names):
                    data = replace_block(source["kind"], data, key, new["text"], new.get("version"))
            result.append(dict(role=source["kind"], name=source["relpath"], text=data.decode("utf-8")))
        return result

    def revise_baseline(self, project_id, kind, block_key, text, expected_sha, expected_revision,
                        preview_hash=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                found, revision = self._current_baseline(db, project_id, kind)
                if not found or expected_sha != sha256_bytes(found[1]) or expected_revision != revision:
                    raise Conflict("制作基准已变动，请重新读取原文并比较")
                path, data = found
                old = block_by_key(kind, data, block_key)
                if not isinstance(text, str) or not text.strip() or text == old["text"]:
                    raise Rejected("请填写与现行版本不同的修订正文")
                match = re.fullmatch(r"v(\d+)", str(old.get("version", "")))
                version = f"v{int(match[1])+1}" if match else f"R{revision+1}"
                changed = replace_block(kind, data, block_key, text, version)
                impacted = []
                for episode in db.execute("""SELECT e.id,e.display_code,s.kind,s.sha256 FROM episode_source s
                      JOIN episode e ON e.id=s.episode_id WHERE e.project_id=? AND s.kind=? AND e.state='in_production'
                      ORDER BY e.display_code""", (project_id,kind)):
                    local = self._effective_bytes(db, episode["id"], episode)
                    local_block = next((b for b in source_blocks(kind, local) if b["key"] == block_key), None)
                    if local_block and local_block["text"] != text:
                        impacted.append(dict(episodeId=episode["id"], code=episode["display_code"],
                            segments=self._baseline_impact(db, episode["id"], block_key, local_block["text"], text)))
                digest = sha256_bytes(changed)
                preview = dict(projectId=project_id, kind=kind, key=block_key, path=path, before=old["text"], after=text,
                               revision=revision+1, baseSha256=expected_sha, sha256=digest, episodes=impacted)
                stamp = sha256_bytes(canonical(preview))
                if preview_hash is None:
                    return dict(preview, previewHash=stamp)
                if preview_hash != stamp:
                    raise Conflict("修订或引用情况已改变，请重新比较")
                rid = new_id()
                db.execute("INSERT INTO baseline_revision VALUES(?,?,?,?,?,?,?,?,?)",
                           (rid, project_id, kind, revision+1, block_key, path, self.store.put_blob(data),
                            self.store.put_blob(changed), self.clock()))
                return dict(revisionId=rid, revision=revision+1, sha256=digest)
            args = [project_id,kind,block_key,text,expected_sha,expected_revision,preview_hash]
            return self._command(db,command_id if preview_hash else None,"revise_baseline",args,run)

    def follow_baseline(self, project_id, episode_id, revision_id, preview_hash=None, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                episode = self._episode_in(db,episode_id,project_id)
                if episode["state"] != "in_production":
                    raise Rejected("只能为在制集选用基准修订")
                rec = row(db,"SELECT * FROM baseline_revision WHERE id=? AND project_id=?",revision_id,project_id)
                kind, key = rec["kind"], rec["block_key"]
                latest = row(db,"SELECT id FROM baseline_revision WHERE project_id=? AND kind=? AND block_key=? ORDER BY revision DESC LIMIT 1",
                             project_id,kind,key)
                if latest["id"] != revision_id:
                    raise Conflict("这个固定块已有更新的项目修订")
                source = row(db,"SELECT * FROM episode_source WHERE episode_id=? AND kind=?",episode_id,kind)
                data = self._effective_bytes(db,episode_id,source)
                before = block_by_key(kind,data,key)
                _, after = self._revision_blocks(rec)
                if before["text"] == after["text"] and before.get("version") == after.get("version"):
                    raise Rejected("本集已使用此固定块")
                if db.execute("SELECT 1 FROM episode_baseline_change WHERE episode_id=? AND revision_id=?", (episode_id, revision_id)).fetchone():
                    raise Conflict("本集已采用此修订")
                changed = replace_block(kind,data,key,after["text"],after.get("version"))
                impact = self._baseline_impact(db,episode_id,key,before["text"],after["text"])
                preview = dict(revisionId=revision_id, episodeId=episode_id, before=before["text"], after=after["text"],
                               baseSha256=sha256_bytes(data), sha256=sha256_bytes(changed), segments=impact)
                stamp = sha256_bytes(canonical(preview))
                if preview_hash is None:
                    return dict(preview, previewHash=stamp)
                if preview_hash != stamp:
                    raise Conflict("本集基准或引用范围已变化，请重新核对")
                db.execute("INSERT INTO episode_baseline_change VALUES(?,?,?)",(episode_id,revision_id,self.store.put_blob(changed)))
                return preview
            return self._command(db,command_id if preview_hash else None,"follow_baseline",[project_id,episode_id,revision_id,preview_hash],run)
