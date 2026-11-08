"""Build a new method pack from reviewed line ranges at immutable Git revisions.

The recipe is deliberately editorial: a project example never enters the shared
pack by passing a keyword filter. Identical builds may be replayed in place.
"""
import argparse
import os
import tempfile
from functools import lru_cache

import delivery_v6
import production_v5
import script_v7
from source_impact import intervals, map_ranges, source_hunks
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STORY = "milo-leo-story"
VIDEO = "milo-leo-short-video"
PINS = {STORY: "a07840b5aa286d8f4a388d007ae6ba921a899024",
        VIDEO: "ab0fa75e3b2254a9341a8501a48be7577ab023eb"}

# source, source stem, lines, title, consuming stages (project-only entries below)
SHARED = [
    (STORY, "story-standard", "103-111,133-136", "审读依据与角色交接", "story story_review"),
    (STORY, "story-draft", "5-7,9,16,18,20-26,29-35,44-46", "可选的故事开发辅助", "story"),
    (STORY, "outline", "5-8,10,15,18-19", "故事脊与阶段设计", "story"),
    (STORY, "selection", "35-49", "机制与结构联合选择", "topic story"),
    (STORY, "selection", "51-53", "六问复核", "topic story_review"),
    (STORY, "topic-research", "21-27,52-60", "选题研究与证据", "topic"),
    (VIDEO, "story-handoff", "17-19,21-38,42-44", "承接已审故事与跨段动作", "script"),
    (VIDEO, "story-ip", "5-11,17-19,23", "剧情阶段与可见表演", "script"),
    (VIDEO, "plot-mechanisms", "5-7", "机制的表演落实", "script"),
    (VIDEO, "series-continuity", "15,21", "连续集与单集兑现", "script"),
    (VIDEO, "camera-rhythm", "22-34", "动作能量与反应", "script plan"),
    (VIDEO, "camera-rhythm", "5-20,36-68,72-90", "设备视点、观看窗口、声音与剪点", "plan"),
    (VIDEO, "scene-assets", "5-38", "分镜、分段与空间锁定", "plan space"),
    (VIDEO, "asset-library", "4-6", "复用资产与用途", "plan assets"),
    (VIDEO, "shot-references", "3-17", "逐镜选择实际输入", "plan assets"),
    (VIDEO, "character-contract", "3-5,11-13,17", "身份与逐段出镜", "cast plan"),
    (VIDEO, "image-selection", "9-30,53-57", "画面价值、用途与明确取舍", "assets"),
    (VIDEO, "visual-checklist", "5,7-14", "原图与对应镜头复核", "space cast assets comparison"),
    (VIDEO, "prompt-format", "5-16", "H3 原文查阅表", "prompts comparison"),
    (VIDEO, "prompt-format", "59-76,78-85,87-106,118-120,124-126,130-134", "参考、起幅、平铺时间段与声音", "prompts"),
    (VIDEO, "voice-dialogue", "11-15,23,29-31", "对白时窗与动作接续", "script prompts"),
    (VIDEO, "prompt-review", "3,9", "独立审查的冷读观察", "cold_read"),
    (VIDEO, "prompt-review", "3,9", "独立审查的对照依据", "comparison"),
    (VIDEO, "publish-pack", "5-7,17,19-25", "发布系统拼接与项目设置", "publish"),
    (VIDEO, "review-delivery", "26-40,92-96", "核验依据与返修路径", "delivery"),
    (VIDEO, "finecut-handoff", "5-11,15-23,27-31", "精剪交接与实际粗剪核对", "delivery"),
]
PROJECT = [
    (STORY, "selection", "39-45", "系列结构与时长参考", "topic story"),
    (STORY, "story-standard", "15-22", "系列风格", "topic story story_review"),
    (STORY, "story-draft", "62-76", "故事反例", "story"),
    (VIDEO, "examples", "1-12", "系列既有作品", "script"),
    (VIDEO, "product-placement", "1-1", "商品入景", "plan assets"),
    (VIDEO, "voice-dialogue", "5-9,17,21", "系列声音基准与台词示例", "prompts script"),
    (VIDEO, "publish-pack", "9-15,27-33", "系列发布与配乐", "publish"),
]
# Only substitutions at editorially selected passages. The prepared files retain
# full-file hashes, exact source ranges, and the recipe used to build them.
EDITS = {
    "可选的故事开发辅助": {"MILO": "主角"},
    "故事脊与阶段设计": {"MILO": "主角"},
    "机制与结构联合选择": {"MILO": "主角", "LEO": "其他角色", "女主人": "其他角色"},
    "六问复核": {"MILO": "主角", "LEO": "其他角色", "女主人": "其他角色"},
    "选题研究与证据": {"MILO": "主角"},
    "剧情阶段与可见表演": {"MILO": "主角"},
    "设备视点、观看窗口、声音与剪点": {"LEO托稳盒底后MILO松爪": "接手者托稳盒底后递出者松手"},
    "对白时窗与动作接续": {"英语原文": "项目指定语言的原文"},
    "H3 原文查阅表": {"每段默认无原生配乐": "是否使用原生配乐按本段声音方案"},
    "参考、起幅、平铺时间段与声音": {"cats_silent在声音区": "适用的项目声音约束在声音区"},
}


