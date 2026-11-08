"""Select the edition's explicit visual reference sections for GPT Image and H3."""
import re

from .errors import Rejected

SCOPE = "色卡仅供光色与构图参考。主体、服装、动作、空间、时长和声音以本集采用稿为准；模板中的主体占位符指本次请求的主体。"
BODY = "色卡主文档.md"
IMAGE = "prompts/conversational_prompts.md"
VIDEO = "prompts/video_prompts.md"
LEGACY_IMAGE = "prompts/stable_diffusion_prompts.md"


def section(text, title, *, fenced=False):
    """Return an exact text slice and its original one-based, inclusive line range."""
    lines = text.splitlines(keepends=True)
    headings = [(i, len(m[1]), m[2]) for i, line in enumerate(lines)
                if (m := re.fullmatch(r"(#{1,6})[ \t]+(.+)", line.strip()))]
    for index, (start, level, name) in enumerate(headings):
        if not re.fullmatch(title, name):
            continue
        end = next((i for i, depth, _ in headings[index+1:] if depth <= level), len(lines))
        start += 1
        if fenced:
            opening = next((i for i in range(start, end) if lines[i].strip() in ('```text', '```')), None)
            if opening is None:
                return None
            start = opening + 1
            closing = next((i for i in range(start, end) if lines[i].strip() == '```'), None)
            if closing is None:
                return None
            end = closing
        while start < end and not lines[start].strip():
            start += 1
        while end > start and lines[end-1].strip() in ("", "---"):
            end -= 1
        if start < end:
            return dict(text="".join(lines[start:end]).rstrip("\r\n"), heading=name, firstLine=start+1, lastLine=end)
    return None


def prompt_excerpt(prompts, path, heading, label, custom=False):
    text = prompts.get(path, "")
    if not text.strip():
        return "", None
    if custom:
        return text, dict(path=path, heading="用户填写", firstLine=1, lastLine=len(text.splitlines()))
    part = section(text, heading, fenced=True)
    if not part:
        raise Rejected(f"找不到{label}的提示词片段，请核对 {path}")
    return part.pop("text"), dict(path=path, **part)


def snapshot_inputs(card, custom=False):
    lighting = section(card["body"], r"(?:[一二三四五六七八九十]+、)?光影特征")
    colors = card["palette"]
    primary = [c["name_zh"] + " " + c["hex"] +
               (f'（{c["ratio"]:.0%}）' if not custom and "ratio" in c else "") for c in colors["primary"]]
    emotional = []
    for color in colors.get("emotional", []):
        name = color.get("name_zh") or color.get("emotion") or color["hex"]
        label = name + " " + color["hex"]
        if color.get("emotion") and color["emotion"] != name:
            label += "（" + color["emotion"] + "）"
        emotional.append(label)
    visual = [card["summary"], "主色：" + "、".join(primary)]
    if emotional:
        visual.append("情绪色：" + "、".join(emotional))
    if lighting:
        visual.append(lighting["text"])
    prompts = card["prompts"]
    image_file = LEGACY_IMAGE if custom and IMAGE not in prompts else IMAGE
    image, image_source = prompt_excerpt(prompts, image_file, r"(?:一、)?通用描述段.*", "对话式生图", custom)
    video, video_source = prompt_excerpt(prompts, VIDEO, r"(?:四、)?MiniMax H3(?:（海螺）)?", "H3", custom)
    return dict(scope=SCOPE, visual="\n".join(visual), image=image, video=video, imageSource=image_source, videoSource=video_source)


def stage_card(card, stage):
    excluded = {"video", "videoSource"} if stage == "assets" else {"image", "imageSource"}
    return {key: value for key, value in card.items() if key not in excluded}
