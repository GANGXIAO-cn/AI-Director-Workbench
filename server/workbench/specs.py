"""Project presets copied into an episode at kickoff."""
from fractions import Fraction
import math
import re

from .errors import Rejected
from .images import image_size
from .storage import sha256_bytes

RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9")


def validate_spec(values):
    spec = dict(values)
    if spec.get("aspectRatio") not in RATIOS:
        raise Rejected("画幅不在 H3 支持范围：" + "、".join(RATIOS))
    maximum = str(spec.get("segmentMaxSeconds", 15))
    if not maximum.isascii() or not maximum.isdigit() or not 5 <= int(maximum) <= 15:
        raise Rejected("每段上限须为 5–15 之间的整数秒")
    spec["segmentMaxSeconds"] = int(maximum)
    if spec.get("packaging", "full") not in ("full","trial"):
        raise Rejected("交付范围须选择整集或限定段落")
    if spec.get("targetDuration", "story_driven") not in ("story_driven","over_60"):
        raise Rejected("目标时长须选择随故事决定或严格超过 60 秒")
    for key in ("filmFormat","sound","style","shootingFormat","shotRhythm","postText"):
        if key in spec and not isinstance(spec[key],str):
            raise Rejected("作品形态、画面与声音要求须为文本")
    for key, choices in {"h3Resolution": ("default","480p","768p","1080p"), "h3InstanceType": ("default","plus","ultra")}.items():
        if spec.get(key) not in (None,"",*choices):
            raise Rejected("不支持的 H3 分辨率或实例档位")
    if spec.get("h3Candidates") not in (None,""):
        value = str(spec["h3Candidates"])
        if value not in ("1","2","4"):
            raise Rejected("H3 候选数须为 1、2 或 4")
        spec["h3Candidates"] = int(value)
    drawing=spec.get('productDrawingReferences',[])
    if not isinstance(drawing,list) or not all(isinstance(code,str) and code.strip() for code in drawing):
        raise Rejected('允许仅作绘图参考的商品须提供商品编号列表')
    return spec


H3_EXPORT_FIELDS = {"h3Resolution":"resolution", "h3Candidates":"candidates", "h3InstanceType":"instanceType"}


def creative_spec(spec):
    return {k:v for k,v in spec.items() if k not in H3_EXPORT_FIELDS}


def h3_preset_defaults(spec):
    return {param:spec[key] for key,param in H3_EXPORT_FIELDS.items() if spec.get(key) not in (None,"")}


def parse_aspect_ratio(value):
    if not isinstance(value, str) or len(value) > 80 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?:[0-9]+(?:\.[0-9]+)?', value):
        raise ValueError('aspect_ratio须为正数宽:高，如9:16、16:9、1:1、2.39:1')
    w, h = (Fraction(part) for part in value.split(':'))
    if w <= 0 or h <= 0:
        raise ValueError('画幅宽高必须大于0')
    return w / h


def check_generation_aspect(settings, episode_ratio):
    expected = parse_aspect_ratio(episode_ratio)
    if isinstance(settings, dict) and "aspect_ratio" in settings and parse_aspect_ratio(settings["aspect_ratio"]) != expected:
        raise ValueError("生成设置画幅与本集aspect_ratio不一致")


def aspect_matches(width, height, expected):
    return width > 0 and height > 0 and math.isclose(float(width / height), float(parse_aspect_ratio(expected)), rel_tol=.005)


def grid_dimensions(raw, target):
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        size = image_size(raw)
        if not size:
            raise ValueError("九宫格 PNG 原文件损坏，无法解码")
        if aspect_matches(*size, target):
            return size
        raise ValueError(f"九宫格须为与本集{target}画幅一致的PNG，原图为{size[0]}×{size[1]}")
    raise ValueError(f"九宫格须为与本集{target}画幅一致的PNG")


def frame_geometry(data, read_asset):
    """Check only the episode/segment ratios and the actual grid delivered to each segment."""
    target = data.get("aspect_ratio")
    check_generation_aspect(data.get("generation_settings"), target)
    assets = {a["id"]: a for a in data["assets"]}
    grids = {}
    segments = data["segments"]
    if not isinstance(segments, list) or not segments:
        raise ValueError("没有可核对画幅的分段")
    for seg in segments:
        sid = seg["id"]
        try:
            check_generation_aspect(seg.get("generation_settings"), target)
            strategy = seg.get("reference_strategy", seg.get("strategy"))
            if strategy == "flex":
                continue
            if strategy != "grid":
                raise ValueError("缺少参考策略")
            refs = seg["references"]
            if len(refs) != 1:
                raise ValueError("grid必须仅输入一张九宫格")
            asset = assets[refs[0]["asset_id"]]
            if asset.get("type") != "image" or asset.get("category") != "grid":
                raise ValueError("grid必须仅输入一张九宫格")
            raw = read_asset(asset)
            if sha256_bytes(raw) != asset["sha256"]:
                raise ValueError("九宫格原图与素材表指纹不符")
            try:
                width, height = grid_dimensions(raw, target)
            except ValueError as exc:
                raise ValueError(f"{asset.get('path') or asset['id']}：{exc}") from exc
            grids[sid] = dict(asset=asset["id"], width=width, height=height)
        except (KeyError, OSError, ValueError) as exc:
            raise ValueError(f"{sid}：{exc}") from exc
    return dict(target=target, segments=len(segments), grids=grids)
