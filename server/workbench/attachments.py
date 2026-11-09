"""Prepare local files explicitly attached at kickoff or to one director message."""
from pathlib import Path

from .errors import Rejected
from .images import image_size, image_attachment
from .manuscripts import extract_manuscript
from .storage import sha256_bytes

LIMIT = 30 * 1024 * 1024


def prepare(files, images_only=False):
    entries, receipts = [], []
    total = 0
    for ordinal, (filename, data) in enumerate(files, 1):
        name = Path(filename).name
        total += len(data)
        if ordinal > 6 or total > LIMIT:
            raise Rejected("每次最多附 6 份文件，合计 30 MiB")
        digest = sha256_bytes(data)
        if not images_only and Path(name).suffix.lower() in (".md", ".txt", ".pdf", ".docx"):
            status, text, diagnostic, _ = extract_manuscript(name, data)
            if status != "parsed":
                raise Rejected(f"{name}：{diagnostic}")
            entry = dict(role="citation", name=f"随消息附上的稿件 {ordinal} · {name}", text=text)
        else:
            if not image_size(data):
                raise Rejected(f"{name}：请选择可读取的 PNG 或 JPEG 图片")
            entry = image_attachment(f"附件{ordinal}", data)
        entries.append(entry)
        receipts.append(dict(name=name, sha256=digest, inputName=entry["name"]))
    return entries, receipts
