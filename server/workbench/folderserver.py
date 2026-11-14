"""Serve an opened project on its own loopback origin, with edits written to that folder."""
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from uuid import UUID

from .db import SCHEMA, transaction
from .errors import Rejected
from .projectfolder import MARKER, copy_materials
from .storage import Store
from .service import Workbench


def inspect_folder(path):
    if not path.is_absolute() or not path.is_dir():
        raise Rejected("请填写已另存的项目文件夹绝对路径")
    database = path/"workbench.sqlite"
    try:
        if database.is_symlink() or not database.is_file() or not (path/MARKER).is_file() or (path/MARKER).is_symlink():
            raise ValueError("缺少独立项目数据库或目录标记")
        info = json.loads((path/MARKER).read_bytes())
        pid = info["projectId"]
        if not re.fullmatch(r"[0-9a-f]{64}",info["initialDatabaseSha256"]):
            raise ValueError("项目文件夹标记缺少初始版本指纹")
        if info["format"] != "ai-director-project" or info["version"] != 1 or str(UUID(pid)) != pid:
            raise ValueError("项目目录格式不受支持")
        # The folder supplies data only. Do not execute schema, triggers, custom
        # SQLite extensions, or settings for an executable from an incoming file.
        with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as db, closing(sqlite3.connect(":memory:")) as model:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            model.executescript(SCHEMA)
            schema = "SELECT name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
            trusted = dict(model.execute(schema))
            actual = dict(db.execute(schema))
            # Accept the exact pre-migration and ALTER-generated forms of the one new column.
            kickoff_forms={trusted['kickoff_check']}
            legacy_schema=SCHEMA.replace(", error_class TEXT);", ");", 1)
            with closing(sqlite3.connect(":memory:")) as legacy:
                legacy.executescript(legacy_schema)
                kickoff_forms.add(legacy.execute("SELECT sql FROM sqlite_master WHERE name='kickoff_check'").fetchone()[0])
                legacy.execute("ALTER TABLE kickoff_check ADD COLUMN error_class TEXT")
                kickoff_forms.add(legacy.execute("SELECT sql FROM sqlite_master WHERE name='kickoff_check'").fetchone()[0])
            if any(sql not in kickoff_forms if name=='kickoff_check' else trusted.get(name) != sql for name,sql in actual.items()):
                raise ValueError("项目数据库包含未知结构")
            if [r[0] for r in db.execute("SELECT id FROM project")] != [pid]:
                raise ValueError("项目目录须包含且仅包含标记中的项目")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("项目数据库完整性检查未通过")
            # Columns which can later be opened as files must stay under their
            # own project, delivery or run, including when the folder was moved.
            for table, prefix in (("import_record",f"projects/{pid}/imports"),("ref_source",f"projects/{pid}/imports"),
                                  ("asset_version","blobs/sha256"),("delivery",f"projects/{pid}/episodes"),
                                  ("package","packages"),("backup","backups"),("run","runs")):
                field = "manifest_relpath" if table=="run" else "relpath"
                if table not in actual:
                    continue
                for (value,) in db.execute(f'SELECT "{field}" FROM "{table}"'):
                    rel = Path(value)
                    if rel.is_absolute() or ".." in rel.parts or not rel.is_relative_to(prefix):
                        raise ValueError(f"{table} 的文件登记越出项目范围")
            for table in ("project","episode","run","delivery","ref_question"):
                if table in actual:
                    for (ident,) in db.execute(f'SELECT id FROM "{table}"'):
                        if str(UUID(ident)) != ident:
                            raise ValueError(f"{table} 对象编号无效")
            copy_materials(Store(path),None,db)
        return pid
    except (KeyError,TypeError,ValueError,OSError,sqlite3.Error) as exc:
        raise Rejected(f"无法打开项目目录：{exc}") from exc


class FolderWorkbench(Workbench):
    """Machine-wide executables, external mounts and secrets stay with the opening service."""
    def __init__(self, folder, parent):
        self.parent = parent
        super().__init__(folder,dict(parent.executors),clock=parent.clock,image_providers=dict(parent.image_providers_map))

        self.opened_directory = self.store.root.stat()
        self.opened_database = self.db_path.stat()
        with transaction(self.db_path) as db:
            db.execute("UPDATE run SET status='failed',reason='独立项目重新打开，请核对后重试' WHERE status='queued'")

    def available(self):
        try:
            return (os.path.samestat(self.opened_directory,self.store.root.stat())
                    and os.path.samestat(self.opened_database,self.db_path.stat()))
        except OSError:
            return False

    def storage_status(self):
        return dict(super().storage_status(),externalProject=True)

    def get_setting(self, key, default=None):
        if key in ("default-image-channel","codex_path","claude_path","antigravity_path","packager_path") or key.startswith(("mount:","connection:","cli_models:")):
            return self.parent.get_setting(key,default)
        return super().get_setting(key,default)

    def put_setting(self,key,value):
        if key in ("default-image-channel","codex_path","claude_path","antigravity_path","packager_path") or key.startswith(("mount:","connection:","cli_models:")):
            raise Rejected("请在主工作台设置本机工具与资料目录")
        super().put_setting(key,value)

    def create_project(self,*args,**kwargs):
        raise Rejected("此目录仅保存当前项目，请在主工作台新建项目")