# Additions first shipped in v3. The earlier pack can always be reproduced
# byte-for-byte. Template paths deliberately stay distinct from references.
V3_SHARED = [
    (STORY, "selection", "3-9", "选题三问初筛", "topic story_review"),
    (STORY, "selection", "23-27,31-33,55-57", "可读选题与确认点", "topic"),
    (STORY, "story-draft", "11-14,37-42", "时间线与可读故事正文", "story"),
    (STORY, "story-standard", "56,60,64,66-97", "可读性、因果、时间与节奏的审读清单", "story_review"),
    (STORY, "templates/story-draft.md", "3-19", "故事梗概、角色表与交接模板", "story"),
    (VIDEO, "story-handoff", "5-11,15", "故事接收与版本依据", "script"),
    (VIDEO, "series-continuity", "5-7,23-25", "近期差异与已有故事组", "topic script"),
    (VIDEO, "templates/story-script.md", "3-42", "剧情阶段、分段预算与修订模板", "script"),
    (VIDEO, "templates/production-plan.md", "3-25,29-47,58-73,77-93", "镜头计划、时序与空间模板", "plan"),
    (VIDEO, "templates/production-plan.md", "13-18,91-93", "空间锁定与开场核对模板", "space"),
    (VIDEO, "character-contract", "19-21", "身份基准与实际上传的区分", "cast delivery"),
    (VIDEO, "templates/production-plan.md", "58-73", "资产职责、输入顺序与九格状态模板", "assets"),
    (VIDEO, "templates/review-record.md", "3-24,32-36", "审核对象、观察与处置模板", "delivery"),
    (VIDEO, "prompt-review", "10,13-15,27", "送审版本与问题处理", "comparison"),
    (VIDEO, "templates/finecut-handoff.md", "1-33", "完整精剪交接模板", "delivery"),
    (VIDEO, "continuous-production", "5-11,13-19,21-27,29-36,38-42", "跨集来源、依赖与复核", "group"),
    (VIDEO, "templates/group-production.md", "1-41", "跨集共享与验收模板", "group"),
]
V3_PROJECT = [
    (STORY, "selection", "3-33", "系列三问、近期差异与同批比较", "topic"),
    (STORY, "outline", "9-13", "系列反制与时长策略", "story"),
    (STORY, "story-standard", "98-101", "系列人物、风格与近期差异审读", "story_review"),
    (VIDEO, "visual-checklist", "6", "系列身份与服装视觉核对", "cast assets comparison"),
    (VIDEO, "asset-library", "8", "固定家庭场景与换装边界", "space assets"),
    (VIDEO, "templates/production-plan.md", "49-56", "商品入景规划模板", "plan assets"),
    (VIDEO, "templates/review-record.md", "26,30", "发布包与商品入景核对", "delivery"),
]
V3_EDITS = {
    "选题三问初筛": {"MILO":"主角", "普通宠物的追物/占窝/求抱":"仅符合角色类别的惯常反应", "英文标题":"项目指定语言的标题"},
    "可读选题与确认点": {"MILO":"主角"},
    "时间线与可读故事正文": {"MILO在哪":"主角在哪", "LEO在哪":"搭档在哪", "女主人在哪":"其他角色在哪",
                         "女主人上班不在家":"另一位角色上班不在家", "猫不说话，就写":"角色没有台词时，就写"},
    "可读性、因果、时间与节奏的审读清单": {"MILO":"主角", "主人收场":"另一位角色收场"},
    "剧情阶段、分段预算与修订模板": {"MILO":"主角"},
    "镜头计划、时序与空间模板": {"（未指定默认9:16）":"（取自本集开工规格）"},
    "完整精剪交接模板": {"<本集英文标题>":"<本集标题>"},
}


