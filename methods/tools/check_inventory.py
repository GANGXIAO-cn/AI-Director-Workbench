"""Index the direct decisions in the six pinned validators alongside established parity evidence."""
import ast
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from freeze_sections import PINS, ROOT, VIDEO

SOURCES = ("validate_episode", "check_delivery", "check_publish_pack", "shot_rhythm", "prompt_review", "check_assets")
# Keys are line numbers in the immutable source commit. Values identify native rejection sites and sample names.
PUBLISH = {
    25: ("代码块未闭合", "unclosed"), 27: ("同一标题下", "duplicate"), 45: ("缺少或为空", "required"),
    50: ("含禁用字符", "character"), 52: ("发布标题须单行", "prefix"), 55: ("正文的行数", "line_count"),
    58: ("正文第一行须有剧情", "hook"), 60: ("正文的行数", "fixed_lines"), 62: ("正文重复了标题", "repeat_title"),
    64: ("正文不能写 link in bio", "link"), 66: ("话题标签须以固定标签", "hashtags"),
    71: ("话题标签须以固定标签", "fixed_tags"), 73: ("本集标签须符合数量", "extra_tags"),
    75: ("Exclude Styles 须排除", "vocals"), 77: ("Style of Music 须写 BPM", "bpm"),
    79: ("须注明 Instrumental", "instrumental"),
}
ROLES = {
    13: ('file(settings["series"]', "missing_series"), 16: ("固定角色清单须恰好包含", "wrong_roles"),
    19: ('file(settings["voice"]', "missing_voice"), 24: ("角色原图缺失或超出", "escaped_path"),
    27: ("角色原图指纹不匹配", "wrong_hash"), 29: ('{role} 缺少版本', "missing_version"),
}


RHYTHM = {
    69: ("重复的表头或列", "dup_header"), 73: ("列数与表头不同", "wrong_width"),
    76: ("缺少镜头充分性联合表或数据行", "missing_table"), 91: ("镜号不合法", "bad_id"),
    93: ("表文接续标记不一致", "marker"), 96: ("同镜延续须沿用上一镜号", "same_id"),
    98: ("起镜或切镜须使用新镜号", "new_id"), 101: ("联合表与正文时间不同", "time"),
    104: ("未在本段正文唯一命中", "shared_anchor"), 107: ("缺少{field}", "no_camera"),
    121: ("缺行、多行或错序", "wrong_segment"), 132: ("观看任务为空或重复", "duplicate_task"),
    134: ('warning("多任务"', "multi_task"), 140: ('warning("切镜疑点"', "cut_cue"),
    151: ('warning("长镜"', "long"), 158: ('warning("少镜段"', "few"),
    160: ("联合表包含TXT未覆盖", "extra_row"), 162: ('warning("全片同镜数"', "even"),
    171: ("须声明 shot_rhythm.version=2", "bool_version"),
}


DELIVERY_PAGE = {
    137: ("页面交付范围与检查范围不符","scope"), 142: ("正式页面缺少当前完整同页补选区","gallery"),
    145: ("正式页面缺少顶部未采用图片","gallery_navigation"), 169: ("补选区与正文ID冲突","id_collision"),
    173: ("页面英文标题不符","heading"), 175: ("页面段数不符","section_count"),
    177: ("页面锚点ID重复","duplicate_id"), 180: ("交付模板或样式不符","template"),
    183: ("页面链接缺失","missing_url"), 186: ("链接不是纯本地文件","not_local"),
    188: ("链接不存在","missing_file"), 194: ("网页缺少发布包正文或导航锚点","publish_nav"),
    198: ("发布包复制字段缺失","publish_fields"), 200: ("发布包音乐使用状态或剪辑对位表与MD不一致","publish_note"),
    202: ("发布包源文件链接缺失或映射错误","publish_source"), 207: ("本集文档入口缺失或顺序不符","no_doc"),
    210: ("本集文档入口映射错误","wrong_doc"), 212: ("分段导航缺失或顺序不符","no_segment_nav"),
    218: ("分段导航锚点映射错误","wrong_anchor"), 222: ("每段须有唯一完整pre","pre_count"),
    224: ("素材卡片布局缺失","no_grid"), 227: ("TXT与页面原文不符","txt_mismatch"),
    230: ("页面实际上传顺序不符","ref_order"), 233: ("页面作用镜头与实际清单不符","shot_label"),
    239: ("素材链接或标签映射错误","asset_label"), 242: ("素材预览映射错误","media"),
    244: ("TXT下载链接映射错误","txt_link"), 251: ("页面存在额外或缺少的预览/链接","extra_link"),
    260: ("复制脚本不符或存在其他脚本","extra_script"),
    155: ("页面角色设定内容、图片或适用段落缺失/失效","cast_body"), 159: ("页面缺少完整本集角色设定区","cast_roles"),
    163: ("页面角色基准缺项或错序","cast_asset_order"), 166: ("页面角色基准链接错配","cast_link"),
    171: ("补选区ID重复","gallery_duplicate_id"), 204: ("网页含有无源文件的发布包","orphan_publish"),
    249: ("缺本集角色设定导航","cast_navigation"),
}


