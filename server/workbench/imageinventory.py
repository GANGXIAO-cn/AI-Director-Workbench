"""Discover candidates within one historical episode and replay image choices from its frozen files."""
import json
import os
from pathlib import Path
import re

LEDGER = "过程稿/图片选择.json"
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def independent_manifest(raw):
    try:
        data = json.loads(raw)
    except (ValueError,UnicodeError):
        return False
    return (isinstance(data,dict) and data.get("schema_version")==2 and bool(data.get("episode_id"))
            and isinstance(data.get("assets"),list) and isinstance(data.get("segments"),list))


def capture(root, manifest, copy, read_copy):
    root = root.resolve()
    metadata = {"root":str(root), "paths":[], "excludedProjects":[], "childManifests":{}}
    try:
        if manifest.get("schema_version") != 2:
            raise ValueError("图片盘点只支持schema_version=2")
        evidence = {root/"过程稿/对标"}
        def local(name):
            if not isinstance(name,str) or not name:
                raise ValueError("本集文件路径无效")
            path = (root/name).resolve()
            if not path.is_relative_to(root):
                raise ValueError(f"文件路径越出本集：{name}")
            return path
        def document(path):
            rel = path.relative_to(root).as_posix()
            digest = copy(rel)
            if not digest:
                raise ValueError("无法读取本集文件："+rel)
            return json.loads(read_copy(digest))
        state = root/"流程状态.json"
        if state.is_file():
            receipt = document(state).get("records",{}).get("reference",{}).get("paths",{}).get("receipt")
            if receipt:
                receipt = (root/receipt).resolve()
                if receipt.name == "参考包验收.json" and receipt.parent != root and receipt.is_relative_to(root):
                    evidence.add(receipt.parent)
        paths = set()
        def walk_error(exc):
            raise exc
        for directory, dirs, files in os.walk(root,followlinks=False,onerror=walk_error):
            directory = Path(directory)
            if directory in evidence:
                dirs.clear()
                continue
            if directory != root and "episode.json" in files:
                child_path = directory/"episode.json"
                if child_path.resolve().is_relative_to(root):
                    rel = child_path.relative_to(root).as_posix()
                    digest = copy(rel)
                    if not digest:
                        raise ValueError("无法核对嵌套集清单："+rel)
                    name = directory.relative_to(root).as_posix()
                    metadata["childManifests"][name] = rel
                    if independent_manifest(read_copy(digest)):
                        metadata["excludedProjects"].append(name)
                        dirs.clear()
                        continue
            for name in files:
                if Path(name).suffix.lower() in EXTENSIONS:
                    paths.add((directory/name).relative_to(root).as_posix())
        assets = {a["id"]:a for a in manifest.get("assets",[])}
        for segment in manifest.get("segments",[]):
            for ref in segment.get("references",[]):
                asset = assets.get(ref.get("asset_id"),{})
                if asset.get("type")=="image":
                    path = (root/asset["path"]).resolve()
                    if path.is_relative_to(root):
                        paths.add(path.relative_to(root).as_posix())
        if (root/LEDGER).is_file():
            for row in ledger_rows(document(root/LEDGER),str(root)):
                if row.get("priority")!="required" or row.get("selected_by")!="user":
                    continue
                for name in row["paths"]:
                    path = (root/name).resolve()
                    if path.is_relative_to(root) and any(path.is_relative_to(d) for d in evidence) and path.is_file():
                        rel = path.relative_to(root).as_posix()
                        if copy(rel,expected=row["sha256"]):
                            paths.add(rel)
        for name in sorted(paths):
            if not local(name).is_file():
                raise FileNotFoundError("图片缺失："+name)
            if not copy(name):
                raise ValueError("图片缺失或超出本集："+name)
            metadata["paths"].append(name)
        metadata["excludedProjects"].sort()
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError) as exc:
        metadata["error"] = str(exc)
        if isinstance(exc,FileNotFoundError):
            metadata["missingFile"] = True
    return metadata


def ledger_rows(ledger, root):
    if not isinstance(ledger,dict) or ledger.get("schema_version")!=1 or ledger.get("episode_root")!=root:
        raise ValueError("图片登记版本或本集根目录不符")
    rows = ledger.get("images")
    if not isinstance(rows,list):
        raise ValueError("图片登记须列出图片")
    ids, hashes = set(), set()
    for row in rows:
        if not isinstance(row,dict):
            raise ValueError("图片登记行无效")
        digest, ident = row.get("sha256"), row.get("id")
        if not re.fullmatch("[0-9a-f]{64}",str(digest)) or not re.fullmatch("I[0-9a-f]{3,64}",str(ident)):
            raise ValueError("图片编号与内容指纹不符")
        if ident in ids or digest in hashes:
            raise ValueError("图片编号或内容重复登记")
        if not isinstance(row.get("paths"),list) or not all(isinstance(p,str) for p in row["paths"]):
            raise ValueError("图片路径清单无效")
        ids.add(ident)
        hashes.add(digest)
    return rows


