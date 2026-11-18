"""FakeExecutor: scripted results for tests. Never touches the network."""
import json
import re

from .base import ExecOutcome, Executor, ExecRequest

STORY = dict(
    title="第一次值夜班",
    synopsis="那天夜里，柴犬阿福第一次值夜班。他看见收银台抽屉没关，按规章把抽屉推回并登记。",
    cast="|角色ID|身份与作用|独立身份基准|\n|---|---|---|\n|afu|值夜班的柴犬，主角|需要|",
    handoff="目标时长约 70 秒；无特殊限制。",
)


def context(prompt: str) -> dict:
    """Read the fixed fields the service put into the prompt (as a real model would)."""
    def grab(label):
        found = re.findall(label + r"：(\S+)", prompt)
        return found[-1] if found else None   # the fixed fields are always the trailer
    base = grab("基版")
    return dict(runId=grab("运行号"), inputFingerprint=grab("输入指纹"), targetId=grab("目标对象"),
                passName=grab("遍次") or "story", segment=grab("被审段"),
                baseVersionId=None if base in (None, "null") else base, reviewedVersionId=grab("被审版本"),
                requiredSettings=re.findall(r"\[(\S+)\]（必守）", prompt))


def draft(req: ExecRequest, **fields) -> dict:
    ctx = context(req.prompt)
    values = fields or STORY
    return dict(schemaVersion=1, kind="draft", runId=ctx["runId"], inputFingerprint=ctx["inputFingerprint"],
                baseVersionId=ctx["baseVersionId"],
                changes=[dict(targetId=ctx["targetId"], field=k, value=v) for k, v in values.items()])


def review(req: ExecRequest, decision="pass", issues=()) -> dict:
    ctx = context(req.prompt)
    vid = ctx["reviewedVersionId"] or ("G01" if ctx["passName"] == "plan" else "全文")
    seg = ctx["segment"]
    if seg:
        obs = [dict(objectId=seg, versionId=vid, unit="segment", anchor="00:00", evidence="看到角色与动作")]
        if ctx["passName"] == "comparison":
            obs.extend(dict(objectId=key, versionId=vid, unit="segment", anchor=seg, evidence="已对照必守设定")
                       for key in ctx["requiredSettings"])
        for block in re.findall(r"^===== director_decisions · [^\n]+ =====\n([^\n]+)", req.prompt, re.M):
            obs.extend(dict(objectId=d["code"], versionId=vid, unit="segment", anchor=seg, evidence="已对照决定")
                       for d in json.loads(block))
    else:
        obs = [dict(objectId=vid, versionId=vid, unit="paragraph", anchor="梗概", evidence="冷读复述：阿福值夜班。")]
        obs += [dict(objectId=s, versionId=vid, unit="paragraph", anchor="梗概", evidence="已核对")
                for s in ctx["requiredSettings"]]
    out_issues = [dict(location=dict(objectId=seg or vid, versionId=vid, unit="segment" if seg else "paragraph",
                                     anchor=i.get("anchor", "第1段"),
                                     evidence=i.get("evidence", "原句")),
                       category=i.get("category", "clarity"), settingId=i.get("settingId"),
                       **({"decisionId":i["decisionId"]} if "decisionId" in i else {}),
                       decision="return", reason=i.get("reason", "意见")) for i in issues]
    return dict(schemaVersion=1, kind="review", runId=ctx["runId"], inputFingerprint=ctx["inputFingerprint"],
                reviewedVersionId=vid, pass_=ctx["passName"], decision=decision, observations=obs, issues=out_issues)