# In v4 each story passage has one owner per stage. Keep v2/v3 recipes immutable.
V4_STORY_ROWS = [
    (STORY, "story-standard", "1,5-13,24-37", "story_standard", "story story_review"),
    (STORY, "story-standard", "41-53,56,58,60,64", "故事阶段、因果推演与复读", "story story_review"),
    (STORY, "story-standard", "66-97,103-111", "可读性、因果、时间与节奏的审读清单", "story_review"),
    (STORY, "story-standard", "133-136", "审读依据与角色交接", "story story_review"),
    (STORY, "story-standard", "124-131", "故事标准的参考出处", "story_review"),
    (STORY, "story-draft", "5-16,18-26,29-35", "可选的故事开发辅助", "story"),
    (STORY, "story-draft", "37-46", "可读故事正文与作者复读", "story"),
    (STORY, "story-draft", "78-81", "从故事梗概复核角色交接", "script"),
    (STORY, "outline", "1-10,13-19", "故事脊与阶段设计", "story"),
]
V4_PROJECT_ROWS = [
    (STORY, "story-draft", "17,27", "系列时间线与固定外观引用", "story"),
    (STORY, "story-standard", "38-39", "系列空间关系与结局兑现", "story story_review"),
    (STORY, "outline", "11", "系列目标时长", "story"),
]
V4_EDITS = {
    "story_standard": {"MILO":"主角"},
    "故事阶段、因果推演与复读": {"MILO":"主角"},
    "可选的故事开发辅助": {"女主人上班不在家":"另一位角色上班不在家", "LEO在哪":"搭档在哪", "女主人在哪":"其他角色在哪"},
    "可读故事正文与作者复读": {"猫不说话，就写":"角色没有台词时，就写"},
    "故事脊与阶段设计": {"LEO或女主人":"对手", "实验另有E情境短事件":"E情境短事件", "实验E":"情境短事件结构"},
}


