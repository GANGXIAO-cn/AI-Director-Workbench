"""Freeze editing intent in a full static delivery using the adopted work and explicit directions."""
import json


def group_records(db, eid):
    return [dict(r) for r in db.execute(
        "SELECT g.id,g.name,g.shared_limits,m.chapter,m.ordinal,m.ending,m.next_hook FROM group_member m "
        "JOIN story_group g ON g.id=m.group_id WHERE m.episode_id=? ORDER BY g.id", (eid,))]


def directions(db, eid):
    # These settings may change after adoption. Record their current revisions at delivery.
    keeps = []
    from .workflow import ORDER
    for stage in (*ORDER, "publish"):
        row = db.execute("SELECT value FROM app_setting WHERE key=?", (f"step:{eid}:{stage}",)).fetchone()
        setting = json.loads(row[0]) if row else {}
        if setting.get("keep"):
            keeps.append(dict(stage=stage, revision=setting.get("revision",0), text=setting["keep"]))
    kickoff = db.execute("SELECT predecessor_ids,carried_facts FROM kickoff WHERE episode_id=?", (eid,)).fetchone()
    return dict(keeps=keeps, groups=group_records(db, eid), predecessors=json.loads(kickoff[0]), facts=json.loads(kickoff[1]))


def snapshot(ctx, did, publish):
    story = ctx["story"]
    anchors = []
    for scene in ctx["script"].get("scenes", []):
        points = [(label,scene[key]) for label,key in (("表演","performance"),("反应","response"),("回收","payoff"))
                  if isinstance(scene.get(key),str) and scene[key].strip()]
        if points:
            anchors.append(dict(scene=scene["id"], points=points))
    keeps = []
    effects = []
    for shot in ctx["plan"]["shots"]:
        if shot.get("mustKeep") or shot.get("placement"):
            keeps.append({k:shot[k] for k in ("id","segment","action","performance","reaction","placement","mustKeep")
                          if shot.get(k)})
        if shot.get("sound") or shot.get("cut"):
            effects.append({k:shot[k] for k in ("id","segment","action","sound","cut") if shot.get(k)})
    return dict(format=2, revision=did, episode=ctx["ep"]["display_code"], title=publish["englishTitle"],
                adopted=ctx["adopted"], story={k:story[k] for k in ("synopsis","spine","causes","handoff") if story.get(k)},
                directions=ctx["directions"], anchors=anchors, shots=keeps, effects=effects, required=ctx["requiredImages"],
                products=[dict(code=p["code"],name=p["product"]["name"],segment=p["segment"],shot=p["shot_id"],
                               intent=p["intent"],image=p["scene_version"]) for p in ctx["placements"]],
                sounds=[dict(segment=s["id"],sound=ctx["prompts"]["segments"][s["id"]].get("sound") or {}) for s in ctx["segs"]],
                music=publish["music"], cues=publish["cues"])


