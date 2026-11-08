"""Incremental maintenance of a mounted aistudio-md-2 library and its portable catalogs."""
import csv
from contextlib import closing, contextmanager
from datetime import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile

from .errors import Rejected
from .storage import write_atomic

CATEGORY = re.compile(r"^(?:0[1-8]|10)-[^/]+$")
AUTHORIZED = "07-用户授权作品"
# Shipped administration document: it explicitly lists books still awaiting import.
ADMIN_DOCUMENTS = {"08-编剧方法书籍/现代编剧经典-正版待导入/现代编剧经典-正版导入清单.md"}


def root_db(root):
    target = root / "knowledgeIndex.sqlite"
    if target.is_symlink() or not target.is_file():
        raise Rejected("当前挂载目录没有可维护的本地知识库索引")
    conn = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA trusted_schema=OFF")
    return closing(conn)


@contextmanager
def exclusive(root):
    # Lock the directory inode, shared by imports, refreshes and multiple app processes.
    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with root_db(root) as db:
            schema = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
            if not schema or schema[0] != "aistudio-md-2":
                raise Rejected("仅支持维护 aistudio-md-2 知识库")
        yield
    finally:
        os.close(fd)


def sql_text(value):
    return "CAST(X'" + value.encode().hex() + "' AS TEXT)"


def readable_text(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise Rejected("剧本须为 UTF-8 Markdown") from exc
    if not text.strip() or "\0" in text:
        raise Rejected("剧本没有可读正文")
    return text


def terms(text):
    # Mounted index uses one token per distinct adjacent Unicode pair.
    lower = text.casefold()
    pairs = {a+b for a,b in zip(lower,lower[1:])}
    return " ".join(f"g{ord(a):06x}{ord(b):06x}" for a,b in sorted(pairs))


def check_path(root, name):
    if not isinstance(name, str) or "/" in name or not CATEGORY.fullmatch(name):
        raise Rejected("请选择正式资料分类")
    folder = root / name
    if folder.is_symlink() or not (folder.is_dir() or name == AUTHORIZED):
        raise Rejected("资料分类不在挂载目录")
    return folder


def enumerate_documents(root):
    def fail(error):
        raise error
    for folder in sorted(root.iterdir()):
        if not CATEGORY.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
            continue
        for parent, dirs, files in os.walk(folder, onerror=fail):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(parent)/d).is_symlink()]
            for name in files:
                path = Path(parent) / name
                if name.startswith(".") or path.suffix.lower() != ".md" or path.is_symlink() or not path.is_file():
                    continue
                yield path.relative_to(root).as_posix(), path


def catalog_files(root):
    with root_db(root) as db:
        rows = db.execute("SELECT title,kind,category,path,size,modified_ns,indexed FROM documents ORDER BY path")
        csv_buffer = io.StringIO(newline="")
        writer = csv.writer(csv_buffer)
        writer.writerow(["书名","资料类型","分类","相对路径","文件字节","修改时间纳秒","正文加速"])
        writer.writerows(rows)
        csv_data = csv_buffer.getvalue().encode("utf-8-sig")
        count, indexed = db.execute("SELECT count(*),coalesce(sum(indexed),0) FROM documents").fetchone()
        metadata = dict(db.execute("SELECT key,value FROM metadata WHERE key IN ('schema','kind_version')"))
    stat = (root / "knowledgeIndex.sqlite").stat()
    info = dict(schema=metadata["schema"], kindVersion=metadata.get("kind_version","2"), documents=count,
                indexedDocuments=indexed, bytes=stat.st_size, modifiedMs=stat.st_mtime_ns / 1e6)
    csv_path = root/"knowledgeCatalog.csv"
    info_path = root/"knowledgeIndex.json"
    if not csv_path.is_file() or csv_path.read_bytes() != csv_data:
        write_atomic(csv_path, csv_data)
    try:
        saved = json.loads(info_path.read_bytes())
    except (OSError, ValueError):
        saved = {}
    if isinstance(saved, dict) and isinstance(saved.get("modifiedMs"), (int,float)) and abs(saved["modifiedMs"]-info["modifiedMs"]) < 0.001:
        # Source package records microseconds; Python's float may retain one additional digit.
        info["modifiedMs"] = saved["modifiedMs"]
    if saved != info:
        write_atomic(info_path, (json.dumps(info,ensure_ascii=False,indent=2)+"\n").encode())
    return info


