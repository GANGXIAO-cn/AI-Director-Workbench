"""Build an editable, single-project workbench directory from one SQLite snapshot."""
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import sys

from .db import connect, initialize
from .errors import Rejected, row
from .storage import canonical, file_sha256, sha256_bytes

MARKER = "导演工作台项目.json"
# Tables without a direct project_id or episode_id have one owning parent.
PARENTS = {
    "accepted_image_issue": ("asset_version_id", "asset_version"),
    "repair_run": ("plan_id", "repair_plan"),
    "stress_resolution": ("report_version_id", "version"),
    "asset_need_candidate": ("need_id", "asset_need"),
    "asset_need_request": ("need_id", "asset_need"),
    "grid_cell_binding": ("asset_version_id","asset_version"),
    "stage_edit_session": ("artifact_id","artifact"),
    "free_turn": ("thread_id","free_thread"),
    "import_extraction": ("import_id", "import_record"), "topic_source": ("topic_id","topic"),
    "version": ("artifact_id","artifact"), "thread": ("task_id","task"), "message": ("thread_id","thread"),
    "run": ("task_id","task"), "story_candidate": ("run_id","run"), "event": ("run_id","run"),
    "review": ("version_id","version"), "issue": ("review_id","review"), "waiver": ("issue_id","issue"),
    "prompt_segment_progress": ("run_id","run"), "prompt_segment_result": ("task_id","task"),
    "prompt_draft_task": ("task_id","task"), "historical_archive": ("receipt_id","historical_episode"),
    "version_plan_basis": ("version_id", "version"),
    "version_baseline_source": ("version_id","version"), "version_publish_basis": ("version_id","version"), "version_publish_config": ("version_id","version"), "adoption": ("artifact_id","artifact"),
    "ref_shot": ("pack_id","ref_pack"), "beat": ("pack_id","ref_pack"), "ref_question": ("pack_id","ref_pack"),
    "asset_version": ("asset_id","asset"), "image_request": ("batch_id","image_batch"),
    "required_image_details": ("choice_id","image_choice"), "package": ("delivery_id","delivery"),
    "package_tool": ("package_id","package"), "proposal_item": ("closure_id","closure"),
    "citation": ("task_id","task"), "expert_scope": ("auth_id","expert_auth"),
    "expert_budget_currency": ("auth_id","expert_auth"),
    "expert_batch": ("batch_id","image_batch"), "uploaded_method_revision": ("method_id","uploaded_method"),
    "task_pack": ("task_id","task"),
}


def copy_records(source, dest, pid):
    tables = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    def ownership(table):
        if table in ("topic_intent","topic_publication"):
            return "topic_id IN (SELECT id FROM topic WHERE project_id=?)"
        if table == "project":
            return "id=?"
        fields = {r[1] for r in dest.execute(f'PRAGMA table_info("{table}")')}
        if "project_id" in fields:
            return "project_id=?"
        field, parent = ("episode_id","episode") if "episode_id" in fields else PARENTS[table]
        return f'{field} IN (SELECT id FROM "{parent}" WHERE {ownership(parent)})'

    for table in tables:
        if table in ("app_setting","api_provider","backup","command"):
            continue
        if table == "project_series_file" and not source.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            continue
        rows = source.execute(f'SELECT * FROM "{table}" WHERE {ownership(table)} ORDER BY rowid',(pid,))
        marks = ",".join("?" for _ in rows.description)
        dest.executemany(f'INSERT INTO "{table}" VALUES({marks})', rows)
    episodes = {r[0] for r in dest.execute("SELECT id FROM episode")}
    keys = {f"publish:{pid}", f"palette-selection:{pid}"} | {f"contact:{eid}" for eid in episodes}
    keys.update("decision-dismissed:"+r[0] for r in dest.execute("SELECT id FROM artifact"))
    scoped = {"step","palette","method-options","uploaded-methods"}
    for key, value in source.execute("SELECT key,value FROM app_setting"):
        parts = key.split(":")
        if key in keys or (len(parts)==3 and parts[0] in scoped and
                           (parts[1] in episodes or parts[0]=="uploaded-methods" and parts[1]==pid)):
            dest.execute("INSERT INTO app_setting VALUES(?,?)",(key,value))
    # Keep retry receipts only when their returned identifier or recorded command belongs here.
    owned = set()
    commands = set()
    for table in tables:
        fields = {r[1] for r in dest.execute(f'PRAGMA table_info("{table}")')}
        if "id" in fields and table != "event":
            owned.update(r[0] for r in dest.execute(f'SELECT id FROM "{table}"'))
        if "command_id" in fields and table != "command":
            commands.update(r[0] for r in dest.execute(f'SELECT command_id FROM "{table}" WHERE command_id IS NOT NULL'))
    for rec in source.execute("SELECT * FROM command"):
        result = json.loads(rec["result"])
        refs = [v for k,v in result.items() if k.endswith("Id") or k=="receipt"] if isinstance(result,dict) else []
        if rec["command_id"] in commands or any(isinstance(v,str) and v in owned for v in refs):
            dest.execute("INSERT INTO command VALUES(?,?,?,?,?)", tuple(rec))
    if dest.execute("PRAGMA foreign_key_check").fetchone():
        raise Rejected("项目记录引用了其他项目或缺失的对象，无法生成独立目录")


