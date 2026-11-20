"""One ACP image turn, run in the executor's cancellable process group."""
import base64
import json
import mimetypes
from pathlib import Path
import subprocess
import sys


def media_turn(proc, request):
    chunks = []
    seq = 0

    def send(message):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        proc.stdin.flush()

    def call(method, params):
        nonlocal seq
        seq += 1
        send(dict(id=seq, method=method, params=params))
        for line in proc.stdout:
            msg = json.loads(line)
            if "method" in msg:
                if msg["method"] == "session/update":
                    update = msg["params"]["update"]
                    if update["sessionUpdate"] == "agent_message_chunk" and update["content"]["type"] == "text":
                        chunks.append(update["content"]["text"])
                elif "id" in msg:
                    if msg["method"] == "session/request_permission":
                        send(dict(id=msg["id"], result={"outcome": {"outcome": "cancelled"}}))
                    else:
                        send(dict(id=msg["id"], error={"code": -32601, "message": "Unsupported client method"}))
            elif msg.get("id") == seq:
                if "error" in msg:
                    raise ValueError(msg["error"].get("message", "ACP request failed"))
                return msg["result"]
        raise ValueError("Antigravity ACP closed before replying")

    init = call("initialize", dict(protocolVersion=1, clientCapabilities={}))
    if request["images"] and not init["agentCapabilities"].get("promptCapabilities", {}).get("image"):
        raise ValueError("当前 Antigravity ACP 版本未声明图片输入能力")
    session = call("session/new", dict(cwd=str(Path.cwd()), mcpServers=[]))
    sid = session["sessionId"]
    model = request.get("model")
    if model:
        call("session/set_model", dict(sessionId=sid, modelId=model))
    if request.get("effort"):
        options = session.get("configOptions", [])
        option = next((item for item in options if item.get("category") == "thought_level"), None)
        if not option:
            raise ValueError("当前 ACP 会话未提供独立推理强度选项，请使用含档位的型号或默认强度")
        call("session/set_config_option", dict(sessionId=sid, configId=option["id"], value=request["effort"]))
    blocks = [dict(type="text", text=request["prompt"])]
    for path in map(Path, request["images"]):
        blocks.append(dict(type="image", mimeType=mimetypes.guess_type(path.name)[0] or "image/png",
                           data=base64.b64encode(path.read_bytes()).decode("ascii")))
    end = call("session/prompt", dict(sessionId=sid, prompt=blocks))
    if end.get("stopReason") != "end_turn":
        raise ValueError(f"Antigravity ACP stopped: {end.get('stopReason')}")
    return dict(status="SUCCESS", session_id=sid, response="".join(chunks))


if __name__ == "__main__":
    request = json.load(sys.stdin)
    args = [sys.argv[1], "acp", "--sandbox"]
    if request.get("independent"):
        args.extend(["--process-per-session", "--include-default-customizations=false"])
    with subprocess.Popen(args, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True) as proc:
        try:
            print(json.dumps(media_turn(proc, request)))
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps(dict(status="ERROR", error=str(exc))))
        finally:
            proc.terminate()