def render(data, legacy_labels=False):
    from .workflow import LABELS
    stage_names = {**LABELS, "publish":"发布文案"}
    if legacy_labels:
        # Old immutable handoffs contain the stage labels used when they were written.
        stage_names.update(story="01 故事梗概", characters="02 角色小传", world="03 世界观",
                           treatment="04 分场大纲", script="05 完整剧本", stress="06 压力测试",
                           asset_design="07 素材设计", acting="09 表演设计", plan="10 镜头设计", prompts="11 H3 提示词")
    if data["format"] not in (1, 2):
        raise ValueError("未知精剪交接格式")
    lines = [f"# {data['episode']} · {data['title']} 精剪交接", "", "制作意图交接，待接收实际视频。",
             f"交接修订 {data['revision']}", "", "## 来源版本", ""]
    for k,v in data["adopted"].items():
        if v:
            source = data.get("sourceFiles", {}).get(k)
            path = source["path"] if source else {"publish":"01_发布包.md","prompts":"episode.json"}.get(k)
            label = f"[{stage_names[k]}]({path})" if path else stage_names[k]
            lines.append(f"- {label} · {v}")
    lines += ["", "## 故事与观看回报", ""]
    story = data["story"]
    for k,title in (("synopsis","采用梗概"),("spine","故事脊"),("causes","因果核对"),("handoff","故事稿交接原文")):
        if story.get(k):
            lines += [f"### {title}", "", story[k], ""]
    for row in data["anchors"]:
        lines.append("- "+row["scene"]+" · "+" · ".join(f"{label}：{value}" for label,value in row["points"]))
    notes = data["directions"]
    if notes["groups"] or notes["predecessors"]:
        lines += ["", "### 连续集来源", ""]
        for g in notes["groups"]:
            lines.append(f"- 故事组 {g['name']}（{g['id']}），第 {g['ordinal']} 集")
            for key,label in (("shared_limits","共同约束"),("chapter","章节"),("ending","本集结尾"),("next_hook","后续承接")):
                if g[key]:
                    lines.append(f"  - {label}：{g[key]}")
        if notes["predecessors"]:
            lines.append("采用的前集 ID · "+"、".join(notes["predecessors"]))
        lines.extend(f"- 开工时承接的事实：{v}" for v in notes["facts"])
    lines += ["", "## 必留内容与调整边界", ""]
    for k in notes["keeps"]:
        lines.append(f"- {stage_names[k['stage']]}本步需求 R{k['revision']} · 保留要求原文：{k['text']}")
    for r in data["shots"]:
        context = " · ".join(str(r[k]) for k in ("action","performance","reaction","placement") if r.get(k))
        tag = "采用方案标注必留" if r.get("mustKeep") else "采用方案入景位置"
        lines.append(f"- {tag} · {r['segment']}/{r['id']} · {context}")
    for rule in data["required"]:
        source = "用户指定" if rule["selectedBy"] == "user" else "既有选择记录"
        lines.append(f"- {source}的生成参考原图 · {rule['name']} · 版本 {rule['versionId']} · "
                     f"已用于 {'、'.join(rule['usedHere'])}，目标镜头 {'、'.join(rule['shotIds']) or '未指定'}。"
                     f"选择理由：{rule['reason'] or '旧记录未填写'}。")
        if rule["limitations"]:
            lines.append("  - 适用边界："+rule["limitations"])
    if data["products"]:
        lines += ["", "### 商品入景必留", ""]
        for p in data["products"]:
            lines.append(f"- 项目商品要求 · {p['code']} {p['name']} · {p['segment']}/{p['shot']} · {p['intent']}。"
                         f"场面版本 {p['image']}。剪辑时保留自然可辨的入景，可在满足该要求时压缩或换角度。")
    if not (notes["keeps"] or data["shots"] or data["required"] or data["products"]):
        lines.append("本次交付未登记额外必留项。")
    lines += ["", "## 节奏、声音与后期", "", f"剪辑配乐状态：{data['music']}。下列时间采用全片设计预算。", ""]
    for r in data["sounds"]:
        sound = r["sound"]
        flags = ["全静音"] if sound.get("silent") else (
            ["有原生配乐" if sound.get("bgm") else "无原生配乐"] + list(sound.get("ambience") or []) + list(sound.get("dialogue") or []))
        lines.append(f"- {r['segment']} 声音设计 · "+"、".join(flags))
    for r in data["effects"]:
        lines.append(f"- {r['segment']}/{r['id']} · "+" · ".join(str(r[k]) for k in ("action","sound","cut") if r.get(k)))
    lines.extend(f"- {c.get('segment') or c.get('scene') or '全片'} · {c['at']} · {c['action']}" for c in data["cues"])
    lines += ["", "## 已知差异与未验证项", "",
              "已登记的原图适用限制见上述必留项。视频中的表演、动态连续性、实际声音及最终片长待看片核对。",
              "", "## 实际素材入口", "", ("本次静态包未附粗剪、剪辑工程、原片或独立配乐。对应路径待提供。" if data["format"] == 1 else "视频和配乐并行生成。分段全部采用、配乐就绪后，工作台自动将原片和音乐交给精剪。"),
              "", "## 接收说明", "", ("以最新明确修订和当前粗剪定位动作、反应与必留内容，记录实际位置。" if data["format"] == 1 else "按采用原片、可用区间和专家剪点定位动作与反应，按本集配乐安排节奏并混入原声。")+
              "保留原有制作记录；有混音限制、缺少素材或尚未实现的意图时，在接收记录中注明。"]
    return "\n".join(lines)+"\n"
