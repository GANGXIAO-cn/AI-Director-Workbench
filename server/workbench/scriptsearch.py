"""Bounded, resumable literal search across the mounted catalog, including unindexed Markdown."""
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from .errors import Conflict, Rejected
from .resources import database, inside
from .storage import canonical


def catalog_stamp(root, query, category, kind, recommended):
    files = []
    for filename in ("knowledgeIndex.sqlite", "knowledgeIndex.sqlite-wal"):
        try:
            stat = (root / filename).stat()
        except FileNotFoundError:
            continue
        files.append([filename, stat.st_ino, stat.st_mtime_ns, stat.st_size])
    return sha256(canonical([str(root), files, query, category, kind, recommended])).hexdigest()


def search(root, query, category, kind, recommended, cursor, limit):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 160:
        raise Rejected("请输入 1 至 160 字的原文")
    if kind not in ("", "reference", "method", "novel") or type(limit) is not int or not 1 <= limit <= 40:
        raise Rejected("资料类型或分页数量无效")
    query = query.strip()
    stamp = catalog_stamp(root, query, category, kind, recommended)
    last = 0
    if cursor is not None:
        if not isinstance(cursor, dict) or cursor.get("stamp") != stamp or type(cursor.get("after")) is not int:
            raise Conflict("检索条件或目录索引已改变，请重新搜索")
        last = cursor["after"]
    where, args = ["category NOT LIKE '90-%'", "path NOT LIKE '%/来源清单.md' AND path != '来源清单.md'"], []
    for field, value in (("category", category), ("kind", kind)):
        if value:
            where.append(field + "=?")
            args.append(value)
    if recommended:
        where.append("kind IN ('method','reference')")
    clause = " FROM documents WHERE " + " AND ".join(where)
    with closing(database(root)) as db:
        db.execute("BEGIN")
        total = db.execute("SELECT count(*)" + clause, args).fetchone()[0]
        candidates = db.execute("SELECT id,title,path,kind,category,sha256" + clause +
                                " AND id>? ORDER BY id LIMIT 129", [*args, last]).fetchall()
    rows, issues = [], []
    scanned = used = 0
    needle = query.encode()
    for row in candidates:
        if len(rows) >= limit or scanned >= 128 or used >= 64 * 1024 * 1024:
            break
        scanned += 1
        last = row["id"]
        try:
            if Path(row["path"]).suffix.lower() != ".md":
                raise Rejected("资料格式不是 Markdown")
            raw = inside(root, row["path"]).read_bytes()
            used += len(raw)
            position = raw.find(needle)
            if position < 0:
                continue
            digest = sha256(raw).hexdigest()
            if row["sha256"] and row["sha256"] != digest:
                raise Rejected("原文件指纹已变化，请更新资料索引")
            # All excerpt offsets count Unicode code points after an optional UTF-8 BOM.
            body = raw.decode("utf-8-sig")
            at = body.find(query)
            if at < 0:
                continue
            rows.append(dict(title=row["title"], path=row["path"], kind=row["kind"], category=row["category"],
                             sha256=digest, match=dict(offset=at, text=query), matchOffset=max(0, at-180),
                             evidence=body[max(0, at-28):at+len(query)+65]))
        except (OSError, UnicodeError, Rejected) as exc:
            issues.append(dict(path=row["path"], reason=str(exc)))
    if catalog_stamp(root, query, category, kind, recommended) != stamp:
        raise Conflict("目录索引在检索期间已改变，请重新搜索")
    following = dict(stamp=stamp, after=last) if scanned < len(candidates) else None
    return dict(rows=rows, issues=issues, scanned=scanned, total=total, cursor=following)
