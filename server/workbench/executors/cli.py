"""Codex CLI and Claude Code adapters.

Subprocesses get an argument array (no shell), the prompt on stdin, an empty per-run working
directory and the system loopback proxy when one is configured. Reviews run in a new ephemeral
session with tools and extra MCP/plugins disabled ("session isolation", user decision 2026-11-07).
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import getproxies

from ..connections import launch_env
from ..errors import Rejected
from .base import ExecOutcome, Executor, ExecRequest

ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "SHELL")


def local_proxy_env() -> dict:
    result = {}
    for scheme, endpoint in getproxies().items():
        if scheme not in ("http", "https"):
            continue
        url = urlsplit(endpoint)
        if (url.scheme in ("http", "https") and url.hostname in ("127.0.0.1", "::1", "localhost")
                and not url.username and not url.password and url.port):
            result[scheme.upper() + "_PROXY"] = endpoint
            result[scheme + "_proxy"] = endpoint
    if result:
        result["NO_PROXY"] = result["no_proxy"] = "localhost,127.0.0.1,::1"
    return result


def classify(text: str) -> str:
    s = text.lower()
    for tag, words in [
        ("quota", ["usage limit", "rate limit", "5-hour limit", "quota", "limit reached", "out of credits",
                   "insufficient_quota"]),
        ("authentication", ["unauthorized", "not logged in", "authentication required", "not authenticated",
                            "please sign in", "invalid api key", "please run /login", " 401"]),
        ("timeout", ["timeout", "timed out"]),
        ("schema", ["invalid schema", "invalid_json_schema", "json schema"]),
        ("network", ["connection", "connect error", "dns", "stream disconnected", "econnrefused"]),
    ]:
        if any(w in s for w in words):
            return tag
    return "unknown"


def resets_at(text: str):
    match = re.search(r"resets?\s+(?:at\s+)?([^\n\"|.]{2,40})", text, re.I)
    if match:
        return match.group(1).strip()
    match = re.search(r"limit reached\|(\d{9,})", text)
    return match.group(1) if match else None


def redact(text: str, limit=2000) -> str:
    text = re.sub(r"(sk-[A-Za-z0-9_-]{8})[A-Za-z0-9_-]+", r"\1…", text)
    text = re.sub(r"(?i)(bearer|token|api[_-]?key)([\"':= ]+)[A-Za-z0-9._-]{12,}", r"\1\2…", text)
    return text[-limit:]


RUNNING: dict = {}


def invoke(args, cwd: Path, stdin: str, timeout: int, run_id: str | None = None, *, extra_env=None):
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.update(local_proxy_env())
    env.update(extra_env or {})
    with subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as proc:
        if run_id:
            RUNNING[run_id] = proc
        try:
            out, err = proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                out, err = proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:   # a grandchild may still hold the pipes open: never wait without a bound
                    out, err = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    for stream in (proc.stdout, proc.stderr):
                        try:
                            stream.close()
                        except OSError:
                            pass
                    out, err = "", ""
            return 124, out or "", (err or "") + "\ntimeout"
        finally:
            RUNNING.pop(run_id, None)
        return proc.returncode, out, err


def stop(run_id) -> bool:
    """Stop: SIGTERM the run's process group, then SIGKILL after 5 s."""
    proc = RUNNING.get(run_id)
    if not proc:
        return False
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return True


def find_claude(configured: str | None = None) -> Path | None:
    """Configured path first, then PATH, ~/.local/bin, then the desktop-app managed dir by version."""
    for candidate in (configured, os.environ.get("ADW_CLAUDE_PATH")):
        if candidate and os.access(candidate, os.X_OK):
            return Path(candidate)
    if found := shutil.which("claude"):
        return Path(found)
    local = Path.home() / ".local/bin/claude"
    if os.access(local, os.X_OK):
        return local
    root = Path.home() / "Library/Application Support/Claude/claude-code"
    best = None
    for p in root.glob("*/*/claude.app/Contents/MacOS/claude"):
        parts = p.parents[4].name.split(".")  # <version>/<hash>/claude.app/Contents/MacOS/claude
        if all(s.isdigit() for s in parts) and os.access(p, os.X_OK):
            key = tuple(map(int, parts))
            if best is None or key > best[0]:
                best = (key, p)
    return best[1] if best else None


