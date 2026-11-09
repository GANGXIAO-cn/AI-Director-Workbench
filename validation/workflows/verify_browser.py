"""Launch an isolated simulated service and run the browser workflows against that instance."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
def run(script):
    if script not in {"history","shot_wall","enhancements","story_skill","script_methods","home","archive_page","baselines","publish_handoff","mixed_media","h3_sound","h3_guides","interrupted_history","free_chat","image_inventory","story_handoff","prompt_signals","project_folder","browser","resources","knowledge","script_bodies","presets","delivery_page","projects","shot_refs","navigation","story_comparison","providers","method_sync","custom_methods","contact_sheet"}:
        raise ValueError(script)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, ADW_TEST_PORT=str(port))
    if script == "history":
        env["ADW_TEST_HISTORY"] = "1"
    if script == "shot_wall":
        env["ADW_TEST_SHOT_WALL"] = "1"
    if script == "story_skill":
        env["ADW_TEST_STORY_SKILL"] = "1"
    if script == "script_methods":
        env["ADW_TEST_SCRIPT_METHODS"]="1"
    if script == "baselines":
        env["ADW_TEST_BASELINES"]="1"
    if script == "publish_handoff":
        env["ADW_TEST_PUBLISH_HANDOFF"]="1"
    if script == "interrupted_history":
        env["ADW_TEST_INTERRUPTION"]="1"
    if script in ("h3_sound", "mixed_media"):
        env["ADW_TEST_H3_SOUND"]="1"
    if script == "prompt_signals":
        env["ADW_TEST_SIGNALS"]="1"
    if script == "contact_sheet":
        env["ADW_TEST_CONTACT"]="1"
    if script == "method_sync":
        env["ADW_TEST_METHOD_SYNC"] = "1"
    if script == "browser":
        env["ADW_TEST_CAST"] = "1"
    if script == "story_comparison":
        env["ADW_TEST_STORY_COMPARE"]="1"
    if script == "shot_refs":
        env["ADW_TEST_SHOT_REFS"] = "1"
    if script == "delivery_page":
        env["ADW_TEST_DELIVERY_PAGE"] = "1"
    if script == "knowledge" and not env.get("ADW_LIBRARY_CLONE"):
        import ctypes
        import tempfile
        source = next((ROOT.parents[2]/"自媒体 2"/"对标分析").glob("知识库*/*/knowledgeIndex.sqlite")).parent
        clone = Path(tempfile.mkdtemp(prefix="adw-full-library-"))/"library"
        # macOS copy-on-write clone. Abort if unavailable rather than touching the mounted originals.
        libc = ctypes.CDLL(None,use_errno=True)
        if libc.clonefile(os.fsencode(source),os.fsencode(clone),0):
            raise OSError(ctypes.get_errno(),"无法创建知识库验收副本")
        env["ADW_LIBRARY_CLONE"] = str(clone)
    server = subprocess.Popen([sys.executable, str(ROOT / "serve.py")], env=env)
    try:
        for _ in range(150):
            if server.poll() is not None:
                raise RuntimeError("测试服务启动失败")
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/session", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError("测试服务未就绪")
        subprocess.run(["node", str(ROOT / (script+".cjs"))], check=True, env=env)
    finally:
        server.terminate()
        server.wait(timeout=15)


for script in sys.argv[1:] or ["browser","resources"]:
    run(script)
