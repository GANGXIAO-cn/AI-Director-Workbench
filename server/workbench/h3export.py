"""Consumer constraints from H3 Canvas Studio's shared/projectPackage.ts and server/projectPackage.ts."""
import json
import math
from pathlib import Path
import re
import subprocess
import zipfile

from .storage import sha256_bytes

MIB = 1024 * 1024
OPTIONS = {"resolution", "audio", "candidates", "instanceType", "seed"}
EXTENSIONS = {"image": {".jpeg", ".jpg", ".png", ".webp"}, "audio": {".wav", ".flac", ".mp3"},
              "video": {".mp4", ".mov", ".avi", ".mkv"}}


def asset_origin(version):
    # A redraw may create a new version on an asset initially copied from the library.
    if version.get("library_entry_id"):
        return "reuse"
    if version["version_no"] == 1 and version["origin"] in ("reuse", "fixed_role", "project_library", "library_copy"):
        return "reuse"
    return "new"


def length(text):
    # H3's JavaScript schema measures UTF-16 code units.
    return len(text.encode("utf-16-le")) // 2


MEDIA_FORMATS = {".mp4":"mov", ".mov":"mov", ".mkv":"matroska", ".avi":"avi",
                 ".wav":"wav", ".flac":"flac", ".mp3":"mp3"}
MEDIA_MIME = {".mp4":"video/mp4", ".mov":"video/quicktime", ".mkv":"video/x-matroska", ".avi":"video/x-msvideo",
              ".wav":"audio/wav", ".flac":"audio/flac", ".mp3":"audio/mpeg"}


def media_kind(name):
    ext = Path(name).suffix.lower()
    for kind in ("video", "audio"):
        if ext in EXTENSIONS[kind]:
            return kind
    raise ValueError("请选择 MP4、MOV、AVI、MKV 视频或 WAV、FLAC、MP3 音频")


def media_size_limit(kind):
    return (15 if kind == "audio" else 30) * MIB