class CliExecutor(Executor):
    # start() owns one local request/process; transport metadata is not authored prose.
    binds_request_context = True
    required_flags: tuple = ()

    def __init__(self, executable: str | None = None):
        self.configured = executable

    def binary(self) -> Path | None:
        raise NotImplementedError

    def probe(self, connection=None) -> dict:
        env = launch_env(self.name, connection or {})
        binary = self.binary()
        info = dict(provider=self.name, executable=str(binary) if binary else None, version=None,
                    authenticated=None, structured_result="untested", proxy=bool(env.get("HTTP_PROXY") if "HTTP_PROXY" in env else local_proxy_env()),
                    independent_review="session_isolation")
        if not binary:
            info["status"] = "unavailable"
            return info
        rc, out, _ = invoke([str(binary), "--version"], Path.home(), "", 20, extra_env=env)
        info["version"] = out.strip() if rc == 0 else None
        rc, help_out, help_err = invoke([str(binary), *self.help_args, "--help"], Path.home(), "", 20, extra_env=env)
        info["missing_flags"] = [f for f in self.required_flags if f not in help_out + help_err]
        info.update(self.native_identity(binary,env))
        ready = info["version"] and not info["missing_flags"]
        if ready and info["authenticated"] is None:
            info["status"] = "auth_unknown"
        else:
            info["status"] = "available" if ready and info["authenticated"] else "unavailable"
        return info

    help_args: tuple = ()

    def native_identity(self, binary, env):
        return dict(authenticated=None, accountLabel=None)

    def cancel(self, run_id):
        return stop(run_id)


class CodexExecutor(CliExecutor):
    strict_schema = True
    name = "codex"
    isolation = "session"      # verified live 2026-11-07 (story review)
    reads_images = True        # --image; not yet verified live
    help_args = ("exec",)
    required_flags = ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--output-schema",
                      "--sandbox")

    def binary(self):
        if self.configured and os.access(self.configured, os.X_OK):
            return Path(self.configured)
        found = shutil.which("codex") or os.environ.get("ADW_CODEX_PATH")
        return Path(found) if found else None

    def native_identity(self, binary, env):
        rc, out, err = invoke([str(binary), "login", "status"], Path.home(), "", 20, extra_env=env)
        status = (out + err).lower()
        if "not logged in" in status:
            return dict(authenticated=False, accountLabel=None)
        signed_in = rc == 0 and "logged in" in status
        method = "ChatGPT" if "chatgpt" in status else "API Key" if "api key" in status else None
        return dict(authenticated=True if signed_in else None,accountLabel=method if signed_in else None)

    def start(self, req: ExecRequest) -> ExecOutcome:
        binary = self.binary()
        if not binary:
            return ExecOutcome(ok=False, error_class="unavailable", diagnostic="codex not found")
        schema_path = req.workdir.parent.resolve() / "output-schema.json"
        schema_path.write_text(json.dumps(req.schema, ensure_ascii=False))
        opts = []
        if req.model:
            opts += ["--model", req.model]
        if req.effort:
            opts += ["-c", "model_reasoning_effort=" + json.dumps(req.effort)]
        isolation = ["--ephemeral", "--disable", "memories", "--enable", "skip_host_skill_discovery", "-c", "project_doc_max_bytes=0"]
        args = [str(binary), "exec", *opts, "--json", "--sandbox", "read-only", *isolation,
                "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                "--disable", "shell_tool", "--disable", "apps", "--disable", "plugins",
                "--disable", "multi_agent", "--disable", "skill_search", "-c", 'web_search="disabled"',
                *[a for p in req.images for a in ("--image", str(Path(p).resolve()))],
                "--output-schema", str(schema_path), "-"]
        # Keep discovery outside both the operator's profile and the project checkout.
        # Only the login file is shared. Never copy its contents into a project or run receipt.
        env = launch_env(self.name, req.connection)
        profile = Path(env.get("CODEX_HOME") or Path.home() / ".codex")
        with tempfile.TemporaryDirectory(prefix="adw-codex-profile-") as temp:
            home = Path(temp)
            state = home / ".codex"
            state.mkdir(mode=0o700)
            login = profile / "auth.json"
            if login.is_file():
                (state / "auth.json").symlink_to(login.resolve())
            cwd = home / "work"
            cwd.mkdir()
            rc, out, err = invoke(args, cwd, req.prompt, req.timeout, req.run_id,
                                  extra_env={**env, "HOME":str(home), "CODEX_HOME":str(state)})
        events, session, messages, failed = [], None, [], []
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.append(event.get("type"))
            if event.get("type") == "thread.started":
                session = event.get("thread_id")
            # 实测 135：断线后 Codex 自行重连并完成本轮时，"Reconnecting..." 只是过程提示，不是失败；
            # 重连用尽时会以 turn.failed 或缺少 turn.completed 结束，仍按失败处理。
            transient = event.get("type") == "error" and str(event.get("message", "")).startswith("Reconnecting...")
            if event.get("type") in ("error", "turn.failed") and not transient:
                failed.append(json.dumps(event, ensure_ascii=False))
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                messages.append(item.get("text", ""))
        if messages:
            (req.workdir.parent / f"raw-response-{req.attempt}.txt").write_text(messages[-1], encoding="utf-8")
        base = dict(session_id=session, events=events, executable=str(binary),
                    isolation=dict(session=req.session_mode, sandbox="read-only", tools="disabled", ephemeral=True, profile="isolated",
                                   user_config="ignored", rules="ignored", cwd="empty run dir"))
        if rc or failed or not events or events[-1] != "turn.completed" or not messages:
            text = "\n".join(failed) + "\n" + err
            return ExecOutcome(ok=False, error_class=classify(text), diagnostic=redact(text),
                               quota_resets_at=resets_at(text), **base)
        try:
            structured = json.loads(messages[-1])
        except ValueError:
            return ExecOutcome(ok=False, error_class="schema", diagnostic="final message is not JSON", **base)
        return ExecOutcome(ok=True, structured=structured, **base)


