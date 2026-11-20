"""Single-segment live trial. Configure RUNNINGHUB_* in the environment.

python3 validation/h3_online_segment.py DELIVERY_ID SEGMENT [--probe-empty] [--submit]
Repeat with the same arguments to resume an existing cloud task. No production DB writes.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from workbench.storage import file_sha256


from workbench.videosegment import load_segment, review, generate


def run(args, out):
    account = dict(
        key=os.environ["RUNNINGHUB_API_KEY"],
        site=os.environ.get("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn").rstrip(
            "/"
        ),
        instance=os.environ.get("RUNNINGHUB_INSTANCE_TYPE"),
    )
    generate(
        args.data,
        args.delivery,
        args.segment,
        out,
        account,
        workflow=os.environ["RUNNINGHUB_H3_WORKFLOW_ID"],
        submit=args.submit,
        probe_empty=args.probe_empty,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery")
    parser.add_argument("segment")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--submit", action="store_true")
    action.add_argument("--review", action="store_true", help="用本集专家对已取回的试片做视觉评审")
    action.add_argument(
        "--state-at", type=float, metavar="SECONDS", help="从本段已审视频提取状态参考原帧"
    )
    parser.add_argument(
        "--probe-empty", action="store_true", help="试验在线图可选参考位传 null 是否生效"
    )
    args = parser.parse_args()
    if not all(
        x and x not in (".", "..") and Path(x).name == x
        for x in (args.delivery, args.segment)
    ):
        parser.error("分段或交付编号无效")
    out = args.data / "video-trials" / args.delivery / args.segment
    out.mkdir(parents=True, exist_ok=True)
    with (out / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.review:
            print(
                json.dumps(
                    review(args.data, args.delivery, args.segment, out)["visualReview"],
                    ensure_ascii=False,
                )
            )
        elif args.state_at is not None:
            from workbench.videoreview import state_frame

            report = json.loads((out / "expert/visual-review.json").read_bytes())
            media = out / "result.mp4"
            verdict = report["visualReview"]
            if (
                report["evidence"]["sha256"] != file_sha256(media)
                or not verdict["usableStart"] <= args.state_at <= verdict["usableEnd"]
            ):
                raise ValueError("请选择本段已审版本可用区间内的原帧")
            print(
                json.dumps(state_frame(media, out, args.state_at), ensure_ascii=False)
            )
        else:
            run(args, out)


if __name__ == "__main__":
    main()
