"""Inspect the outbound context of installed CLIs against a loopback-only test transport."""
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "server/tests")]
from helpers import Env
from workbench.executors import cli

MARKERS = {"memory":"ADW_PRIVATE_MEMORY_4817", "skill":"ADW_GLOBAL_SKILL_8271", "rules":"ADW_GLOBAL_RULE_1325"}
REQUEST = "一张末班渡轮的过期船票 · ADW_SELECTED_6812"
BRAND = ("MILO", "LEO", "PUGGY", "女主人", "Fourthwall", "Suno")


def capture(provider, baseline=False):
    root = Path(tempfile.mkdtemp(prefix="adw-cli-context-"))
    home, codex, claude = root / "user", root / "user/.codex", root / "user/.claude"
    paths = {codex / "memories/memory_summary.md":MARKERS["memory"],
             codex / "memories_v2/memory_summary.md":MARKERS["memory"],
             codex / "AGENTS.md":MARKERS["rules"], claude / "CLAUDE.md":MARKERS["rules"]}
    for directory in (codex, claude, home / ".agents"):
        paths[directory / "skills/private-example/SKILL.md"] = (
            "---\nname: private-example\ndescription: " + MARKERS["skill"] + "\n---\n" + MARKERS["skill"])
    for path, content in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    fixture = Env(root)
    _, eid = fixture.new_episode(idea=REQUEST)
    rid = fixture.wb.create_task(eid, "story", "写渡轮故事")["runId"]
    fixture.drain(rid)
    req = fixture.codex.calls[-1]
    captured = []
    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            elif self.headers.get("Content-Encoding") == "zstd":
                data = subprocess.check_output(["zstd", "-dc"], input=data)
            if data:
                payload = json.loads(data)
                body = json.dumps(payload, ensure_ascii=False)
                if "ADW_SELECTED_6812" in body:
                    captured.append(dict(path=self.path, canaries={k:v in body for k,v in MARKERS.items()},
                                         brand=[v for v in BRAND if v.lower() in body.lower()],
                                         tools=[t.get("name", t.get("type")) for t in payload.get("tools", [])]))
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"type":"error","error":{"type":"invalid_request_error","message":"local capture done"}}')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    source = "http://127.0.0.1:" + str(server.server_port)
    adapter = cli.CodexExecutor(shutil.which("codex")) if provider == "codex" else cli.ClaudeExecutor(str(cli.find_claude()))
    req.model = "gpt-6.1-sol" if provider == "codex" else "sonnet"
    def invoke(args, cwd, stdin, timeout, run_id, **options):
        env = dict(PATH=os.environ["PATH"], HOME=str(home), CODEX_HOME=str(codex), CLAUDE_CONFIG_DIR=str(claude),
                   HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1",
                   NO_PROXY="localhost,127.0.0.1", ANTHROPIC_BASE_URL=source, ANTHROPIC_API_KEY="local-fixture",
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        if not baseline:
            env.update(options.get("extra_env", {}))
        if provider == "codex":
            if baseline:
                for flag in ("memories", "skip_host_skill_discovery", "project_doc_max_bytes=0"):
                    if flag in args:
                        i = args.index(flag)
                        args = args[:i-1] + args[i+1:]
            opts = ["-c", 'model_provider="capture"', "-c", 'model_providers.capture.name="capture"',
                    "-c", f'model_providers.capture.base_url="{source}/v1"', "-c",
                    'model_providers.capture.wire_api="responses"']
            args = [*args[:2], *opts, *args[2:]]
        try:
            result = subprocess.run(args, input=stdin, text=True, capture_output=True, cwd=cwd, env=env, timeout=28)
            if not captured:
                print("No matching request:", result.stderr[-1100:], result.stdout[-900:], file=sys.stderr)
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            print("Timeout waiting for local receiver", file=sys.stderr)
            return 124, "", "local capture timeout"
        finally:
            server.shutdown()
            server.server_close()
    with patch.dict(os.environ, {"CODEX_HOME":str(codex)}), patch.object(cli, "invoke", invoke):
        adapter.start(req)
    assert captured, f"{provider} did not send the fixture to the local receiver"
    result = captured[-1]
    print(json.dumps(dict(provider=provider, mode="original" if baseline else "isolated", **result), ensure_ascii=False), flush=True)
    if not baseline:
        assert not any(result["canaries"].values()) and not result["brand"], result
        assert set(result["tools"]) <= ({"StructuredOutput"} if provider == "claude" else set()), result
    elif provider == "codex":
        assert result["canaries"]["skill"] and result["canaries"]["rules"], result


if __name__ == "__main__":
    for name in sys.argv[1:] or ["codex-original", "codex", "claude"]:
        capture(name.split("-")[0], name.endswith("-original"))