def copy_materials(source, dest, db):
    files = {}
    palette_ids = set()
    def copy(relative, digest):
        name = Path(relative)
        origin = (source.root / name).resolve()
        if name.is_absolute() or ".." in name.parts or origin != source.root / name:
            raise Rejected(f"项目副本引用越界或为符号链接：{name}")
        if relative in files:
            if files[relative] != digest:
                raise Rejected(f"同一路径登记了不同文件指纹：{name}")
            return
        target = origin
        if dest is not None:
            target = dest / name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(origin,target)
        if file_sha256(target) != digest:
            raise Rejected(f"项目文件指纹不一致：{name}")
        files[relative] = digest

    def registered_json(name, registered_hash):
        path = source._inside(source.root/name)
        raw = path.read_bytes()
        data = json.loads(raw)
        if sha256_bytes(canonical(data)) != registered_hash:
            raise Rejected(f"与登记的内容指纹不符：{name}")
        copy(name,sha256_bytes(raw))
        return data

    def blob(digest):
        copy(source.blob_path(digest).relative_to(source.root).as_posix(),digest)

    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, col in (("asset_version","sha256"),("version","blob_hash"),("topic_source","sha256"),
                       ("episode_source","sha256"),("baseline_revision","base_sha256"),("baseline_revision","sha256"),
                       ("episode_baseline_change","sha256"),("uploaded_method_revision","sha256"),("historical_check","sha256")):
        if table in {"baseline_revision","episode_baseline_change"} and table not in tables:
            continue  # Project folders saved before editable standards.
        for (digest,) in db.execute(f'SELECT DISTINCT "{col}" FROM "{table}"'):
            blob(digest)
    if "project_series_file" in tables:
        for (digest,) in db.execute("SELECT DISTINCT sha256 FROM project_series_file"):
            blob(digest)
    if "episode_story_skill" in tables:
        for (digest,) in db.execute("SELECT snapshot FROM episode_story_skill"):
            blob(digest)
    if "version_baseline_source" in tables:
        for (sources,) in db.execute("SELECT sources FROM version_baseline_source"):
            for digest in json.loads(sources).values():
                blob(digest)
    for table in ("import_record","ref_source","package"):
        for name,digest in db.execute(f'SELECT relpath,sha256 FROM "{table}"'):
            copy(name,digest)
    for (record,) in db.execute("SELECT source FROM episode_product"):
        blob(json.loads(record)["originalSha256"])
    for (frames,) in db.execute("SELECT frames FROM ref_question"):
        for frame in json.loads(frames).values():
            if frame.get("sha256"):
                blob(frame["sha256"])
    for (record,) in db.execute("SELECT files FROM historical_episode"):
        for digest in json.loads(record).values():
            blob(digest)
    for (record,) in db.execute("SELECT metadata FROM historical_archive"):
        meta = json.loads(record)
        for kind in ("fixed","standards","fixedRefs"):
            for item in meta.get(kind,{}).values():
                blob(item["sha256"])
        for digest in meta.get("sceneRefs",{}).values():
            blob(digest)
        from .referencereports import digests
        for digest in digests(meta):
            blob(digest)
    for turn in db.execute("SELECT id,manifest_relpath,input_hash FROM free_turn"):
        manifest = registered_json(turn["manifest_relpath"],turn["input_hash"])
        if manifest["runId"] != turn["id"]:
            raise Rejected("自由对话清单与消息编号不符")
        for item in manifest["inputs"]:
            blob(item["sha256"])
    for run in db.execute("SELECT id,manifest_relpath,input_hash,result_hash FROM run"):
        manifest = registered_json(run["manifest_relpath"],run["input_hash"])
        if run["id"] != manifest["runId"]:
            raise Rejected("运行输入清单与登记编号不符")
        for item in manifest["inputs"]:
            if item.get("binary") and Path(item["name"]).name != item["name"]:
                raise Rejected("运行图像附件名含目录")
            blob(item["sha256"])
            if item["role"]=="visual_reference":
                card = json.loads(source.read_blob(item["sha256"]))
                palette_ids.add(card["id"])
        output = source.run_dir(run["id"])/"result.json"
        output_name = output.relative_to(source.root).as_posix()
        if run["result_hash"]:
            registered_json(output_name,run["result_hash"])
        elif output.is_file():
            copy(output_name,file_sha256(output))
        schema = output.with_name("output-schema.json")
        if schema.is_file():
            copy(schema.relative_to(source.root).as_posix(),file_sha256(schema))
    for name,digest in db.execute("SELECT relpath,manifest_hash FROM delivery"):
        manifest = registered_json(name+"/manifest.json",digest)
        for rel,sha in manifest["files"].items():
            copy((Path(name)/rel).as_posix(),sha)
    for key,value in db.execute("SELECT key,value FROM app_setting WHERE key LIKE 'palette:%' OR key LIKE 'palette-selection:%'"):
        selection = json.loads(value)
        cards = selection if isinstance(selection,list) else selection.get("cards",[])
        palette_ids.update(card["id"] for card in cards)
    for (spec,) in db.execute("SELECT spec FROM image_batch"):
        palette_ids.update(card["id"] for card in json.loads(spec).get("palettes",[]))
    for cid in sorted(palette_ids):
        if re.fullmatch(r"custom-[0-9a-f]{32}",cid):
            folder = Path("director-palettes") / cid
            if not (source.root/folder).is_dir():
                raise Rejected(f"找不到已选自定义色卡目录：{folder}")
            for path in sorted((source.root/folder).rglob("*")):
                if path.is_file():
                    rel = path.relative_to(source.root).as_posix()
                    copy(rel,file_sha256(path))
    return files