class ClaudeExecutor(CliExecutor):
    strict_schema = True
    name = "claude"
    isolation = "session"      # verified live 2026-11-07 (story review)
    reads_images = False       # tools are disabled in the isolated review, so files cannot be opened
    required_flags = ("--output-format", "--json-schema", "--strict-mcp-config", "--restricted",
                      "--no-session-persistence", "--tools", "--disable-slash-commands")

    def binary(self):
        return find_claude(self.configured)

    def model_options(self, binary, connection):
        # Stream initialization returns selector metadata without sending a user turn.
        request = dict(type="control_request", request_id="catalog", request={"subtype": "initialize"})
        args = [str(binary), "-p", "--verbose", "--input-format", "stream-json", "--output-format", "stream-json",
                "--no-session-persistence", "--restricted", "--strict-mcp-config", "--tools", "",
                "--disable-slash-commands", "--setting-sources", ""]
        with tempfile.TemporaryDirectory(prefix="adw-claude-models-") as cwd:
            rc, out, _ = invoke(args, Path(cwd), json.dumps(request) + "\n", 20,
                                extra_env=launch_env(self.name, connection))
        if rc == 0:
            for line in out.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "control_response":
                    continue
                reply = event.get("response") or {}
                if isinstance(reply, dict) and reply.get("request_id") == request["request_id"] and reply.get("subtype") == "success":
                    payload = reply.get("response")
                    options = payload.get("models") if isinstance(payload, dict) else None
                    if isinstance(options, list) and options:
                        return options
        raise Rejected("Claude CLI 未返回型号清单，请检查连接和 CLI 版本后重试。")

    def native_identity(self, binary, env):
        rc, out, _ = invoke([str(binary), "auth", "status", "--json"], Path.home(), "", 20, extra_env=env)
        try:
            info = json.loads(out)
            logged = info["loggedIn"]
            email = info.get("email")
            return dict(authenticated=logged if type(logged) is bool else None,
                        accountLabel=email if logged is True and isinstance(email,str) else None)
        except (TypeError, ValueError, KeyError):
            return dict(authenticated=None,accountLabel=None)

    def start(self, req: ExecRequest) -> ExecOutcome:
        binary = self.binary()
        if not binary:
            return ExecOutcome(ok=False, error_class="unavailable", diagnostic="claude not found")
        opts = []
        if req.model:
            opts += ["--model", req.model]
        if req.effort:
            opts += ["--effort", req.effort]
        isolation = ["--no-session-persistence", "--setting-sources", ""]
        args = [str(binary), *opts, "-p", "--restricted", "--strict-mcp-config", "--tools", "",
                "--disable-slash-commands", *isolation, "--output-format", "json",
                "--json-schema", json.dumps(req.schema, ensure_ascii=False)]
        rc, out, err = invoke(args, req.workdir, req.prompt, req.timeout, req.run_id,
                              extra_env={**launch_env(self.name,req.connection), "CLAUDE_CODE_DISABLE_CLAUDE_MDS":"1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY":"1"})
        base = dict(executable=str(binary),
                    isolation=dict(session=req.session_mode, tools="none", mcp="strict-empty",
                                   settings="restricted", slash_commands="disabled", persistence=False, cwd="empty run dir"))
        try:
            receipt = json.loads(out)
        except ValueError:
            text = out + "\n" + err
            return ExecOutcome(ok=False, error_class=classify(text), diagnostic=redact(text),
                               quota_resets_at=resets_at(text), **base)
        reply = receipt.get('structured_output')
        raw = json.dumps(reply, ensure_ascii=False) if reply is not None else receipt.get('result')
        if isinstance(raw, str):
            (req.workdir.parent / f"raw-response-{req.attempt}.txt").write_text(raw, encoding='utf-8')
        base["session_id"] = receipt.get("session_id")
        if receipt.get("is_error") is not False or receipt.get("subtype") != "success" \
                or not isinstance(receipt.get("structured_output"), dict):
            text = json.dumps({k: receipt.get(k) for k in ("subtype", "result", "error", "api_error_status")},
                              ensure_ascii=False) + "\n" + err
            return ExecOutcome(ok=False, error_class=classify(text), diagnostic=redact(text),
                               quota_resets_at=resets_at(text), **base)
        model = receipt.get("model")
        if not isinstance(model, str):
            usage = receipt.get("modelUsage")
            model = next(iter(usage)) if isinstance(usage, dict) and len(usage) == 1 else None
        return ExecOutcome(ok=True, structured=receipt["structured_output"], reported_model=model, **base)


