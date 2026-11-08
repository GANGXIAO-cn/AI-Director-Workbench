"""Project-owned Markdown method editions and the editions selected for each writing stage."""
import difflib
import json
from pathlib import Path

from .db import connect, dumps, new_id, transaction
from .errors import Conflict, Rejected, row
from .storage import sha256_bytes

STAGES = ("story", "script", "plan", "prompts", "publish")
MAX_BYTES = 256 * 1024


def binding_key(scope, stage):
    return f"uploaded-methods:{scope}:{stage}"


def selection(db, scope, stage):
    found = db.execute("SELECT value FROM app_setting WHERE key=?", (binding_key(scope, stage),)).fetchone()
    return json.loads(found[0]) if found else []


def store_selection(db, scope, stage, ids):
    db.execute("INSERT INTO app_setting VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (binding_key(scope, stage), dumps(ids)))


def pin_at_kickoff(db, project_id, episode_id):
    for stage in STAGES:
        ids = selection(db, project_id, stage)
        if ids:
            store_selection(db, episode_id, stage, ids)


def edition(db, project, version):
    return dict(row(db, "SELECT v.*,m.title FROM uploaded_method_revision v JOIN uploaded_method m "
                    "ON v.method_id=m.id WHERE v.id=? AND m.project_id=?", version, project,
                    missing="在本项目中找不到这个方法版本"))


class CustomMethodsMixin:
    def import_custom_method(self, project_id, filename, data, title="", method_id=None, expected_revision=None):
        if not isinstance(filename, str) or "\\" in filename or not filename.isprintable() or Path(filename).name != filename or Path(filename).suffix.lower() != ".md":
            raise Rejected("请选择 Markdown 技能文件（SKILL.md）")
        if len(data) > MAX_BYTES:
            raise Rejected("单份技能文件上限为 256 KiB")
        try:
            body = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise Rejected("技能文件须采用 UTF-8 编码") from exc
        if not body.strip() or "\0" in body:
            raise Rejected("技能文件缺少可读的 Markdown 正文")
        digest = sha256_bytes(data)
        with transaction(self.db_path) as db:
            row(db, "SELECT id FROM project WHERE id=?", project_id)
            if method_id:
                latest = row(db, "SELECT v.* FROM uploaded_method m JOIN uploaded_method_revision v "
                             "ON m.id=v.method_id WHERE m.id=? AND m.project_id=? ORDER BY v.number DESC LIMIT 1",
                             method_id, project_id)
                if latest["sha256"] == digest and latest["filename"] == filename:
                    return dict(methodId=method_id, versionId=latest["id"], number=latest["number"])
                if expected_revision != latest["id"]:
                    raise Conflict("此方法已有更新版本，请刷新后对比")
                number = latest["number"] + 1
            else:
                if not isinstance(title, str) or not title.strip() or len(title.strip()) > 100 or not title.strip().isprintable():
                    raise Rejected("请填写 1 至 100 个字符的技能名称")
                title = title.strip()
                existing = db.execute("SELECT id FROM uploaded_method WHERE project_id=? AND title=?",
                                      (project_id, title)).fetchone()
                if existing:
                    first = edition(db, project_id, db.execute("SELECT id FROM uploaded_method_revision "
                                    "WHERE method_id=? AND number=1", (existing[0],)).fetchone()[0])
                    if first["sha256"] == digest and first["filename"] == filename:
                        return dict(methodId=existing[0], versionId=first["id"], number=1)
                    raise Conflict("同名技能已存在，可在该技能下上传新版")
                method_id, number = new_id(), 1
                db.execute("INSERT INTO uploaded_method VALUES(?,?,?)", (method_id, project_id, title))
            self.store.put_blob(data)
            if self.store.read_blob(digest) != data:
                raise Rejected("无法核对上传原文")
            version = new_id()
            db.execute("INSERT INTO uploaded_method_revision VALUES(?,?,?,?,?,?)",
                       (version, method_id, number, filename, digest, self.clock()))
            return dict(methodId=method_id, versionId=version, number=number)

    def custom_methods(self, project_id, stage=None, episode_id=None):
        with connect(self.db_path) as db:
            row(db, "SELECT id FROM project WHERE id=?", project_id)
            if episode_id:
                self._episode_in(db, episode_id, project_id)
            if stage is not None and stage not in STAGES:
                raise Rejected("请选择可写作的阶段")
            methods = []
            for m in db.execute("SELECT id,title FROM uploaded_method WHERE project_id=? ORDER BY title,id", (project_id,)):
                versions = [dict(v) for v in db.execute("SELECT id,number,filename,sha256,created_at FROM uploaded_method_revision "
                            "WHERE method_id=? ORDER BY number DESC", (m["id"],))]
                methods.append(dict(m, versions=versions))
            return dict(methods=methods, selected=selection(db, episode_id or project_id, stage) if stage else [])

    def custom_method_text(self, project_id, version_id, compare_to=None):
        with connect(self.db_path) as db:
            current = edition(db, project_id, version_id)
            before = edition(db, project_id, compare_to) if compare_to else None
            if before and before["method_id"] != current["method_id"]:
                raise Rejected("请选择同一技能的两个版本")
        text = self._uploaded_text(current)
        difference = None
        if before:
            diff = difflib.unified_diff(self._uploaded_text(before).splitlines(keepends=True),
                                        text.splitlines(keepends=True), fromfile=f"R{before['number']}", tofile=f"R{current['number']}")
            difference = "".join(line if line.endswith(("\n","\r")) else line + "\n\\ No newline at end of file\n" for line in diff)
        return dict(**current, text=text, difference=difference)

    def set_custom_methods(self, project_id, stage, versions, expected, episode_id=None):
        if stage not in STAGES or not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
            raise Rejected("请选择有效的阶段与方法版本")
        with transaction(self.db_path) as db:
            row(db, "SELECT id FROM project WHERE id=?", project_id)
            if episode_id:
                self._episode_in(db, episode_id, project_id)
                self._kickoff(db, episode_id)
            chosen = [edition(db, project_id, v)["method_id"] for v in versions]
            if len(set(chosen)) != len(chosen):
                raise Rejected("同一技能在本阶段只能启用一个版本")
            scope = episode_id or project_id
            current = selection(db, scope, stage)
            if versions != current:
                if current != expected:
                    raise Conflict("阶段方法已更改，请刷新后再保存")
                store_selection(db, scope, stage, versions)
        return dict(selected=versions)

    def _uploaded_text(self, version):
        try:
            return self.store.read_blob(version["sha256"]).decode("utf-8")
        except (OSError, ValueError) as exc:
            raise Rejected("无法核对上传技能的原文：" + version["title"]) from exc

    def _custom_method_entries(self, db, task):
        entries = []
        for vid in selection(db, task["episode_id"], task["stage"]):
            v = edition(db, task["project_id"], vid)
            source = dict(path=v["filename"], methodId=v["method_id"], versionId=vid, revision=v["number"])
            entries.append(dict(role="project_method", name=f"上传技能 · {v['title']} · R{v['number']}",
                                text=self._uploaded_text(v), methodSource=dict(path=v["filename"], sha256=v["sha256"], source=source)))
        return entries