def update(root, only=None):
    """Called under the directory lock. One transaction updates both FTS and the document table."""
    counts = dict(added=0, changed=0, removed=0)
    with root_db(root) as db:
        old = {r["path"]:r for r in db.execute("SELECT * FROM documents")}
        excluded = ADMIN_DOCUMENTS | {r[0] for r in db.execute("SELECT path FROM excluded_sources")}
    found = set()
    with tempfile.TemporaryFile(mode="w+",encoding="utf-8") as script:
        script.write("PRAGMA trusted_schema=OFF;\nBEGIN IMMEDIATE;\n")
        for name, path in ([(only,root/only)] if only else enumerate_documents(root)):
            if name in excluded:
                if only:
                    raise Rejected("该路径已列入知识库排除清单")
                continue
            found.add(name)
            previous = old.get(name)
            stat = path.stat()
            if previous and previous["size"] == stat.st_size and previous["modified_ns"] == stat.st_mtime_ns:
                continue
            raw = path.read_bytes()
            body = readable_text(raw)
            after = path.stat()
            if stat.st_size != len(raw) or (stat.st_mtime_ns,stat.st_size,stat.st_ino) != (after.st_mtime_ns,after.st_size,after.st_ino):
                raise Rejected("扫描期间文件发生变化，请重试：" + name)
            category = name.split("/")[0]
            kind = previous["kind"] if previous else ("method" if category.startswith("08-") else "novel" if category.startswith("10-") else "reference")
            indexed = previous["indexed"] if previous else int(kind != "novel")
            if previous:
                script.write(f"DELETE FROM search WHERE rowid={previous['id']};\n")
            values = [sql_text(name),sql_text(previous["title"] if previous else path.stem),sql_text(category),sql_text(kind),
                      str(len(raw)),str(stat.st_mtime_ns),sql_text(hashlib.sha256(raw).hexdigest()),str(indexed)]
            script.write("INSERT INTO documents(path,title,category,kind,size,modified_ns,sha256,indexed) VALUES(" +
                         ",".join(values) + ") ON CONFLICT(path) DO UPDATE SET size=excluded.size, modified_ns=excluded.modified_ns, sha256=excluded.sha256;\n")
            if indexed:
                script.write("INSERT INTO search(rowid,terms) SELECT id," + sql_text(terms(body)) +
                             " FROM documents WHERE path=" + sql_text(name) + ";\n")
            counts["changed" if previous else "added"] += 1
        if only is None:
            for name in old.keys()-found:
                if not CATEGORY.fullmatch(name.split("/")[0]):
                    continue
                ident = old[name]["id"]
                script.write(f"DELETE FROM search WHERE rowid={ident};\nDELETE FROM documents WHERE id={ident};\n")
                counts["removed"] += 1
        if any(counts.values()):
            script.write("UPDATE metadata SET value="+sql_text(datetime.now().isoformat(timespec="seconds"))+
                         " WHERE key='completed_at';\nCOMMIT;\n")
            script.seek(0)
            cli = shutil.which("sqlite3")
            if not cli:
                raise Rejected("维护正文索引需要 sqlite3 命令行工具（3.43 或以上）")
            result = subprocess.run([cli,"-batch","-bail","-init","/dev/null",str(root/"knowledgeIndex.sqlite")],
                                    stdin=script,capture_output=True)
            if result.returncode:
                raise Rejected("正文索引事务未提交：" + result.stderr.decode(errors="replace")[:400])
    return dict(**counts, **catalog_files(root))


def add_document(root, category, title, content):
    if not isinstance(title,str) or not title.strip() or len(title)>150 or re.search(r'[/\\:\x00-\x1f]',title) or title.strip().startswith("."):
        raise Rejected("请填写有效的剧本名称")
    if not isinstance(content,str):
        raise Rejected("剧本须为 UTF-8 Markdown")
    raw = content.encode()
    if len(raw)>16*1024*1024:
        raise Rejected("单次添加上限为 16 MB")
    readable_text(raw)
    with exclusive(root):
        folder = check_path(root,category)
        folder.mkdir(exist_ok=True)
        target = folder / (title.strip()+".md")
        name = target.relative_to(root).as_posix()
        with root_db(root) as db:
            if db.execute("SELECT 1 FROM excluded_sources WHERE path=?", (name,)).fetchone():
                raise Rejected("该路径已列入知识库排除清单")
        if target.is_symlink() or (target.exists() and (not target.is_file() or target.read_bytes()!=raw)):
            raise Rejected("存在同名资料，请更换名称，现有正文已保留")
        if not target.exists():
            # An exclusive link publishes a fully written file without replacing an external writer.
            with tempfile.NamedTemporaryFile(dir=folder) as tmp:
                tmp.write(raw)
                tmp.flush()
                try:
                    os.link(tmp.name,target)
                except FileExistsError as exc:
                    raise Rejected("同名资料已由其他操作添加，请重试") from exc
        return dict(path=name,**update(root,name))