class FolderServers:
    def __init__(self, parent):
        self.parent = parent
        self.lock = threading.Lock()
        self.opened = {}

    def open(self, folder, expected=None):
        from .app import create_app
        import uvicorn
        if not isinstance(folder,str) or not Path(folder).expanduser().is_absolute():
            raise Rejected("请填写项目文件夹绝对路径")
        path = Path(folder).expanduser().resolve()
        if path == self.parent.store.root or path.is_relative_to(self.parent.store.root):
            raise Rejected("请选择当前工作台数据目录以外的独立项目文件夹")
        with self.lock:
            if expected:
                try:
                    is_expected = json.loads((path/MARKER).read_bytes()).get("initialDatabaseSha256") == expected
                except (ValueError, OSError):
                    is_expected = False
                if not is_expected:
                    raise Rejected("所选目录与原独立项目不符，请核对磁盘和项目位置")
            if path in self.opened:
                server,thread,url,pid = self.opened[path]
                if thread.is_alive():
                    if server.should_exit:
                        raise Rejected("此目录正在关闭，请稍后重开")
                    return dict(url=url, projectId=pid,path=str(path))
            if not path.is_dir():
                raise Rejected("项目目录不可用，请检查磁盘或重新定位")
            fd = os.open(path,os.O_RDONLY)
            sock = None
            try:
                try:
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Rejected("此项目目录已由另一个工作台打开") from exc
                for directory,subdirs,files in os.walk(path):
                    if any((Path(directory)/name).is_symlink() for name in [*subdirs,*files]):
                        raise Rejected("独立项目文件夹中存在符号链接，请核对原件")
                pid = inspect_folder(path)
                wb = FolderWorkbench(path,self.parent)
                app = create_app(wb, external=True)
                sock = socket.socket()
                sock.bind(("127.0.0.1",0))
                port = sock.getsockname()[1]
                url = f"http://127.0.0.1:{port}/#/p/{pid}"
                server = uvicorn.Server(uvicorn.Config(app,log_level="warning",access_log=False))
                def serve():
                    try:
                        server.run(sockets=[sock])
                    finally:
                        sock.close()
                        os.close(fd)
                thread = threading.Thread(target=serve,daemon=True,name="adw-project-"+pid[:8])
                thread.start()
            except BaseException:
                if sock is not None:
                    sock.close()
                os.close(fd)
                raise
            for _ in range(500):
                if server.started:
                    self.opened[path] = (server,thread,url,pid)
                    marker = json.loads((path/MARKER).read_bytes())
                    key = marker["initialDatabaseSha256"]
                    recent = self.parent.get_setting("project-folders",[])
                    entry = dict(path=str(path),projectId=pid,name=wb.list_projects()[0]["name"],fingerprint=key)
                    self.parent.put_setting("project-folders",[entry]+[r for r in recent if r["path"] != str(path) and
                            (r["fingerprint"] != key or Path(r["path"]).is_dir())])
                    return dict(url=url,projectId=pid,path=str(path))
                if not thread.is_alive():
                    break
                time.sleep(.01)
            server.should_exit = True
            raise Rejected("项目目录服务未能启动")

    def entries(self):
        with self.lock:
            return [dict(path=str(path),url=url,projectId=pid) for path,(_,thread,url,pid) in self.opened.items() if thread.is_alive()]

    def recent(self):
        return [dict(item,available=(Path(item["path"])/MARKER).is_file()) for item in self.parent.get_setting("project-folders",[])]

    def close(self, folder):
        with self.lock:
            entry = self.opened.get(Path(folder))
            if entry:
                server,thread,_,_ = entry
                server.should_exit = True
                thread.join(timeout=10)
                if thread.is_alive():
                    raise Rejected("项目仍在完成任务，请稍后再检查磁盘状态")
                self.opened.pop(Path(folder),None)
            return dict(closed=True)

    def shutdown(self):
        with self.lock:
            for server,thread,_,_ in self.opened.values():
                server.should_exit = True
            for _,thread,_,_ in self.opened.values():
                thread.join(timeout=10)