ARCHIVE_INPUTS = {
    24: ("非法时间数值","time_type"), 31: ("raise ValueError(f\"文件缺失或超出本集目录","outside"),
    37: ("aspect_ratio须为正数宽:高","ratio_syntax"), 40: ("画幅宽高必须大于0","ratio_zero"),
    123: ("缺少文本字段","missing_purpose"), 134: ("无法识别图片文件头","header"),
    140: ("缺少资产表","missing_asset_table"), 146: ("重复资产ID","duplicate_asset"),
    149: ("非法资产媒体类型","invalid_type"), 151: ("非法资产类别","invalid_category"),
    155: ("资产来源方式非法或未审核","origin"), 161: ("固定角色不匹配现行角色基准","fixed_path"),
    163: ("固定角色文件不可用","fixed_category"), 166: ("非固定角色资产必须","local_absolute"),
    169: ("同一文件不能重复注册","repeated_path"), 173: ("资产指纹不符","wrong_hash"),
    177: ("图片尺寸非法","empty_size"), 186: ("缺少生成段","no_segments"), 193: ("重复段号","duplicate_segment"),
    197: ("时间不连续或本段时长","start_gap"), 200: ("非法reference_strategy","strategy"),
    205: ("未知label_style","labels"), 208: ("缺少实际输入","no_input"),
    214: ("输入上传顺序不连续","upload_order"), 216: ("引用未声明资产","unknown_asset"),
    221: ("重复标签","repeat_label"), 223: ("输入顺序与标签不一致","wrong_label"),
    225: ("同段重复输入同一文件","repeat_file"), 230: ("缺少作用镜头","shot_scope"),
    233: ("每段图片必须为1至9张","image_count"), 240: ("实际入口媒体数量限制","media_limits"),
    242: ("实际入口混合总限制","mixed_limits"), 245: ("每段须独立提示词文件","shared_txt"),
    251: ("缺少或重复约定中文段落","heading_missing"), 254: ("提示词章节顺序错误","heading_order"),
    258: ("未声明的主体标签","subject_token"), 261: ("提示词引用未声明标签","extra_ref"),
    266: ("参考标签必须有且只有一个非空定义","empty_definition"), 268: ("参考定义缺少输入标签","grid_definition"),
    271: ("grid必须仅输入一张九宫格","grid_extra"), 274: ("grid须有9个合法关键状态","grid_frame"),
    277: ("九宫格须为与本集","grid_ratio"), 281: ("时间码未覆盖本段或超出段长","outside_time"),
    285: ("本段未审核","unreviewed"),
    299: ("on_screen_roles缺失或含未知/重复角色","unknown_appearance_role"),
}


MANIFEST = {
    312: ("history", "须声明 schema_version=2", "schema_type"),
    314: ("history", "清单不使用每集标准快照字段", "legacy_field"),
    316: ("history", "须声明 h3_rules=v1", "h3_version"),
    329: ("specs", "生成设置画幅与本集aspect_ratio不一致", "segment_generation_ratio"),
    332: ("history", "缺少本集英文视频标题", "chinese_title"),
    336: ("history", "非法片长或duration_policy", "unknown_policy"),
    338: ("history", "over_60总时长必须严格大于60秒", "sixty_seconds"),
    343: ("archivechecks", "段落时长之和不等于总时长", "sum_differs"),
}


PROMPT_REVIEW = {
    14: ("须使用schema_version=2", "schema"), 17: ("prompt_review须列出", "sources"),
    25: ("重复段号：", "duplicate_segment"), 29: ("图片上传顺序与清单不一致", "upload_order"),
    61: ("审核记录包含未知段", "unknown_record"), 63: ("审核记录段号重复或结论冲突", "duplicate_record"),
    68: ("交付TXT或实际输入图片与送审文件不一致", "change_txt"),
    70: ('raise ValueError(sid + "：" + str(exc))', "bad_images_json"),
    74: ("缺少提示词独审段", "missing_record"),
}


