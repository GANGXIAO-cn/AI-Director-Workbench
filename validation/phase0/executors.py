"""Local R7 capability probe. Only --live sends the built-in synthetic sample."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import getproxies

from jsonschema import Draft202012Validator, ValidationError

PROVIDERS = ("codex", "claude", "antigravity")
ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts/model-result.v1.schema.json").read_text())
CHECK = Draft202012Validator(SCHEMA)
SOURCE = "场02：阿福站在门边。"
FINGERPRINT = hashlib.sha256(SOURCE.encode()).hexdigest()
SAMPLE = dict(schemaVersion=1, kind="draft", runId="phase0-synthetic",
              inputFingerprint=FINGERPRINT, baseVersionId="R03",
              changes=[dict(targetId="scene02", field="action", value="阿福用爪子推开门。")])


def claude_binary():
    if binary := shutil.which("claude"):
        return Path(binary)
    root = Path.home() / "Library/Application Support/Claude/claude-code"
    candidates = [(tuple(map(int, p.parents[4].name.split("."))), p)
                  for p in root.glob("*/*/claude.app/Contents/MacOS/claude")
                  if all(s.isdigit() for s in p.parents[4].name.split(".")) and os.access(p, os.X_OK)]
    return max(candidates)[1] if candidates else None


def local_proxy_env():
    result = {}
    for scheme, endpoint in getproxies().items():
        if scheme not in ("http", "https"):
            continue
        url = urlsplit(endpoint)
        if (url.scheme in ("http", "https") and url.hostname in ("127.0.0.1", "::1", "localhost")
                and not url.username and not url.password and url.port):
            result[scheme.upper() + "_PROXY"] = endpoint
    if result:
        result["NO_PROXY"] = "localhost,127.0.0.1,::1"
    return result


def invoke(args, cwd, prompt=None, timeout=15, network_env=None):
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL")
           if k in os.environ}
    env.update(network_env or {})
    with subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          start_new_session=True) as proc:
        try:
            out, err = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            return 124, out, err + "\ntimeout"
        return proc.returncode, out, err


def decode(provider, output):
    if provider == "antigravity":
        event = json.loads(output)
        if event.get("status") != "SUCCESS":
            raise ValueError("no_success_receipt")
        return event["structured_output"]
    if provider == "claude":
        event = json.loads(output)
        if event.get("is_error") is not False or event.get("subtype") != "success":
            raise ValueError("no_success_receipt")
        return event["structured_output"]
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    if (not events or events[-1].get("type") != "turn.completed"
            or not any(e.get("type") == "thread.started" and e.get("thread_id") for e in events)
            or any(e.get("type") in ("error", "turn.failed") for e in events)):
        raise ValueError("no_success_receipt")
    messages = [e["item"]["text"] for e in events if e.get("type") == "item.completed"
                and e.get("item", {}).get("type") == "agent_message"]
    return json.loads(messages[-1])


def diagnostics(text):
    s = text.lower()
    for tag, words in [
        ("timeout", ["timeout", "timed out"]),
        ("permission_denied", ["operation not permitted", "permission denied"]),
        ("schema_rejected", ["invalid schema", "invalid_json_schema"]),
        ("network_error", ["connection", "connect error", "dns", "stream disconnected"]),
        ("authentication", ["unauthorized", "not logged in", "authentication required", "not authenticated", "please sign in", "401"]),
        ("quota", ["quota", "usage limit", "rate limit", "5-hour limit"]),
    ]:
        if any(word in s for word in words):
            return tag
    return "unclassified"


def probe(provider, live, cwd, model=None, effort=None):
    if provider == "antigravity":
        binary = shutil.which("agy") or shutil.which(str(Path.home() / ".local/bin/agy"))
    elif provider == "claude":
        binary = claude_binary()
    else:
        binary = shutil.which("codex")
    info = dict(executable=str(binary) if binary else None, version=None, authenticated=None,
                structured_result="untested", independent_review="unverified")
    if not binary:
        return info
    binary = str(binary)
    rc, version, _ = invoke([binary, "--version"], cwd)
    if rc:
        info["version_error"] = rc
        return info
    info["version"] = version.strip()
    codex = provider == "codex"
    rc, help_text, help_err = invoke([binary, *(["exec"] if codex else []), "--help"], cwd)
    help_text += help_err
    required = {
        "codex": ["--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--output-schema"],
        "claude": ["--output-format", "--json-schema", "--strict-mcp-config", "--restricted",
                   "--no-session-persistence", "--tools"],
        "antigravity": ["--print", "--output-format", "--json-schema", "--print-timeout", "--sandbox"],
    }
    flags = required[provider].copy()
    if model:
        flags.append("--model")
    if effort:
        flags.append("--config" if codex else "--effort")
    info["missing_flags"] = [f for f in flags if f not in help_text]
    if provider == "codex":
        rc, out, err = invoke([binary, "login", "status"], cwd)
        status = (out + err).lower()
        if rc == 0 and "logged in" in status:
            info["authenticated"] = True
            info["login_mode"] = "ChatGPT" if "chatgpt" in status else "other"
        elif "not logged in" in status:
            info["authenticated"] = False
    elif provider == "claude":
        rc, out, err = invoke([binary, "auth", "status", "--json"], cwd)
        try:
            status = json.loads(out)
            info["authenticated"] = status["loggedIn"]
            info["login_mode"] = status.get("authMethod")
        except (ValueError, KeyError):
            pass
    if not live or info["missing_flags"] or (provider != "antigravity" and not info["authenticated"]):
        return info
    prompt = ("Synthetic transport test. Use no tools. Echo this draft as the structured result "
              "without changing any value. " + json.dumps(SAMPLE, ensure_ascii=False))
    report, result = draft_request(provider, binary, cwd, prompt, model, effort)
    if provider == "antigravity":
        if result is not None:
            info["authenticated"] = True
        elif report.get("diagnostic") == "authentication":
            info["authenticated"] = False
    if result is not None and result != SAMPLE:
        report.update(structured_result="invalid", diagnostic="sample_mismatch")
    return {**info, **report}


def draft_request(provider, binary, cwd, prompt, model=None, effort=None):
    info = dict(requested_model=model, requested_effort=effort,
                reported_model=None, reported_effort=None)
    options = []
    if model:
        if model.startswith("-"):
            raise ValueError("invalid model ID")
        options.extend(["--model", model])
    if effort:
        if provider == "codex":
            options.extend(["-c", "model_reasoning_effort=" + json.dumps(effort)])
        else:
            options.extend(["--effort", effort])
    codex = provider == "codex"
    schema = json.dumps(SCHEMA["$defs"]["draft"])
    if codex:
        path = cwd / "draft-schema.json"
        path.write_text(schema)
        args = [binary, "exec", *options, "--json", "--sandbox", "read-only", "--ephemeral",
                "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                "--disable", "shell_tool", "--disable", "apps", "--disable", "plugins",
                "--disable", "multi_agent", "--disable", "skill_search",
                "-c", 'web_search="disabled"', "--output-schema", str(path), "-"]
    elif provider == "antigravity":
        args = [binary, *options, "--sandbox", "--print-timeout", "80s", "--output-format", "json",
                "--json-schema", schema, "-p", prompt]
        prompt = None
    else:
        args = [binary, *options, "-p", "--restricted", "--strict-mcp-config", "--tools", "",
                "--no-session-persistence", "--output-format", "json", "--json-schema", schema]
    network_env = local_proxy_env()
    info["network_route"] = "system_loopback_proxy" if network_env else "default"
    rc, out, err = invoke(args, cwd, prompt, 95, network_env)
    info["exit"] = rc
    if rc:
        info["structured_result"] = "failed"
        info["diagnostic"] = diagnostics(out + err)
        if codex:
            for line in out.splitlines():
                try:
                    event = json.loads(line)
                    info.setdefault("events", []).append(event["type"])
                    message = str(event.get("message", event.get("error", "")))
                    if message:
                        info.setdefault("event_diagnostics", []).append(diagnostics(message))
                except (ValueError, TypeError, KeyError):
                    continue
        return info, None
    try:
        result = decode(provider, out)
        CHECK.validate(result)
        if provider != "codex":
            receipt = json.loads(out)
            if isinstance(receipt.get("model"), str):
                info["reported_model"] = receipt["model"]
            if isinstance(receipt.get("effort"), str):
                info["reported_effort"] = receipt["effort"]
    except (ValueError, KeyError, IndexError, TypeError, AttributeError,
            ValidationError) as exc:
        info["structured_result"] = "invalid"
        info["diagnostic"] = type(exc).__name__
        return info, None
    info["structured_result"] = "verified"
    if codex:
        info["events"] = [json.loads(line)["type"] for line in out.splitlines() if line.strip()]
    return info, result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", choices=PROVIDERS, help="send one synthetic text request")
    parser.add_argument("--model", help="explicit model ID or executor alias")
    parser.add_argument("--effort", help="native reasoning effort for the selected model")
    opts = parser.parse_args()
    if (opts.model or opts.effort) and not opts.live:
        parser.error("--model and --effort require --live")
    with tempfile.TemporaryDirectory(prefix="r7-cli-") as tmp:
        results = {p: probe(p, opts.live == p, Path(tmp),
                            opts.model if p == opts.live else None,
                            opts.effort if p == opts.live else None) for p in PROVIDERS}
        print(json.dumps(results, ensure_ascii=False, indent=2))
        if opts.live and results[opts.live]["structured_result"] != "verified":
            raise SystemExit(1)
