"""The single film workflow. Storage keys are stable, labels describe professional tasks.

The writable artifacts below use workbench step numbers. Asset selection, review,
delivery verification and packaging are separate operator steps.
"""
STAGES = (
    ("story", "01 故事方向"), ("characters", "02 角色"),
    ("world", "03 世界规则"), ("treatment", "04 分场"),
    ("script", "05 剧本"), ("stress", "06 故事审查"),
    ("asset_design", "07 素材"), ("plan", "08 分镜"), ("prompts", "09 提示词"),
)
LABELS = dict(STAGES)
ORDER = tuple(LABELS)
# Historical artifacts stay readable, without re-entering the active stage sequence.
LABELS["acting"] = "历史表演稿"
LABELS["publish"] = "发布文案"
OPTIONAL = frozenset(("world", "stress"))
NEXT = dict(zip(ORDER, ORDER[1:]))
METHOD_REF = "unified-film@draft"

# Dependencies are explicit; optional outputs are read when adopted, otherwise
# the recorded skip reason is supplied. No semantic routing or external API needed.
DEPENDENCIES = {
    "story": (), "characters": ("story",), "world": ("story", "characters"),
    "treatment": ("story", "characters", "world"),
    "script": ("treatment", "story", "characters", "world"), "stress": ("script", "treatment", "story", "characters", "world"),
    "asset_design": ("script", "characters", "world", "stress"),
    "acting": ("script", "characters", "asset_design"),
    "plan": ("script", "asset_design", "characters", "world"),
    "prompts": ("plan", "script", "characters", "asset_design"),
    "publish": ("prompts",),
}

FIELDS = {
    "characters": ("characters", "notes"), "world": ("rules", "notes"),
    "treatment": ("scenes", "durationBudget", "notes"),
    "stress": ("retelling", "scope", "diagnosis", "issues", "options", "productionNotes", "notes"),
    "asset_design": ("strategy", "assets", "notes"),
    "acting": ("scenes", "notes"),
}
FIELDS = {stage: (*fields, "decisionNotes") for stage,fields in FIELDS.items()}
JSON_FIELDS = {(s, f) for s, fields in FIELDS.items() for f in fields
               if f in ("characters", "rules", "scenes", "issues", "options", "assets", "decisionNotes", "productionNotes")}