class AntigravityExecutor(CliExecutor):
    """Google Antigravity `agy` headless mode (structured_output in a SUCCESS receipt)."""
    strict_schema = False
    name = "antigravity"
    union_schema = False       # verified 2026-11-07: an anyOf wrapper yields status ERROR
    isolation = "unverified"   # review isolation flags not yet verified → required reviews wait
    reads_images = False
    required_flags = ("--print", "--output-format", "--json-schema", "--print-timeout", "--sandbox")

    def binary(self):
        if self.configured and os.access(self.configured, os.X_OK):
            return Path(self.configured)
        found = shutil.which("agy") or (str(Path.home() / ".local/bin/agy")
                                        if os.access(Path.home() / ".local/bin/agy", os.X_OK) else None)
        return Path(found) if found else None

    def native_identity(self, binary, env):
        return dict(authenticated=None, accountLabel=None)   # only a real request proves it (phase 0)

    def start(self, req: ExecRequest) -> ExecOutcome:
        binary = self.binary()
        if not binary:
            return ExecOutcome(ok=False, error_class="unavailable", diagnostic="agy not found")
        opts = []
        if req.model:
            opts += ["--model", req.model]
        if req.effort:
            opts += ["--effort", req.effort]
        args = [str(binary), *opts, "--sandbox", "--print-timeout", f"{max(30, req.timeout - 10)}s",
                "--output-format", "json"]
        if req.schema is not None:
            args += ["--json-schema", json.dumps(req.schema, ensure_ascii=False)]
        args += ["-p", req.prompt]
        rc, out, err = invoke(args, req.workdir, "", req.timeout, req.run_id, extra_env=launch_env(self.name,req.connection))
        base = dict(executable=str(binary), isolation=dict(session=req.session_mode, sandbox="on"))
        try:
            receipt = json.loads(out)
        except ValueError:
            text = out + "\n" + err
            return ExecOutcome(ok=False, error_class=classify(text), diagnostic=redact(text),
                               quota_resets_at=resets_at(text), **base)
        if isinstance(receipt.get("response"), str):
            (req.workdir.parent / f"raw-response-{req.attempt}.txt").write_text(receipt["response"], encoding="utf-8")
        if req.schema is None and receipt.get("status") == "SUCCESS":
            response = receipt.get("response")
            if not isinstance(response, str):
                return ExecOutcome(ok=False, error_class="schema", diagnostic="回复须为 JSON 对象", **base)
            response = response.strip()
            fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", response, re.DOTALL)
            if fenced:
                response = fenced.group(1)
            else:
                blocks = re.findall(r'^```(?:json)?\s*\n(.*?)\n```\s*$',response,re.DOTALL|re.MULTILINE)
                if len(blocks)==1:response=blocks[0]
            try:
                parsed = json.loads(response)
            except json.JSONDecodeError as exc:
                return ExecOutcome(ok=False, error_class="schema",
                                   diagnostic=f"回复不是可解析的 JSON：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}", **base)
            if not isinstance(parsed, dict):
                return ExecOutcome(ok=False, error_class="schema", diagnostic="回复须为 JSON 对象", **base)
            return ExecOutcome(ok=True, structured=parsed, session_id=receipt.get("session_id"),
                               reported_model=receipt.get("model") if isinstance(receipt.get("model"), str) else None, **base)
        if receipt.get("status") == "SUCCESS" and not isinstance(receipt.get("structured_output"), dict):
            # 实测 143：成功但只回了文字。若文字本身就是合格 JSON 则采用，否则归为格式错误（由调用方重试一次）。
            try:
                parsed = json.loads(receipt.get("response") or "")
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                receipt["structured_output"] = parsed
            else:
                return ExecOutcome(ok=False, error_class="schema", diagnostic="执行器回复了文字，但没有按要求的格式回包", **base)
        if receipt.get("status") != "SUCCESS" or not isinstance(receipt.get("structured_output"), dict):
            text = json.dumps(receipt, ensure_ascii=False)[:2000] + err
            return ExecOutcome(ok=False, error_class=classify(text), diagnostic=redact(text),
                               quota_resets_at=resets_at(text), **base)
        return ExecOutcome(ok=True, structured=receipt["structured_output"], session_id=receipt.get("session_id"),
                           reported_model=receipt.get("model") if isinstance(receipt.get("model"), str) else None, **base)
