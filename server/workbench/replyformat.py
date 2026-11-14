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
