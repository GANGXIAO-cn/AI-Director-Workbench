"""Freeze accepted shot reports independently of candidate-image enumeration."""
from hashlib import sha256
from html import escape
from io import BytesIO
from urllib.parse import quote
from zipfile import ZipFile
import json
from pathlib import Path, PurePosixPath
import re


def capture(root, project_root, store, files):
    state = files.get("流程状态.json")
    active = None
    if state:
        try:
            path = json.loads(store.read_blob(state)).get("records",{}).get("reference",{}).get("paths",{}).get("receipt")
            if isinstance(path,str) and path:
                active = root/path
        except (ValueError,TypeError,AttributeError,OSError):
            return []
    reserved = root/"过程稿/对标"
    candidates = [active] if active else sorted(reserved.rglob("参考包验收.json")) if reserved.resolve().is_relative_to(root) else []
    reports = []
    for path in candidates:
        report = {"source":str(path),"resources":{}, "missing":[]}
        reports.append(report)
        try:
            receipt = path.resolve()
            if receipt.name!="参考包验收.json" or not (receipt.is_relative_to(root) or
                    root.is_relative_to(project_root/"episodes") and receipt.is_relative_to(project_root/"episodes")):
                raise ValueError("参考包验收记录不在本集或本项目的历史集目录")
            raw = receipt.read_bytes()
            document = json.loads(raw)
            hashes = document.get("files") if isinstance(document,dict) else None
            if not isinstance(hashes,dict) or not re.fullmatch("[0-9a-f]{64}",str(hashes.get("report.html"))):
                raise ValueError("验收记录没有可核对的 report.html 指纹")
            report["receiptHash"] = store.put_blob(raw)
            def freeze(name):
                target = (receipt.parent/name).resolve()
                if not target.is_relative_to(receipt.parent):
                    raise ValueError("文件越出参考包目录")
                data = target.read_bytes()
                if sha256(data).hexdigest()!=hashes[name]:
                    raise ValueError("文件与验收回执指纹不符")
                report["resources"][name] = store.put_blob(data)
            freeze("report.html")
            for name in sorted(hashes):
                rel = PurePosixPath(name)
                is_image = len(rel.parts) >= 2 and rel.parts[0] in {"evidence","frames"} and rel.suffix.lower() in {".png",".jpg",".jpeg",".webp",".gif"}
                if name != "report.md" and not is_image:
                    continue
                if ".." in rel.parts or rel.as_posix()!=name:
                    report["missing"].append(name)
                    continue
                try:
                    freeze(name)
                except (ValueError,OSError,RuntimeError):
                    report["missing"].append(name)
        except (ValueError,OSError,RuntimeError) as exc:
            report["error"] = str(exc)
    return reports


def digests(metadata):
    for report in metadata.get("referenceReports",[]):
        if report.get("receiptHash"):
            yield report["receiptHash"]
        yield from report["resources"].values()


def packet(report, store):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, digest in report["resources"].items():
            archive.writestr(name, store.read_blob(digest))
    return output.getvalue()


def preview(report, store):
    resources = report["resources"]
    frames = [name for name in sorted(resources) if name.startswith(("frames/", "evidence/"))]
    text = store.read_blob(resources["report.md"]).decode("utf-8-sig", errors="replace") if "report.md" in resources else ""
    paragraphs = []
    for block in text.split("\n\n"):
        block = block.strip()
        heading = re.fullmatch(r"#{1,6} (.+)", block)
        if heading:
            paragraphs.append("<h2>" + escape(heading[1]) + "</h2>")
        elif block:
            paragraphs.append("<pre>" + escape(block) + "</pre>")
    gallery = []
    for name in frames:
        url = quote(name)
        gallery.append(f'<figure><a href="{url}"><img loading="lazy" src="{url}" alt="{escape(name,quote=True)}"></a>'
                       f'<figcaption>{escape(name)}</figcaption></figure>')
    missing = escape("、".join(report["missing"]))
    warning = f"<p>未能核对的文件 · {missing}</p>" if missing else ""
    page = """<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>逐镜报告</title><style>body{font:16px/1.6 system-ui,sans-serif;color:#242424;max-width:1000px;margin:2rem auto;padding:0 1rem}
    h1,h2{line-height:1.3}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}section{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:1rem}
    figure{margin:0}img{width:100%;height:auto}figcaption{overflow-wrap:anywhere}</style><main><h1>逐镜报告</h1>"""
    return (page + '<a href="report.zip" download>下载原版报告与配图</a> · <a href="#frames">查看抽帧与证据图</a>' +
            warning + "".join(paragraphs) + f'<h2 id="frames">抽帧与证据图（{len(frames)}）</h2><section>' +
            "".join(gallery) + "</section></main></html>").encode("utf-8")