def groups(archive):
    meta = archive.metadata["imageInventory"]
    if meta.get("missingFile"):
        raise FileNotFoundError(meta["error"])
    if meta.get("error"):
        raise ValueError(meta["error"])
    if "childManifests" in meta:
        excluded = sorted(name for name,path in meta["childManifests"].items() if independent_manifest(archive.read(path)))
        if excluded!=meta["excludedProjects"]:
            raise ValueError("独立子集与冻结的嵌套清单不一致")
    found = {}
    for path in meta["paths"]:
        digest = archive.files[archive.local(path)]
        found.setdefault(digest,[]).append(path)
    return found, meta["excludedProjects"]


def usage(archive, manifest):
    assets = {a["id"]:a for a in manifest.get("assets",[])}
    used, segments = {}, set()
    for seg in manifest.get("segments",[]):
        sid = seg["id"]
        segments.add(sid)
        for ref in seg.get("references",[]):
            asset = assets.get(ref.get("asset_id"))
            if not asset:
                raise ValueError("正式输入缺少资产："+str(ref.get("asset_id")))
            if asset["type"]!="image":
                continue
            name = asset["path"]
            if Path(name).is_absolute():
                ref = archive.metadata.get("fixedRefs",{}).get(name)
                actual = ref["sha256"] if ref else archive.metadata.get("sceneRefs",{}).get(name)
                if not actual:
                    raise ValueError("正式输入原图不在冻结资料中："+name)
            else:
                actual = archive.files[archive.local(name)]
            if asset.get("sha256") and asset["sha256"]!=actual:
                raise ValueError("正式输入指纹已失效："+name)
            used.setdefault(actual,set()).add(sid)
    return used,segments


def inventory(archive, manifest):
    found, excluded = groups(archive)
    used, segments = usage(archive,manifest)
    meta = archive.metadata["imageInventory"]
    prior = ledger_rows(json.loads(archive.read(LEDGER)),meta["root"]) if LEDGER in archive.files else []
    registered = {r["sha256"]:dict(r) for r in prior}
    ids = {r["id"] for r in prior}
    for digest, paths in found.items():
        if digest not in registered:
            ident = "I"+digest[:16]
            if ident in ids:
                ident = "I"+digest
            if ident in ids:
                raise ValueError("图片编号冲突："+ident)
            ids.add(ident)
            registered[digest] = dict(id=ident,sha256=digest,paths=[])
        row = registered[digest]
        row["paths"] = sorted(set(row["paths"]) | set(paths))
    problems = []
    for digest,row in registered.items():
        row["currentPaths"] = found.get(digest,[])
        row["usedIn"] = sorted(used.get(digest,set()))
        priority = row.get("priority","normal")
        scope = row.get("scope","candidate")
        fitness = row.get("fitness","not_tested")
        for label,value,allowed in [
            ("priority",priority,("normal","recommended","required")), ("scope",scope,("candidate","auxiliary")),
            ("quality",row.get("quality","not_tested"),("not_tested","standout","usable","weak")),
            ("fitness",fitness,("not_tested","pass","limited","blocked"))]:
            if value not in allowed:
                problems.append(f"{row['id']}：未知{label}")
        targets = row.get("target_segments",[])
        if not isinstance(targets,list) or not all(isinstance(s,str) for s in targets):
            problems.append(row["id"]+"：目标段清单无效")
            targets = []
        reason = row.get("selection_reason","")
        chosen = priority=="required" and row.get("selected_by")=="user" and isinstance(reason,str) and reason.strip()
        if (row["usedIn"] or priority=="required") and fitness in ("limited","blocked"):
            limitations = row.get("limitations","")
            if not isinstance(limitations,str) or not limitations.strip():
                problems.append(row["id"]+"：缺用途限制/缺陷范围")
        if row["usedIn"] and fitness=="blocked" and not chosen:
            problems.append(row["id"]+"：当前用途受阻的图仍在正式输入")
        if priority=="required":
            if row.get("selected_by") not in ("agent","user") or not isinstance(reason,str) or not reason.strip():
                problems.append(row["id"]+"：必用图缺选择来源或理由")
            if scope!="candidate":
                problems.append(row["id"]+"：必用请求尚未解决用途适配")
            if not row["currentPaths"]:
                problems.append(row["id"]+"：必用原版本不存在，不得静默替换")
            if not row["usedIn"] or set(targets)-set(row["usedIn"]):
                problems.append(row["id"]+"：必用图未落实到要求的正式输入段")
            if set(targets)-segments:
                problems.append(row["id"]+"：目标段不存在")
    return dict(current_images=len(found),images=list(registered.values()),excluded_projects=excluded,
                problems=problems)
