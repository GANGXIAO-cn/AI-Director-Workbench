"""Use the episode's own expert to find visible state changes before dispatch."""
from dataclasses import replace
import fcntl
import json
from pathlib import Path
import sqlite3

from jsonschema import ValidationError, validate

from .executors.base import ExecRequest
from .storage import canonical, file_sha256, sha256_bytes, write_atomic
from .videosegment import episode_expert, script_segments


class RetryablePlanError(ValueError):
    """The expert service could not finish this request yet."""


def material(data, delivery):
    with sqlite3.connect(
        (data / "workbench.sqlite").as_uri() + "?mode=ro", uri=True
    ) as db:
        row = db.execute(
            "SELECT relpath,manifest_hash FROM delivery WHERE id=? AND state='deliverable'",
            (delivery,),
        ).fetchone()
    if not row:
        raise ValueError("请选择有效图文交付")
    root = data / row[0]
    m = json.loads((root / "manifest.json").read_bytes())
    if sha256_bytes(canonical(m)) != row[1] or m["deliveryId"] != delivery:
        raise ValueError("交付清单发生变化")

    def read(rel):
        path = root / rel
        if file_sha256(path) != m["files"][rel]:
            raise ValueError("采用版本发生变化")
        return json.loads(path.read_bytes())

    episode = read("episode.json")
    assets = {a["id"]: a for a in episode["assets"]}
    slots = {}
    for s in episode["segments"]:
        refs = [
            assets[r["asset_id"]]
            for r in sorted(s["references"], key=lambda r: r["upload_order"])
        ]
        slots[s["id"]] = [
            dict(
                name=a["name"],
                protected=bool(a.get("fixed_role") or a.get("category") in ("identity", "grid")),
            )
            for a in refs
            if a["type"] == "image"
        ]
    return slots, read


def validate_plan(slots, dependencies):
    from .videoqueue import VideoQueue

    grouped = {}
    for dep in dependencies:
        grouped.setdefault(dep["target"], []).append(dep)
    for target, group in grouped.items():
        try:
            VideoQueue.validate_deps(slots, target, group)
        except ValueError as exc:
            positions = ", ".join(str(d["slot"]) for d in group)
            raise ValueError(f"{target}（图位 {positions}）：{exc}") from exc
    return dependencies


def plan_context(slots, read, slot):
    script = read("交接来源/剧本.json")
    board = read("交接来源/制作方案.json")
    context = dict(
        segments=script_segments(script, board),
        shots=[
            {k: s.get(k) for k in ("id", "segment", "action", "continuity")}
            for s in board["shots"]
        ],
        images=slots,
    )
    return context, sha256_bytes(canonical([context, slot]))


def plan(data: Path, delivery, folder):
    slots, read = material(data, delivery)
    executor, slot, connection, _ = episode_expert(data, delivery)
    context, stamp = plan_context(slots, read, slot)
    receipt = folder / "continuity-plan.json"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "planning.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if receipt.is_file():
            saved = json.loads(receipt.read_bytes())
            if saved["hash"] == stamp:
                return validate_plan(slots, saved["dependencies"])
        fields = dict(
            source={"type": "string"},
            target={"type": "string"},
            slot={"type": "integer"},
            note={"type": "string"},
        )
        item = dict(
            type="object",
            properties=fields,
            required=list(fields),
            additionalProperties=False,
        )
        schema = dict(
            type="object",
            properties={"dependencies": {"type": "array", "items": item}},
            required=["dependencies"],
            additionalProperties=False,
        )
        work = folder / "planning" / "work"
        work.mkdir(parents=True, exist_ok=True)
        req = ExecRequest(
            "video-plan-" + stamp[:16],
            "video_continuity",
            "review",
            "从已采用剧本、分镜和图位找出确实要继承前段实际生成的物品或空间状态的关系。"
            "只返回剧情所需的承接，独立段放行。source 为较早来源段，target 为使用段。"
            "slot 是目标段的图片编号，从1开始，优先替换承担同一道具状态的旧图，保留固定身份图、分镜九宫格、场景与其他必需参考；"
            "适合追加时用下一个空位，最多九张，无法合理安放时不要猜换。note 说明需要从来源原片观察、承接的具体状态，供工作台核对和改写镜头。"
            + json.dumps(context, ensure_ascii=False),
            schema,
            "ephemeral",
            work,
            slot.get("model"),
            slot.get("effort"),
            connection=connection,
        )
        for attempt in (1, 2):
            answer = executor.start(req)
            if not answer.ok:
                message = "承接分析失败：" + str(answer.diagnostic)[:300]
                if answer.error_class in ("network", "timeout", "quota"):
                    raise RetryablePlanError(message)
                raise ValueError(message)
            try:
                validate(answer.structured, schema)
                deps = validate_plan(slots, answer.structured["dependencies"])
                break
            except (ValidationError, ValueError) as exc:
                reason = exc.message if isinstance(exc, ValidationError) else str(exc)
                if attempt == 2:
                    raise ValueError("承接方案经专家复核仍有错误：" + reason) from exc
                req = replace(req, attempt=2, run_id=req.run_id+"-repair", prompt=req.prompt
                              + "\n请保留有效承接，仅修正以下校验问题。连续追加允许一次占用多个相邻空位。\n"
                              + reason + "\n原方案：" + json.dumps(answer.structured, ensure_ascii=False))
        write_atomic(receipt, canonical(dict(hash=stamp, dependencies=deps)))
        return deps