def build(ref, source_root, output, pins=None, reviewed_ranges=None):
    pins = pins or PINS
    if not re.fullmatch(r"short-film@v[1-9][0-9]*", ref):
        raise ValueError("invalid pack version")
    if set(pins) != set(PINS) or not all(re.fullmatch(r"[0-9a-f]{40}", v) for v in pins.values()):
        raise ValueError("每个源仓库均须提供完整提交号")
    overrides = dict(reviewed_ranges or {})
    @lru_cache(maxsize=None)
    def read_source(repo, path):
        return subprocess.check_output(["git", "-C", source_root / repo, "show", f"{pins[repo]}:{path}"])
    files, project_files, stages, expected = {}, {}, {}, {}
    expanded = int(ref.split("@v")[1]) >= 3
    shared = SHARED + (V3_SHARED if expanded else [])
    annex = PROJECT + (V3_PROJECT if expanded else [])
    story_v4 = int(ref.split("@v")[1]) >= 4
    if story_v4:
        replaced = {r[3] for r in V4_STORY_ROWS} | {"时间线与可读故事正文"}
        shared = [r for r in shared if r[3] not in replaced] + V4_STORY_ROWS
        annex = [r for r in annex if r[3] != "系列反制与时长策略"] + V4_PROJECT_ROWS
    version = int(ref.split("@v")[1])
    production = version >= 5
    substitution_tables = [EDITS]
    if expanded:
        substitution_tables.append(V3_EDITS)
    if story_v4:
        substitution_tables.append(V4_EDITS)
    for minimum, recipe in ((5,production_v5),(6,delivery_v6),(7,script_v7)):
        if version < minimum:
            continue
        substitution_tables.append(recipe.EDITS)
        for catalog, additions in [(shared, recipe.SHARED), (annex, recipe.PROJECT)]:
            replaced = {item[3] for item in additions}
            catalog[:] = [item for item in catalog if item[3] not in replaced] + additions
    for project, rows in [(False, shared), (True, annex)]:
        for repo, stem, ranges, title, uses in rows:
            path = stem if "/" in stem else f"references/{stem}.md"
            raw = read_source(repo, path)
            lines = raw.decode().splitlines()
            if title == "商品入景":
                ranges = f"1-{len(lines)}"
            elif title in overrides:
                choice = overrides.pop(title)
                if choice.get("commit") != pins[repo] or choice.get("path") != path or not isinstance(choice.get("lines"),str):
                    raise ValueError(f"{title} 的人工选段须对应目标提交和原文件")
                ranges = choice["lines"]
            elif pins[repo] != PINS[repo]:
                hunks = source_hunks(source_root / repo, PINS[repo], pins[repo], path)
                mapped = map_ranges(ranges, hunks)
                if mapped["review"]:
                    raise ValueError(f"{title}（{path}）须核对新行范围：" + "；".join(mapped["review"]))
                ranges = mapped["lines"]
            parts = []
            for left, right in intervals(ranges):
                if right > len(lines):
                    raise ValueError(f"invalid range {stem}:{ranges}")
                parts.append("\n".join(lines[left:right]))
            text = "\n\n".join(parts)
            edits = {}
            for table in substitution_tables:
                edits.update(table.get(title, {}))
            for old, new in edits.items():
                text = text.replace(old, new)
            if title == "机制与结构联合选择":
                text = "\n".join(line.rsplit("|", 2)[0] + "|" if line.startswith("|") else line for line in text.splitlines())
                text = text.replace("（M30–M33）", "").replace("进入S2时", "进入故事阶段时")
            if title == "机制的表演落实":
                text = "项目采用无对白表演时，" + text
            if title == "独立审查的冷读观察":
                text = text.split("再提供用户要求")[0].rstrip("。") + "。"
            if not project and re.search(r"milo|leo|puggy|女主人|suno|fourthwall|/Users/|N\d{2}", text, re.I):
                raise ValueError(f"project data in {title}: " + str(re.findall(r'.{0,9}(?:milo|leo|puggy|女主人|suno|fourthwall|/Users/|N\d{2}).{0,9}',text,re.I)))
            name = ("project/" if project else "sections/") + f"{title}.md"
            if title == "story_standard":
                name = "story/story-standard.md"
            data = (text.strip() + "\n").encode()
            expected[name] = data
            record = dict(path=name, sha256=sha256(data).hexdigest(), source=dict(
                repo=repo, path=path, commit=pins[repo], sha256=sha256(raw).hexdigest(), lines=ranges))
            (project_files if project else files)[title] = record
            for stage in uses.split():
                stages.setdefault(stage, []).append(title)
    if overrides:
        raise ValueError("未识别的选段：" + "、".join(overrides))
    # v1's generic contract is still used for response formatting and story review.
    previous = ROOT / "methods/short-film/v1"
    parent = json.loads((previous / "manifest.json").read_text())
    for key, entry in parent["files"].items():
        if story_v4 and key == "story_standard":
            continue
        if story_v4 and key == "story_draft":
            path = ROOT / "methods/tools/contracts/story-draft-v4.md"
            data = path.read_bytes()
            digest = sha256(data).hexdigest()
            expected[entry["path"]] = data
            files[key] = dict(path=entry["path"],sha256=digest,
                              source=dict(path=str(path.relative_to(ROOT)),sha256=digest))
            continue
        data = (previous / entry["path"]).read_bytes()
        assert sha256(data).hexdigest() == entry["sha256"]
        expected[entry["path"]] = data
        files[key] = dict(entry, source=dict(pack=parent["id"], path=entry["path"], sha256=entry["sha256"]))
    if story_v4:
        for stage in ("story","story_review"):
            stages[stage].remove("story_standard")
    stages["story"][:0] = ["story_standard", "story_draft"]
    stages["story_review"][:0] = ["story_review", "story_standard"]
    if production:
        production_v5.add_guides(files,stages,expected)
    manifest = dict(id=ref, source=pins, stages=stages, files=files, projectFiles={"puggy":project_files})
    expected["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2)+"\n").encode()
    def verify_existing():
        if any(not (output/path).is_file() or (output/path).read_bytes()!=data for path,data in expected.items()):
            raise ValueError("existing version differs; build a new version")

    if output.exists():
        verify_existing()
        return
    # Expose a complete version in a single rename. Competing identical builds can be replayed.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".method-build-", dir=output.parent) as scratch:
        staging = Path(scratch)/"pack"
        for path, data in expected.items():
            dest = staging/path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        try:
            os.rename(staging, output)
        except OSError:
            if not output.exists():
                raise
            verify_existing()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="short-film@v2")
    parser.add_argument("--source-root", type=Path, default=Path.home()/".codex/skills")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pins", type=Path, help="JSON mapping of source repository names to reviewed commit hashes")
    parser.add_argument("--reviewed-ranges", type=Path,
                        help='JSON keyed by section title, with target commit, source path and reviewed lines')
    args = parser.parse_args()
    pins = json.loads(args.pins.read_text()) if args.pins else PINS
    if set(pins) != set(PINS) or not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in pins.values()):
        parser.error("pins must specify each source repository by full commit hash")
    build(args.ref, args.source_root, args.output or ROOT/"methods"/"short-film"/args.ref.split("@")[-1], pins,
          json.loads(args.reviewed_ranges.read_text()) if args.reviewed_ranges else None)
