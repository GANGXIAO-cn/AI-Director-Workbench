"""Build the stage conversation from saved production receipts without launching work."""
import json
import os

from . import production_steps
from .cloudtask import timing
from .storage import write_atomic, canonical


def receipt(path):
    try:
        with path.open("rb") as stream:
            return json.load(stream), os.fstat(stream.fileno()).st_mtime
    except FileNotFoundError:
        return {}, None


def finecut_reply(saved):
    reply = saved.get("result") or saved.get("plan") or saved
    action = reply.get("action", "")
    return dict(action={"adopt":"采用", "pass":"通过", "revise":"返修"}.get(action, action),
                text=reply.get("reason") or reply.get("reply") or reply.get("summary") or reply.get("finding",""),
                selection=saved.get("selection"))


def keep_previous_reply(path):
    # The current receipt is replaced on recuts. Keep only its displayed message.
    previous, at = receipt(path)
    if previous:
        history_path = path.with_suffix(".messages.json")
        history, _ = receipt(history_path)
        history = dict(history)
        history[str(at)] = finecut_reply(previous)
        write_atomic(history_path, canonical(history))


def messages(queue, delivery, stage, status):
    result = []
    root = queue.root / delivery

    def add(key, at, role, title, text="", selection=None):
        if at:
            selection = selection or {}
            result.append(dict(id=key, at=at, role=role, title=title, text=text,
                               provider=selection.get("provider"), requestedModel=selection.get("model")))

    def steps(folder, key, title):
        saved = production_steps.read_step(folder)
        for entry in saved.get("history", [saved] if saved else []):
            label = entry["label"]
            role = next((r for r in ("写稿 AI", "独立评审", "专家") if label.startswith(r)), "工作台")
            add(f"{key}/step/{entry['since']}", entry["since"], role, title, label)

    def verdict(path, key, role, title):
        saved, at = receipt(path)
        reply = saved.get("result") or {}
        action = {"adopt":"采用", "pass":"通过", "revise":"返修"}.get(reply.get("action"), reply.get("action"))
        if reply:
            add(key, at, role, title + (" · " + action if action else ""), reply.get("reason",""), saved.get("selection"))

    if stage == "finecut":
        folder = root / "finecut"
        steps(folder, "cut", "精剪")
        def saved_replies(path, role, title):
            old, _ = receipt(path.with_suffix(".messages.json"))
            current, at = receipt(path)
            entries = dict(old)
            if current:
                entries[str(at)] = finecut_reply(current)
            for timestamp, row in entries.items():
                label = title + (" · "+row["action"] if row["action"] else "")
                add(f"{path.relative_to(folder)}/{timestamp}", float(timestamp), role, label, row["text"], row["selection"])

        saved_replies(folder/"剪辑表/finecut_plan.json", "写稿 AI", "剪辑表已完成")
        saved_replies(folder/"精剪过程/expert-plan.json", "写稿 AI", "剪辑表已完成")
        feedback = folder / "精剪过程/反馈验收"
        saved_replies(feedback/"result.json", "独立评审", "导演反馈验收")
        saved_replies(feedback/"expert/finecut_decision.json", "专家", "反馈返修决定")
        for work in (folder, folder / "精剪过程"):
            review = work / "成片复核"
            saved_replies(review/"finecut_review.json", "独立评审", "成片复核")
            saved_replies(review/"finecut_decision.json", "专家", "交片决定")
        cut = status.get("cut") or {}
        add("cut/state", cut.get("endedAt"), "工作台", cut.get("state",""), cut.get("error",""))
    else:
        for job in status["jobs"]:
            segment = job["segment"]
            revisions = queue.directory(delivery, segment, 0).parent / "prompt-revisions"
            for path in revisions.glob("*/edits.json"):
                saved, at = receipt(path)
                numbers = sorted({edit["shot"] for edit in saved.get("edits", [])})
                if numbers:
                    add(f"{segment}/writer/{path.parent.name}", at, "写稿 AI", segment+" · 局部提示词已修订",
                        "已修订提示词第 " + "、".join(map(str, numbers)) + " 镜。", saved.get("writer"))
            for number in range(job["round"] + 1):
                folder = queue.directory(delivery, segment, number)
                key = f"{segment}/{number}"
                title = f"{segment} · " + (f"第 {number} 次返工" if number else "首版")
                steps(folder, key, title)
                for check in (folder/"prompt-checks").glob("*/prompt_confirmation.json"):
                    verdict(check, key+"/prompt/"+check.parent.name, "专家", title+" · 提示词确认")
                cloud = timing(folder)
                add(key+"/cloud", cloud.get("startedAt"), "工作台", title,
                    "已开始跟踪视频任务" if cloud.get("observedOnly") else "H3 已接单")
                review = folder / "expert"
                saved, at = receipt(review / "independent-review.json")
                opinion = saved.get("visualReview")
                if opinion:
                    text = "\n\n".join(v for v in (opinion.get("reason"), opinion.get("localChange"), opinion.get("cutAdvice")) if v)
                    add(key+"/review", at, "独立评审", title+" · 审片意见", text, saved.get("evidence",{}).get("expert"))
                verdict(review/"video_decision.json", key+"/decision", "专家", title+" · 审片决定")
                verdict(review/"limit_decision.json", key+"/final", "专家", segment+" · 最终选片")
                if not opinion and not (review/"video_decision.json").is_file():
                    legacy, saved_at = receipt(review/"visual-review.json")
                    final = legacy.get("visualReview") or {}
                    if final:
                        add(key+"/legacy", saved_at, "工作台", title+" · 已保存的审片记录", final.get("reason",""))
        music = status.get("music") or {}
        cloud = music.get("cloud") or {}
        add("music/start", cloud.get("startedAt"), "工作台", "本集配乐",
            "已开始跟踪配乐任务" if cloud.get("observedOnly") else "配乐服务已接单")
        if music.get("audio") or music.get("state")=="云端失败":
            add("music/end", cloud.get("endedAt"), "工作台", "本集配乐", music.get("action") or music["state"])
    return sorted(result, key=lambda row:row["at"])