class FakeExecutor(Executor):
    """script: list of callables(req) -> ExecOutcome | dict, consumed in order; default: valid result."""

    strict_schema = True

    def __init__(self, name="fake", script=None, isolation="session", reads_images=True):
        self.name = name
        self.isolation, self.reads_images = isolation, reads_images
        self.script = list(script or [])
        self.calls: list[ExecRequest] = []

    def probe(self):
        return dict(provider=self.name, executable="fake", version="fake-1", authenticated=True,
                    structured_result="simulated", independent_review="session_isolation_simulated")

    def start(self, req: ExecRequest) -> ExecOutcome:
        self.calls.append(req)
        step = self.script.pop(0) if self.script else None
        result = step(req) if step else directed(req, self.name)
        if isinstance(result, ExecOutcome):
            return result
        if not step and result.get("kind") == "draft":
            source = sections(req.prompt).get("director_decisions")
            if source:
                fields = {change["field"]:change["value"] for change in result["changes"]}
                if fields.get("synopsis"):
                    location, quote = "梗概", fields["synopsis"].splitlines()[0]
                elif "scenes" in fields:
                    scene = _json.loads(fields["scenes"])[0]
                    location = scene.get("id") or scene.get("sceneId")
                    quote = scene.get("action") or scene.get("event") or scene.get("objective")
                else:
                    location, quote = None, None
                notes = []
                for d in _json.loads(source):
                    if quote and (d["scope"] == "episode" or location in d["scenes"]):
                        notes.append(dict(decision=d["code"], status="applied", where=location, quote=quote))
                    else:
                        notes.append(dict(decision=d["code"], status="not_applicable", reason="本次离线结构夹具未设计对应动作"))
                result["changes"].append(dict(targetId=context(req.prompt)["targetId"],field="decisionNotes",value=_json.dumps(notes,ensure_ascii=False)))
        if "pass_" in result:
            result["pass"] = result.pop("pass_")
        return ExecOutcome(ok=True, structured=result, session_id=f"fake-session-{len(self.calls)}",
                           executable="fake", version="fake-1",
                           isolation=dict(session=req.session_mode, tools="disabled"))


def quota_exhausted(req):
    return ExecOutcome(ok=False, error_class="quota", diagnostic="usage limit reached",
                       quota_resets_at="2026-11-07T20:00:00Z")


def clarify(req, question="只改动作，还是需要换机位？", options=("只改动作", "换机位")):
    ctx = context(req.prompt)
    return dict(schemaVersion=1, kind="clarification", runId=ctx["runId"], inputFingerprint=ctx["inputFingerprint"],
                question=question, options=list(options), relatedIds=[])


def proposal(req, items):
    ctx = context(req.prompt)
    return dict(schemaVersion=1, kind="proposal", runId=ctx["runId"], inputFingerprint=ctx["inputFingerprint"],
                items=items)


# ---------------------------------------------------------------- directed defaults (ADW_FAKE UI walkthroughs)
# Test directives a tester can type in the UI; they only steer the fake executor:
#   【测试：退回】 review returns an ordinary issue      【测试：必守违规】 review returns a required-setting conflict
#   【测试：澄清】 creation asks a clarification first    【测试：额度用尽】 codex reports quota exhausted
import json as _json

MARK_RETURN, MARK_VIOLATION, MARK_CLARIFY, MARK_QUOTA = "【测试：退回】", "【测试：必守违规】", "【测试：澄清】", "【测试：额度用尽】"


