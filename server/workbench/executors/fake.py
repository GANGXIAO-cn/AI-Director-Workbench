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
        match = re.search(label + r"：(\S+)", prompt)
        return match.group(1) if match else None
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
        result = step(req) if step else (draft(req) if req.kind == "draft" else review(req))
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
