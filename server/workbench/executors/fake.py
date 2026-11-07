"""FakeExecutor: scripted results for tests. Never touches the network."""
import re

from .base import ExecOutcome, Executor, ExecRequest

STORY = dict(
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
    vid = ctx["reviewedVersionId"]
    seg = ctx["segment"]
    if seg:
        obs = [dict(objectId=seg, versionId=vid, unit="segment", anchor="00:00", evidence="看到角色与动作")]
    else:
        obs = [dict(objectId=vid, versionId=vid, unit="paragraph", anchor="梗概", evidence="冷读复述：阿福值夜班。")]
        obs += [dict(objectId=s, versionId=vid, unit="paragraph", anchor="梗概", evidence="已核对")
                for s in ctx["requiredSettings"]]
    out_issues = [dict(location=dict(objectId=seg or vid, versionId=vid, unit="segment" if seg else "paragraph",
                                     anchor=i.get("anchor", "第1段"),
                                     evidence=i.get("evidence", "原句")),
                       category=i.get("category", "clarity"), settingId=i.get("settingId"),
                       decision="return", reason=i.get("reason", "意见")) for i in issues]
    return dict(schemaVersion=1, kind="review", runId=ctx["runId"], inputFingerprint=ctx["inputFingerprint"],
                reviewedVersionId=vid, pass_=ctx["passName"], decision=decision, observations=obs, issues=out_issues)


class FakeExecutor(Executor):
    """script: list of callables(req) -> ExecOutcome | dict, consumed in order; default: valid result."""

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


def directed(req: ExecRequest, name: str):
    p = req.prompt
    sec = sections(p)
    request = sec.get("request", "")
    if MARK_QUOTA in request and name == "codex":
        return quota_exhausted(req)
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
    if MARK_CLARIFY in request and "clarification" in p and "澄清：" not in request:
        return clarify(req)
    kick = next((l for l in sec.get("kickoff", "").splitlines() if "【测试" in l), "")
    marked = request.strip() if "【测试" in request else kick.strip()
    if "synopsis" in writable:
        return draft(req, **dict(STORY, synopsis=STORY["synopsis"] + (("\n" + marked) if marked else "")))
    base = sec.get("base_version")
    if "scenes" in writable:
        story = sec.get("adopted_story", "")
        paras = [l for l in story.split("## 故事梗概")[-1].split("##")[0].splitlines() if l.strip()] or ["主角登场。"]
        scenes = [dict(id=f"场{i:02d}", heading="内景 夜", action=t) for i, t in enumerate(paras, 1)]
        if request.strip():
            scenes[-1]["action"] += f"（按要求：{request.strip()[:40]}）"
        return draft(req, scenes=_json.dumps(scenes, ensure_ascii=False))
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
    if "title" in writable:
        return draft(req, title="First Night Shift", body="Afu takes his first night shift.", suno_title="Night Shift",
                     suno_style="lofi, quiet", suno_exclude="vocals")
    return draft(req)