CONTRACTS = {
    "characters": "必须提交 characters 字段，内容为完整角色数组。只写 notes 或在备注中列举出场角色无法形成角色表。每个角色 id 稳定，写 name、identity、goal、obstacle、behavior、voice；identityRequired 为布尔值，不需要身份基准时写 identityReason。继承项目已有身份，区分本集状态与固定身份。notes 记录来源和未定项。",
    "world": "rules 为 JSON 数组，每项 id、rule、choiceImpact、evidence。只写影响人物选择或因果的规则，不强制奇幻设定或固定物件数量。notes 写规则边界。",
    "treatment": "scenes 为 JSON 数组，每场 id、heading、characters、event、result、durationEstimateSec；另写 timePoint（发生时间）、elapsedTime（距上一场多久）、rolePositions（角色编号到所在位置的对象）、stateChanges（状态变化）。场号稳定。写事件推进与结局，不写完整对白。durationBudget 是全片预算与依据文本，估计不等于实测。",
    "stress": "先只依据剧本正文，用自己的话复述谁想要什么、什么挡着、怎么办、结果怎样、最好看的地方在哪，写进 retelling。复述完成后再检查本集规格，对照其余材料。若有“待导演确认”规格说明，将说明单列在 diagnosis，写明缺项影响的判断，继续完成其余对照。规格缺项不得提问，不列入 issues 或 options，不发起返修。逐条核对导演决定，未落实的列入 issues，stage 只填应当修改的阶段编号 01、02、03、04 或 05。只诊断，不改写剧本。scope 写实际审查范围，diagnosis 写最弱环节。issues 为 JSON 数组，每项 id、stage、sceneIds、evidence、problem、severity。逐条给出 severity：blocking（必须修正：观众看不懂谁想要什么、为什么、结果怎么来、胜负怎么判，或违反必守设定、导演决定），normal（普通意见）。不影响整体故事的细节不列入 issues，写入 productionNotes。证据不足明确写出。options 为 JSON 数组，每项 issueId、repairScope、proposal。options 每项另写 changesCore（布尔）：该方案是否改变目标、结局或核心看点。productionNotes 为 JSON 数组，每项 id（P01 起）、sceneIds、note（要在出图或提示词中落实的细节），无则填 []。notes 标明需作者选择；报告采用不授权改稿。",
    "asset_design": "strategy 为 lightweight 或 premium。assets 每项写稳定 id、title、kind（character/scene/prop/state）、适用的 roleId、stateId、sceneIds、description、imagePrompt、evidenceStatus 和 dependsOn。只列剧本可确定的角色身份、空景、道具基准与状态变体，不列按镜头开场状态图和九宫格。已提供且编号对应的固定身份图填 verified，description 写复用项目固定身份图，imagePrompt 留空。仅有复用库摘要的候选由导演看图决定。其余需出图项写中文提示词。dependsOn 用其他条目的 id，状态变体引用同一角色身份条目，含固定场景时引用其锁定图，没有依赖用空数组。固定身份 stateId 用 default。三分法是可选手法，不默认写三分法构图。keyVisual 为布尔，旧稿缺省 false，每集最多三项；只用于非固定新角色的身份图或主场景首次出现。",
    "acting": "scenes 为 JSON 数组，每场 sceneId、objective、obstacle、strategy、beats、subtext、voiceDirection。两个及以上角色互动时，目标写成对搭档的意图；独角戏写要完成的具体动作和挡住它的物件、环境或时间，不补造搭档，不把道具写成有意图的对手；画外人只有在其行动实际改变本场局面时才算搭档。独角戏的搭档可省略或写“无”。对白时间不足就延长，不删改或加速。表演落实为可见行为，保留已采用情节与对白，不在本阶段设计机位或编译 H3 提示词。",
}


