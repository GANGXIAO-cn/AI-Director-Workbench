"""Run only final Ref2VA assembly against an adopted episode in a test data directory."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from workbench.executors.base import ExecRequest
from workbench.executors.cli import CodexExecutor

SKILL = ROOT / "resources/skills/h3-prompt-writing"


def assemble(data, episode):
    db = sqlite3.connect((data / "workbench.sqlite").as_uri() + "?mode=ro", uri=True)
    adopted = {stage: (vid, json.loads(content)) for stage, vid, content in db.execute(
        "SELECT stage, version.id, content FROM artifact JOIN version ON adopted_version_id=version.id "
        "WHERE episode_id=? AND stage IN ('plan','script','prompts')", (episode,))}
    plan = adopted["plan"][1]
    baseline = adopted["prompts"][1]["segments"]
    root = data / "h3-skill-trial" / episode
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    # A single multimodal call can share the image set when bindings match across segments.
    orders = {tuple(r["assetVersionId"] for r in baseline[s["id"]]["refs"]) for s in plan["segments"]}
    if len(orders) != 1:
        raise ValueError("This shared-reference trial requires identical ordered image sets.")
    images = []
    for vid in orders.pop():
        relpath, = db.execute("SELECT relpath FROM asset_version WHERE id=?", (vid,)).fetchone()
        images.append(data / relpath)
    db.close()
    guide = "\n\n".join((SKILL / name).read_text() for name in
                        ("SKILL.md", "references/base-en.txt", "references/ref-en.txt"))
    packet = dict(script=adopted["script"][1]["shootingSegments"], segments=plan["segments"],
                  windows=plan["shots"], orderedReferences={s: e["refs"] for s, e in baseline.items()})
    prompt = (guide + "\nApply the installed skill as the final writer. Full-reference inputs. "
              "Return one complete standalone English Ref2VA prompt per segment. Use the supplied images "
              "in orderedReferences order. Preserve the storyboard cuts and within-shot action timing "
              "at whole-second boundaries, including continuous windows. Use the guide's cut notation. "
              "Preserve all sound and music choices. Return raw six-section text in each segment value.\n"
              + json.dumps(packet, ensure_ascii=False))
    ids = [s["id"] for s in plan["segments"]]
    schema = dict(type="object", properties={s: dict(type="string") for s in ids},
                  required=ids, additionalProperties=False)
    result = CodexExecutor().start(ExecRequest(str(uuid4()), "create", "draft", prompt, schema,
                                  "ephemeral", work, images=images, timeout=600, effort="medium"))
    if not result.ok:
        raise RuntimeError(result.diagnostic)
    report = dict(episode=episode, versions={k: v[0] for k, v in adopted.items()},
                  session=result.session_id, segments=result.structured)
    target = root / "comparison.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("episode")
    args = parser.parse_args()
    assemble(args.data.resolve(), args.episode)