def counterpart(source, line):
    production_scope = {307: ("delivery", "scope须为full或trial", "scope"),
                        358: ("deliverypage", "完整交付缺少03_精剪交接.md", "empty_handoff")}
    scope_proof = "图文范围与H3出包核对.json"
    if source == "validate_episode" and line in {52, 55, 67, 69, 80, 92, 94, 97, 101, 105, 107}:
        proof = json.loads((ROOT / "修复验证_20261108" / scope_proof).read_text())
        assert proof["sourceVideoStatus"]["status"] == proof["workbenchVideoStatus"]["status"] == "not_tested"
        return dict(status="范围外（外部成片实测）", target="AI导演工作台_R7_功能核对与决策记录_20261107.md:12",
                    evidence=scope_proof,
                    action="原技能仅在传入成片时调用 measure_video。图文交付保存计划时长，视频实测状态为 not_tested。")
    if source == "validate_episode" and line in production_scope:
        module, anchor, name = production_scope[line]
        evidence, sample = scope_proof, (anchor, name)
    elif source == "validate_episode" and line in ARCHIVE_INPUTS:
        module = "archivefiles" if line in (31,161,163,166) else "specs" if line in (37,40) else "archivechecks"
        evidence, sample = "历史材料规则故障对照.json", ARCHIVE_INPUTS[line]
    elif source == "validate_episode" and line in MANIFEST:
        module, anchor, name = MANIFEST[line]
        evidence, sample = "历史材料规则故障对照.json", (anchor, name)
    elif source == "prompt_review" and line in PROMPT_REVIEW:
        module, evidence, sample = "promptreceipt", "提示词独审与实际送审文件对照.json", PROMPT_REVIEW[line]
    elif source == "check_publish_pack" and line in PUBLISH:
        module, evidence, sample = "publishing", "历史发布包与故障对照.json", PUBLISH[line]
    elif source == "check_delivery" and line in DELIVERY_PAGE:
        module, evidence, sample = "deliverypage_check", "图文交付页模板与故障对照.json", DELIVERY_PAGE[line]
    elif source == "shot_rhythm" and line in RHYTHM:
        module, evidence, sample = "rhythm", "镜头充分性与节奏故障对照.json", RHYTHM[line]
    elif source == "check_assets" and line in ROLES:
        module, evidence, sample = "roleassets", "固定身份原图与故障对照.json", ROLES[line]
    else:
        return dict(status="待逐条核验", action="建立或定位工作台对应检查，并补齐单项样本。")
    path = Path("server/workbench") / (module + ".py")
    matches = [i for i, text in enumerate((ROOT / path).read_text().splitlines(), 1) if sample[0] in text]
    if len(matches) != 1:
        raise ValueError(f"对应位置不唯一 {source}:{line}")
    proof = json.loads((ROOT / "修复验证_20261108" / evidence).read_text())
    case = (proof.get("cases") or proof.get("faults"))[sample[1]]
    assert case.get("matchedRule") or (case["sourceOK"] is False and case["workbenchOK"] is False)
    status = "已实现并核对样本"
    extra = {}
    if source == "validate_episode" and line not in production_scope:
        status = "历史集导入已核对样本"
    if source == "check_delivery" and line in (155,159,163,166,249):
        status = "原格式角色页已核对样本"
        extra["action"] = "以原技能格式渲染后核对。新版工作台另有整集角色基准及分段绑定，见本集身份基准与分段送审核对.json。"
    if case.get("injection"):
        status = "已核对渲染器故障样本"
        extra["action"] = case["injection"]
    return dict(status=status, target=f"{path}:{matches[0]}", evidence=evidence, sample=sample[1], **extra)


def records(source):
    path = "scripts/" + source + ".py"
    raw = subprocess.check_output(["git", "-C", Path.home()/".codex/skills"/VIDEO, "show", PINS[VIDEO]+":"+path]).decode()
    tree = ast.parse(raw)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    results = []
    for node in ast.walk(tree):
        raised = isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and ast.unparse(node.exc.func) == "ValueError"
        warning = isinstance(node, ast.Call) and ast.unparse(node.func) == "warning"
        if not raised and not warning:
            continue
        current = node
        when = []
        while current in parents:
            parent = parents[current]
            if isinstance(parent, ast.If):
                predicate = ast.unparse(parent.test)
                when.append(predicate if current in parent.body else "not (" + predicate + ")")
            if isinstance(parent, ast.ExceptHandler):
                when.append("except " + (ast.unparse(parent.type) if parent.type else "*"))
            if isinstance(parent, ast.FunctionDef):
                break
            current = parent
        item = dict(path=path, line=node.lineno, function=parent.name, kind="error" if raised else "warning",
                    guards=list(reversed(when)), statement=ast.get_source_segment(raw, node))
        results.append(dict(**item, **counterpart(source, node.lineno)))
    return sorted(results, key=lambda r:r["line"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "methods/short-film/v3/检查对照.json")
    args = parser.parse_args()
    entries = [row for name in SOURCES for row in records(name)]
    summary = dict(Counter(r["status"] for r in entries))
    args.output.write_text(json.dumps(dict(source=VIDEO, commit=PINS[VIDEO], summary=summary, entries=entries),
                                 ensure_ascii=False, indent=2) + "\n")
    print(summary)
