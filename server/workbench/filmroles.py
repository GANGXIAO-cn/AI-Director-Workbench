"""Model selections for the two production pages and their workers."""
import json

from .errors import NotFound, Rejected


def settings_key(episode_id, stage):
    if stage not in ("package", "finecut"):
        raise Rejected("请选择视频与配乐或精剪阶段")
    return f"film-executors:{episode_id}:{stage}"


def selections(db, episode_id, stage):
    key = settings_key(episode_id, stage)
    kickoff = db.execute("SELECT executor_plan FROM kickoff WHERE episode_id=?", (episode_id,)).fetchone()
    if not kickoff:
        raise NotFound("本集尚未开工")
    plan = json.loads(kickoff[0])
    settings = {k: json.loads(v) for k, v in db.execute(
        "SELECT key,value FROM app_setting WHERE key IN (?,?)", ("executor-defaults", key))}
    defaults = settings.get("executor-defaults", {})
    result = {role: plan.get(role) or defaults.get(role) or {"provider": "codex"}
              for role in ("create", "review")}
    result["expert"] = plan.get("expert") or defaults.get("expert") or result["create"]
    if stage == "package":
        # Before the video page had its own selector, rewrites followed the 09 writer.
        writer = db.execute("SELECT selection FROM stage_executor WHERE episode_id=? "
                            "AND stage='prompts' AND role='create'", (episode_id,)).fetchone()
        if writer:
            result["create"] = json.loads(writer[0])
    return {**result, **settings.get(key, {})}