def publish_new_directory(staging, target):
    # Atomic no-replace rename, including the race with another empty directory.
    libc = ctypes.CDLL(None,use_errno=True)
    if sys.platform == "darwin":
        status = libc.renamex_np(os.fsencode(staging),os.fsencode(target),4)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc,"renameat2"):
        status = libc.renameat2(-100,os.fsencode(staging),-100,os.fsencode(target),1)  # RENAME_NOREPLACE
    else:
        raise Rejected("当前系统不支持无覆盖发布项目目录")
    if status:
        code = ctypes.get_errno()
        raise OSError(code,os.strerror(code),str(target))


def export(wb, pid, folder):
    if not isinstance(folder,str) or not folder.strip():
        raise Rejected("请填写独立项目文件夹路径")
    path = Path(folder).expanduser()
    if not path.is_absolute() or path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise Rejected("请选择已有父目录中的新项目文件夹，需填写绝对路径")
    path = path.resolve()
    if path.is_relative_to(wb.store.root):
        raise Rejected("独立项目文件夹须放在当前工作台数据目录外")
    try:
        with tempfile.TemporaryDirectory(dir=path.parent, prefix=".adw-project-") as scratch:
            staging = Path(scratch)/"project"
            staging.mkdir()
            database = staging/"workbench.sqlite"
            initialize(database)
            with connect(wb.db_path) as source, connect(database) as dest:
                source.execute("BEGIN")
                project = row(source,"SELECT id,name FROM project WHERE id=?",pid)
                dest.execute("BEGIN")
                dest.execute("PRAGMA defer_foreign_keys=ON")
                copy_records(source,dest,pid)
                # A second workbench must never automatically submit a queued request from the source.
                dest.execute("UPDATE free_turn SET status='failed',reason='对话随项目另存，请核对原工作台后决定是否重试' WHERE status IN ('queued','running')")
                dest.execute("UPDATE run SET status='failed',reason='制作任务随项目另存，请在原工作台核对后决定是否重试' WHERE status IN ('queued','running')")
                # The original may submit its authorised requests after this snapshot.
                # Preserve their request IDs and reconcile in the new workspace before any retry.
                dest.execute("UPDATE image_request SET state='unknown',error='请核对原工作台的请求结果' "
                             "WHERE state IN ('pending','submitted') AND batch_id IN "
                             "(SELECT id FROM image_batch WHERE authorization IS NOT NULL AND state!='cancelled')")
                dest.execute("UPDATE image_batch SET state='pending_reconcile' WHERE id IN "
                             "(SELECT batch_id FROM image_request WHERE state='unknown')")
                files = copy_materials(wb.store,staging,dest)
                episodes = dest.execute("SELECT count(*) FROM episode").fetchone()[0]
                dest.commit()
            info = dict(format="ai-director-project",version=1,projectId=pid,name=project["name"],episodes=episodes,
                        files=len(files), initialDatabaseSha256=file_sha256(database))
            (staging/MARKER).write_text(json.dumps(info,ensure_ascii=False,indent=2)+"\n")
            # Publish only after every referenced original has been read back. A pre-existing
            # empty directory also belongs to its owner and must never be replaced.
            publish_new_directory(staging,path)
    except (sqlite3.Error, OSError, KeyError, ValueError) as exc:
        raise Rejected(f"无法另存项目：{exc}") from exc
    return dict(**info,path=str(path))
