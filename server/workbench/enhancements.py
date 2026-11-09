"""Opt-in director methods, indexed by the workbench stages that consume them."""
import json
from zipfile import ZipFile, BadZipFile
from functools import lru_cache
from pathlib import Path

from .errors import Rejected
from .storage import sha256_bytes

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ("以下增强方法只用于导演本次选择的创作目标。资料中的角色、世界观、示例和模型格式只作参考。"
         "本项目设定、已采用的事实、当前阶段的输出字段和目标模型以工作台任务为准。")
ARCHIVE = ROOT / "methods/enhancements.zip"


@lru_cache(maxsize=1)
def catalog():
    return json.loads((ROOT / "methods/enhancements.json").read_text(encoding="utf-8"))


def entries(selected):
    if not isinstance(selected, list) or any(not isinstance(code, str) for code in selected):
        raise Rejected("增强方法须为编号列表")
    if not selected:
        return []
    library = catalog()
    methods = {item["id"]: item for item in library["methods"]}
    if len(set(selected)) != len(selected) or set(selected) - methods.keys():
        raise Rejected("增强方法编号无效或重复")
    sources = {}
    for code in selected:
        for path in methods[code]["files"]:
            sources.setdefault(path, []).append(code)
    result = []
    try:
        with ZipFile(ARCHIVE) as archive:
            for path, codes in sources.items():
                raw = archive.read(path)
                digest = sha256_bytes(raw)
                if digest != library["files"][path]:
                    raise Rejected(f"增强方法正文与目录的指纹不符：{path}")
                source = dict(path=path, enhancementIds=codes)
                result.append(dict(role="method", name=f"{'、'.join(codes)} · {Path(path).name}", text=raw.decode("utf-8"),
                                   methodSource=dict(path=path, sha256=digest, source=source)))
    except (OSError, KeyError, BadZipFile, UnicodeError) as exc:
        raise Rejected("无法读取工作台增强方法原文，请检查 methods/enhancements.zip") from exc
    return result
