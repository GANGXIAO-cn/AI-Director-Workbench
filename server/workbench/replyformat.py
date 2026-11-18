"""Translate direct manuscript fields to the existing internal result contract."""
import json


def document_to_internal(run, task, reply, dialogue=None, writable=()):
    """新稿（非对话）也接受“裸稿件”：顶层直接是字段（实测 153：Antigravity 两次 09 回复都是这种写法）。
    不在可写范围的键原样交给 _precheck 报越界，不在这里放过或丢弃。对话仍须 {kind, …, fields}。"""
    if isinstance(reply, dict) and "fields" not in reply and not dialogue:
        bare = {k: v for k, v in reply.items() if k != "kind"}
        if bare and any(k in writable for k in bare):
            reply = dict(kind=reply.get("kind", "draft"), fields=bare)
    if not isinstance(reply, dict) or not isinstance(reply.get("fields"), dict):
        raise ValueError("回复须为 JSON 对象：新稿直接写要修改的字段，对话写 {kind, reply, directorRequest, fields}")
    result = dict(schemaVersion=1, runId=run["id"], inputFingerprint=run["input_hash"],
                  kind=reply.get("kind", "draft"), baseVersionId=run["base_version_id"],
                  changes=[dict(targetId=task["artifact_id"], field=k,
                                value=v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                           for k, v in reply["fields"].items()])
    if dialogue:
        result.update(reply=reply.get("reply", ""), directorRequest=reply.get("directorRequest", ""))
        if dialogue.get("auto"):
            result["removedIds"] = reply.get("removedIds", [])
    return result


def bind_writing_reply(run, task, reply, *, selectable_kind=False):
    """Bind the result of this executor invocation to the frozen draft target."""
    if not isinstance(reply, dict):
        return reply
    result = dict(reply, schemaVersion=1, runId=run["id"], inputFingerprint=run["input_hash"])
    if not selectable_kind:
        result.setdefault("kind", "draft")
    if result.get("kind") in ("draft", "discussion"):
        result["baseVersionId"] = run["base_version_id"]
        if isinstance(result.get("changes"), list):
            result["changes"] = [dict(change, targetId=task["artifact_id"]) if isinstance(change, dict) else change
                                 for change in result["changes"]]
    return result


def bind_review_reply(run, reply, pass_name, segment):
    if not isinstance(reply, dict):
        return reply
    version = run["target_version_id"]
    result = dict(reply, kind="review", schemaVersion=1, runId=run["id"],
                  inputFingerprint=run["input_hash"], reviewedVersionId=version)
    result["pass"] = pass_name

    def locate(location, observation):
        if not isinstance(location, dict):
            return location
        location = dict(location, versionId=version)
        if pass_name == "story":
            if not observation or location.get("objectId") in ("全文", None):
                location["objectId"] = version
        elif pass_name == "cold_read" or (pass_name == "comparison" and not observation):
            location["objectId"] = segment
        return location

    if isinstance(result.get("observations"), list):
        result["observations"] = [locate(item, True) for item in result["observations"]]
    if isinstance(result.get("issues"), list):
        result["issues"] = [dict(item, location=locate(item["location"], False))
                            if isinstance(item, dict) and "location" in item else item for item in result["issues"]]
    return result
