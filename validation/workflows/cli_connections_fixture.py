"""Native adapter fixture: executable files under a temporary directory, no external requests."""
import json
import os
from pathlib import Path

from workbench.executors.cli import AntigravityExecutor, ClaudeExecutor, CodexExecutor


def prepare(env, folder):
    catalog = folder/"native-codex"
    catalog.mkdir()
    os.environ["CODEX_HOME"] = str(catalog)
    (catalog/"models_cache.json").write_text(json.dumps({"models":[dict(slug="studio-model",display_name="创作型号",
        visibility="list",supported_reasoning_levels=[dict(effort="medium"),dict(effort="high")])]}))
    pid, eid = env.new_episode(name="本机连接")
    for provider, cls, name in [("codex",CodexExecutor,"codex"),("claude",ClaudeExecutor,"claude"),
                               ("antigravity",AntigravityExecutor,"agy")]:
        binary = folder/"bin"/name
        binary.parent.mkdir(exist_ok=True)
        implementation = """#!/usr/bin/env python3
from pathlib import Path
import json, sys, os
args=sys.argv[1:]
if args==["--version"]:
    print("local-fixture-1")
elif "--help" in args:
    print(FLAGS)
    print("  --model <model>  Choose 'opus' or 'sonnet'")
    print("  --effort <level>  (low, medium, high)")
elif args==["login","status"]:
    print("Logged in using ChatGPT")
elif args==["auth","status","--json"]:
    print(json.dumps(dict(loggedIn=True,email="studio@example.invalid")))
elif "--input-format" in args:
    init = json.loads(sys.stdin.read())
    assert init["request"] == {"subtype":"initialize"}
    models = [dict(value="sonnet",displayName="Sonnet",description="Sonnet 5.5 · Fixture",supportedEffortLevels=["medium"]),
              dict(value="claude-fable-5-1[1m]",displayName="Fable",description="Fable 5.1 · Fixture",supportedEffortLevels=["high"])]
    print(json.dumps(dict(type="control_response",response=dict(request_id=init["request_id"],subtype="success",response=dict(models=models)))))
elif args==["models"]:
    print("gemini-local-test    Google 本地测试型号")
else:
    sys.stdin.read()
    if PATH_MARKER:
        print(json.dumps(dict(subtype="error",is_error=True,result="usage limit reached")))
    else:
        print("usage limit reached",file=sys.stderr)
    sys.exit(1)
""".replace("FLAGS",repr(" ".join(cls.required_flags))).replace("PATH_MARKER",repr(provider=="claude"))
        binary.write_text(implementation)
        binary.chmod(0o700)
        env.wb.executors[provider] = cls(str(binary))
        alternate = folder/"alternate"/name
        alternate.parent.mkdir(exist_ok=True)
        alternate.write_text(implementation.replace("local-fixture-1","local-fixture-2"))
        alternate.chmod(0o700)
        env.wb.put_setting(provider+"_path",str(binary))