def media_duration(file, kind, name=None, stream=None):
    name = name or file.name
    ext = Path(name).suffix.lower()
    if media_kind(name) != kind:
        raise ValueError(f"{name} 的扩展名与参考类型不符")
    args = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist", MEDIA_FORMATS[ext],
            "-show_entries", "format=format_name,duration:stream=codec_type:stream_disposition=attached_pic",
            "-of", "json", str(file)]
    try:
        run = subprocess.run(args, stdin=stream, timeout=30, capture_output=True, check=True)
        info = json.loads(run.stdout)
        duration = float(info["format"]["duration"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        raise ValueError(f"无法核验 {name} 的文件格式、轨道或时长，请检查文件和本机 ffprobe") from exc
    tracks = [s["codec_type"] for s in info.get("streams", []) if not s.get("disposition", {}).get("attached_pic")]
    if MEDIA_FORMATS[ext] not in info["format"]["format_name"].split(",") or kind not in tracks:
        raise ValueError(f"{name} 的文件格式或媒体轨道与扩展名不符")
    if not math.isfinite(duration) or not 2 <= duration <= 15:
        raise ValueError(f"{name} 的参考时长须为 2–15 秒")
    return duration


def unique_inputs(project, selected, overrides=None, segment_overrides=None):
    """H3 stores one copy per digest; preserve each segment's submitted order and label."""
    data = project["data"]
    wanted = set(selected)
    used = {r["asset_id"] for s in data["segments"] if s["id"] in wanted for r in s["references"]}
    canonical, binding = {}, {}
    for asset in data["assets"]:
        if asset["id"] in used:
            first = canonical.setdefault(asset["sha256"], asset)
            if first["type"] != asset["type"]:
                raise ValueError(f"{asset['id']} 与另一份相同内容的参考媒体类型不一致")
            binding[asset["id"]] = first["id"]
    segments = []
    for seg in data["segments"]:
        if seg["id"] in wanted:
            refs = [dict(ref, asset_id=binding[ref["asset_id"]]) for ref in seg["references"]]
            if len({r["asset_id"] for r in refs}) != len(refs):
                raise ValueError(f"{seg['id']} 有内容相同的参考，请在提示词页合并引用并复审")
            settings = {**(seg.get("generation_settings") or {}), **(overrides or {}),
                        **(segment_overrides or {}).get(seg["id"], {})}
            seg = dict(seg, references=refs, generation_settings=settings)
        segments.append(seg)
    return dict(project, data=dict(data, segments=segments))


def check_prompt(seg, kinds):
    text = seg["prompt"]
    if re.search(r"<(?:Picture|Video|Audio)\s+\d+>", text, re.I|re.ASCII):
        raise ValueError(f"{seg['id']} 含手写的 H3 素材编号，请使用参考栏中的标签")
    labels = {}
    numbers = {"image":0, "video":0, "audio":0}
    for ref in seg["refs"]:
        kind = kinds[ref["assetId"]]
        numbers[kind] += 1
        labels[ref["label"]] = "<" + {"image":"Picture", "video":"Video", "audio":"Audio"}[kind] + f" {numbers[kind]}>"
    # The consumer builds one paragraph per line with separate text and reference nodes.
    nodes, compiled = 1, []
    for line in text.replace("\r\n", "\n").split("\n"):
        nodes += 1
        pieces = re.split(r"((?:图片|视频|音频)\d+|@(?:Image|Video|Audio)\d+)", line, flags=re.ASCII)
        for token in pieces[1::2]:
            if token not in labels:
                raise ValueError(f"{seg['id']} 的正文引用了参考栏中不存在的标签 {token}")
        nodes += sum(bool(piece) for piece in pieces)
        compiled.append("".join(labels[token] if i%2 else token for i,token in enumerate(pieces)))
    if nodes > 10000 or length("\n".join(compiled).strip()) > 50000:
        raise ValueError(f"{seg['id']} 的编译后提示词超过 H3 的结构或长度限制")


def prepare(package, files, project, legacy_origin):
    sources = {a["id"]:a for a in project["data"]["assets"]}
    if not 1 <= length(package["title"]) <= 120:
        raise ValueError("H3 项目包标题须为 1–120 个字符")
    if len(package["assets"]) > 1000:
        raise ValueError("H3 项目包最多 1000 项素材，请分段导出")
    durations = {}
    kinds = {}
    for asset in package["assets"]:
        aid, kind = asset["id"], asset["kind"]
        source = sources[aid]
        if asset["origin"] == "workbench":
            asset["origin"] = legacy_origin(aid)
        if asset["origin"] not in ("new", "reuse"):
            raise ValueError(f"{aid} 的 H3 素材来源无效")
        rel = asset["path"]
        if (Path(rel).suffix.lower() not in EXTENSIONS[kind] or Path(asset["name"]).suffix.lower() not in EXTENSIONS[kind]):
            raise ValueError(f"{source.get('name',aid)} 的格式不受 H3 的 {kind} 参考支持")
        if len(files[rel]) > media_size_limit(kind):
            raise ValueError(f"{asset['name']} 超出 H3 {kind} 参考文件大小限制")
        kinds[aid] = kind
        if kind != "image":
            durations[aid] = (kind, media_duration(project["root"]/source["path"], kind, asset["name"]))
    for seg in package["segments"]:
        sid = seg["id"]
        if length(seg["prompt"]) > 50000 or length(sid+" · "+seg["title"]) > 200:
            raise ValueError(f"{sid} 的 TXT 超过 50000 字符或 H3 画布节点标题超过 200 字符")
        check_prompt(seg, kinds)
        totals = {"audio":0, "video":0}
        for ref in seg["refs"]:
            aid = ref["assetId"]
            if aid in durations:
                kind, duration = durations[aid]
                totals[kind] += duration
        if any(value > 15 for value in totals.values()):
            raise ValueError(f"{sid} 的音频或视频参考总时长超过 15 秒")
        if all(kinds[r["assetId"]] == "audio" for r in seg["refs"]):
            raise ValueError(f"{sid} 至少需要一个图片或视频参考")


def verify_zip(path, package):
    if path.stat().st_size > 128*MIB:
        raise ValueError("H3 导入 ZIP 超过 128 MiB，请分段导出")
    with zipfile.ZipFile(path) as archive:
        files = archive.infolist()
        assets = {a["path"]:a for a in package["assets"]}
        if len(files) != 1+len(assets) or set(archive.namelist()) != {"project.json", *assets}:
            raise ValueError("ZIP 条目与导出清单不同")
        if archive.getinfo("project.json").file_size > 2*MIB or sum(i.file_size for i in files) > 256*MIB:
            raise ValueError("H3 清单超过 2 MiB 或解压总大小超过 256 MiB，请分段导出")
        if json.loads(archive.read("project.json")) != package:
            raise ValueError("ZIP 中的 project.json 与本次导出不一致")
        for name, asset in assets.items():
            if archive.getinfo(name).file_size > 30*MIB or sha256_bytes(archive.read(name)) != asset["sha256"]:
                raise ValueError(f"ZIP 素材指纹或大小有误：{name}")