def sections(prompt: str) -> dict:
    out, cur = {}, None
    for line in prompt.splitlines():
        m = re.match(r"^===== (\S+) · .* =====$", line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
        elif line.startswith("===== "):
            cur = None
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def directed(req: ExecRequest, name: str, stage_reply=True):
    p = req.prompt
    sec = sections(p)
    request = sec.get("request", "")
    if MARK_QUOTA in request and name == "codex":
        return quota_exhausted(req)
    if req.purpose=='image_assess' and '只有 blocked 须写 defect' in p:
        ids=[m[1] for image in req.images if (m:=re.search(r'候选-([a-f0-9-]+)',image.name))]
        return dict(draft(req),kind='discussion',changes=[],directorRequest='',reply=_json.dumps([dict(versionId=i,verdict='usable') for i in ids]))
    if stage_reply and "directorRequest" in req.schema.get("properties", {}):
        if "你是本集写稿人" in request:
            proposal = dict(analysis="观众先看见送错蛋糕，悬念是学徒怎样发现失误。",directions=[
                dict(key="A",title="追船",story="学徒追上渡船，靠盒上店章找到寿星。"),
                dict(key="B",title="认错人",story="两个同名收件人争执，学徒用生日蜡烛找到真正的寿星。")],
                recommendation="A",reason="追船给行动一个可见的期限。")
            return dict(draft(req),kind="discussion",changes=[],directorRequest="",reply=_json.dumps(proposal,ensure_ascii=False))
        if "你是本集定案专家" in request:
            return dict(draft(req),kind="discussion",changes=[],directorRequest="",reply="选 B，把两人拿出同名票根作为中段转折，结尾由蜡烛兑现。")
        if "removedIds" in req.schema["properties"] and ("导演已经选择按讨论生成新稿" in p or "【测试：改稿】" in request):
            return dict(directed(req,name,stage_reply=False), directorRequest=request.strip(),reply="",removedIds=[])
        return dict(draft(req), kind="discussion", changes=[], directorRequest="", reply="本次只讨论当前稿件。", **({"removedIds":[]} if "removedIds" in req.schema["properties"] else {}))
    if req.kind == "topic_research":
        ref = re.search(r"^===== research_source · (R\d+) · ", p, re.M).group(1)
        return {"candidates":[dict(title=f"错拿的钥匙 {i+1}", premise=f"第{i+1}位值班员试图在交班前归还拿错的钥匙。",
                                   mechanism="两人互换线索", motivation="按时完成交班",
                                   difference="将发现遗失改为主动归还。",reference_id=ref) for i in range(req.schema["properties"]["candidates"]["minItems"])]}
    if req.kind == "free_chat":
        return {"reply":"已按本轮资料回应。"+request.strip()}
    if req.kind == "cut_explanation":
        at = int(re.search(r"观察切点（毫秒）：(\d+)", p).group(1))
        return dict(judgement="cut", reason="（替身）两图主色与构图完全不同，没有连续运动的物体，判断为切镜。",
                    evidence=[dict(atMs=max(0, at - 500), observation="（替身）切点前画面"),
                              dict(atMs=at + 500, observation="（替身）切点后画面")])
    if req.kind == "proposal":
        ctx = context(p)
        src = dict(objectId="story", versionId="adopted", unit="paragraph", anchor="第2段", evidence="见采用稿")
        return proposal(req, [dict(category=c, content=t, source=src, suggestedStrength=s, rationale=r) for c, t, s, r in [
            ("character", "主角认真过头、照章办事", "required", "笑点来自把小事当正式流程"),
            ("signature", "鹦鹉模仿收银提示音，常常帮倒忙", "variable", "首集结尾留下了假提示音"),
            ("space", "便利店通宵营业，夜班由主角和鹦鹉值守", "required", "夜间人手有限")]])
    if req.kind == "review":
        target = sec.get("reviewed_version", "") + sec.get("segment_txt", "")
        if MARK_VIOLATION in target:
            ctx = context(p)
            key = (ctx["requiredSettings"] or [None])[0]
            return review(req, "return", [dict(category="setting", settingId=key, anchor="第2段", evidence="随手走开",
                                               reason="发现异常却随手走开，与必守设定冲突"),
                                          dict(category="clarity", anchor="结尾", reason="结尾笑点有分歧")])
        if MARK_RETURN in target:
            return review(req, "return", [dict(anchor="第1段", reason=f"动机不清 {MARK_RETURN}")])
        return review(req)
    fields = re.search(r"可写字段：(.+)", p)
    writable = [f.strip() for f in fields.group(1).split(",")] if fields else []
    # Offline fixtures for the unified stages, distinguished by the explicit
    # stage contract rather than a semantic classifier.
    contract = re.search(r"^===== stage_contract · (\w+)\.txt =====$",p,re.M)
    unified = {
        "characters":dict(characters=[dict(id="afu",name="阿福",identity="值夜班的柴犬",goal="完成交接",behavior="主动检查异常")]),
        "world":dict(rules=[dict(id="night",rule="夜间只留值班员",choiceImpact="必须自己检查异常")]),
        "treatment":dict(scenes=[dict(id="场01",heading="便利店 夜",event="检查抽屉",result="完成登记")],durationBudget="8秒估计，非实测"),
        "stress":dict(retelling="值班员想完成交接，发现抽屉异常后推回并登记。",scope="已采用剧本",diagnosis="事件因果清楚；此为离线夹具，不评价创作质量。",issues=[],options=[],productionNotes=[]),
        "asset_design":dict(strategy="lightweight",assets=[dict(id="afu-base",kind="character",roleId="afu",title="阿福身份",description="值夜班的柴犬",imagePrompt="柴犬阿福的身份图"),dict(id="store",kind="scene",description="夜间便利店收银台",imagePrompt="夜间便利店收银台空景")]),
        "acting":dict(scenes=[dict(sceneId="场01",objective="完成检查",obstacle="异常抽屉",strategy="观察再处理",beats=["看向抽屉","推回并登记"])]),
    }
    if contract and contract[1] in unified:
        return draft(req, **{k:_json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in unified[contract[1]].items()})
    if MARK_CLARIFY in request and "clarification" in p and "澄清：" not in request:
        return clarify(req)
    kick = next((l for l in sec.get("kickoff", "").splitlines() if "【测试" in l), "")
    marked = request.strip() if "【测试" in request else kick.strip()
    if "synopsis" in writable:
        fields = dict(STORY, synopsis=STORY["synopsis"] + (("\n" + marked) if marked else ""))
        if "adaptation_source" in sec and "base_version" not in sec:
            source = _json.loads(sec["adaptation_source"].splitlines()[0])
            unit = source["units"][0]
            fields["adaptation"] = _json.dumps([dict(pack=source["pack"],unit=unit["code"],original=unit["original"],
                adapted="值班员从未关的抽屉发现交接问题",reason="保留发现线索的顺序，更换人物与场景")],ensure_ascii=False)
        return draft(req, **fields)
    base = sec.get("base_version")
    if "scenes" in writable:
        story = sec.get("adopted_story", "")
        paras = [l for l in story.split("## 故事梗概")[-1].split("##")[0].splitlines() if l.strip()] or ["主角登场。"]
        scenes = [dict(id=f"场{i:02d}", heading="内景 夜", action=t) for i, t in enumerate(paras, 1)]
        if request.strip():
            scenes[-1]["action"] += f"（按要求：{request.strip()[:40]}）"
        return draft(req, scenes=_json.dumps(scenes, ensure_ascii=False))
    if "shots" in writable and not base:
        shots = [dict(id="K01",segment="G01",order=1,duration_ms=8000,camera="固定中景",action="阿福推开店门。",visibleRoles=["afu"],propStates={},
                      origin=dict(scene="场01",line="测试分场"))]
        segments = [dict(id="G01",order=1,strategy="flex")]
        return draft(req, shots=_json.dumps(shots, ensure_ascii=False), segments=_json.dumps(segments))
    if "shots" in writable and base:
        plan = _json.loads(base)
        sel = re.search(r"（选区：(\{.*\})）", request)
        shot_id = _json.loads(sel.group(1)).get("shot") if sel else None
        for s in plan["shots"]:
            if s["id"] == shot_id or (not shot_id and s is plan["shots"][0]):
                s["action"] = s["action"] + "；" + request.split("\n")[0][:30]
                if "俯拍" in request:
                    s["camera"] = "正俯拍 近景"
        return draft(req, shots=_json.dumps(plan["shots"], ensure_ascii=False))
    if "prompt_scaffold" in sec and "segments" in writable:
        segments=_json.loads(sec["prompt_scaffold"])
        plan=_json.loads(sec["plan_layout"])
        for sid,item in segments.items():
            shots=[shot for shot in plan["shots"] if shot["segment"]==sid]
            item["txt"]="\n".join([*[f"Shot {n}: The subject moves into frame." for n,_ in enumerate(shots,1)],
                                     "overall_soundscape: Footsteps.", "non_diegetic_music: N/A"])

        return draft(req,segments=_json.dumps(segments,ensure_ascii=False))
    if "tempoBpm" in (req.schema or {}).get("properties", {}):
        props = req.schema["properties"]
        result = dict(title="First Night Shift", body="Afu takes his first night shift.", tags=[],
                      tempoBpm=90, suno_title="Night Shift", suno_style="quiet lofi", suno_exclude="vocals")
        basis = _json.loads(sec["publish_basis"])
        result["cues"] = [dict(segment=s["segment"], at=s["at"], action="轻柔进入") for s in basis["segments"]]
        if "extraFields" in props:
            result["extraFields"] = {k:"本集补充" for k in props["extraFields"]["properties"]}
        return result
    if "title" in writable:
        return draft(req, title="First Night Shift", body="Afu takes his first night shift.", suno_title="Night Shift",
                     suno_style="lofi, quiet, 90 BPM", suno_exclude="vocals")
    return draft(req)