def check(stage, content, *, new_receipt=False):
    """Validate typed results, without pretending to score creative quality."""
    if stage not in FIELDS:
        return []
    errors = []
    required = {
        "characters": ("characters", "id", ("name", "identity", "goal", "behavior")),
        "world": ("rules", "id", ("rule", "choiceImpact")),
        "treatment": ("scenes", "id", ("heading", "event", "result")),
        "asset_design": ("assets", "id", ("kind", "description", "imagePrompt")),
        "acting": ("scenes", "sceneId", ("objective", "obstacle", "strategy", "beats")),
    }
    if stage in required:
        field, id_key, keys = required[stage]
        items = content.get(field)
        if not isinstance(items, list) or not items:
            if stage == "characters":
                return ["角色表为空，请在 characters 字段提交本集出场角色的完整小传。"]
            return [f"{LABELS[stage]}须有实际{field}记录"]
        seen = set()
        for n, item in enumerate(items, 1):
            if not isinstance(item, dict):
                errors.append(f"第 {n} 项须为对象")
                continue
            code = item.get(id_key)
            if not isinstance(code, str) or not code.strip() or code in seen:
                errors.append(f"第 {n} 项缺少唯一 {id_key}")
            else:
                seen.add(code)
            if stage == "characters" and "identityRequired" in item and type(item["identityRequired"]) is not bool:
                errors.append(f"第 {n} 项 identityRequired 须为布尔值")
            missing = [key for key in keys if not item.get(key) and not
                       (stage == "asset_design" and key == "imagePrompt" and item.get("evidenceStatus") == "verified")]
            if missing:
                errors.append(f"第 {n} 项缺少实际内容：{'、'.join(missing)}")
    if stage == "asset_design":
        from .worksheet import dependency_errors
        if not errors:
            errors.extend(dependency_errors(content["assets"]))
        key_visuals=[a for a in content.get("assets",[]) if isinstance(a,dict) and a.get("keyVisual") is True]
        if len(key_visuals)>3:errors.append("关键视觉每集最多三项")
        for asset in content.get("assets",[]):
            if not isinstance(asset,dict):continue
            if "keyVisual" in asset and type(asset["keyVisual"]) is not bool:errors.append("keyVisual 须为布尔值")
            if asset.get("keyVisual") and (asset.get("kind") not in ("character","identity","scene") or asset.get("kind") in ("character","identity") and asset.get("evidenceStatus")=="verified"):
                errors.append("关键视觉只用于新角色身份图或主场景首次出现")
        state_keys=set()
        for asset in content.get("assets",[]):
            if not isinstance(asset,dict) or not asset.get("stateId") or not asset.get("roleId"):
                continue
            if not all(isinstance(asset[k],str) for k in ("stateId","roleId")):
                errors.append("角色及状态编号须为文本")
                continue
            key=(asset["roleId"],asset["stateId"])
            if key in state_keys:
                errors.append("同一角色的状态编号须唯一")
            state_keys.add(key)
    if stage == "asset_design" and content.get("strategy") not in ("lightweight", "premium"):
        errors.append("素材策略须为 lightweight 或 premium")
    if stage == "stress":
        if any(not isinstance(content.get(f), str) or not content[f].strip() for f in ("retelling","scope", "diagnosis")):
            errors.append("故事审查须写明冷读复述、范围和诊断结论")
        for f in ("issues", "options"):
            if not isinstance(content.get(f), list):
                errors.append(f"{f} 须为 JSON 数组，无问题时填 []")
    if stage == "stress" and new_receipt:
        notes = content.get("productionNotes")
        if not isinstance(notes, list):
            errors.append("productionNotes 须为数组")
        else:
            for n, note in enumerate(notes, 1):
                if not isinstance(note, dict) or any(not isinstance(note.get(k), str) or not note[k].strip() for k in ("id", "note")):
                    errors.append(f"productionNotes 第 {n} 项 id、note 须为非空字符串")
                elif not isinstance(note.get("sceneIds"), list) or any(not isinstance(x, str) for x in note["sceneIds"]):
                    errors.append(f"productionNotes 第 {n} 项 sceneIds 须为字符串数组")
        for n, option in enumerate(content.get("options", []) if isinstance(content.get("options"), list) else [], 1):
            if not isinstance(option, dict) or type(option.get("changesCore")) is not bool:
                errors.append(f"options 第 {n} 项 changesCore 须为布尔值")
    return errors


STANDARD_GOALS = {'story': '按开工资料起草故事稿', 'characters': '根据已采用的故事稿和项目角色设定，起草本集出场角色的小传。', 'world': '根据已采用的故事和角色，整理会影响角色选择或事件因果的规则，逐条写明影响及依据。上游未确认的推断单列到备注，避免与已确认规则重复。', 'treatment': '按已采用的故事、角色及适用世界规则划分场次。逐场写明时间、地点、出场角色的位置、动作、结果、状态变化和估计时长，核对相邻场次的人物与道具连续性，汇总全片时长预算。', 'script': '根据已采用的分场大纲、故事、角色和世界规则逐场写完整剧本。保留场次顺序和人物行动因果，写清动作、对白及场间衔接。', 'stress': '根据本集采用稿及开工要求审查剧本，提交冷读复述、审查范围、具体问题、依据和可选返修方案；待导演选择返修范围。'}

STANDARD_GOALS["publish"] = "按已采用的制作内容和项目发布设置完成最后一步发布文案，填写标题、正文、标签及适用字段。"

CONTINUOUS_GOALS = {**STANDARD_GOALS, "asset_design": "按已采用剧本、角色标准和制作提醒起草素材清单，声明新角色身份图与主场景首次出现的关键视觉，最多三项。"}

CONTINUOUS_GOALS.update(plan="根据已采用剧本和实际素材，在同一分镜表内完成动作、表情、视线、摄影与声音设计。沿用剧本拍摄段，每个观看窗口1–3秒整秒；填写 visibleRoles 和影响行动的 propStates，保留已有素材用途及小差异接受结论。", prompts="根据采用分镜和绑定原图逐窗写自然语言提示词，并在本次写作内按短片技能完成内容通读与对照检查。修正遗漏、错译、错引和歧义，不重审整体方案或重新批准素材；不机械拼接字段，不另写自检证明。")
