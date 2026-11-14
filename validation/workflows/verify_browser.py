"""Launch an isolated simulated service and run the browser workflows against that instance."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
def test_X_T2_offline_page(script):
    """X-T2: execute the existing page assertions against an isolated service."""
    if script not in {"continuous_images","fill_defaults","quickstart","continuous","auto_chat_preview","stress_repairs","asset_worksheet","project_stage_methods","stage_inputs","review_scope","director_decisions","supplements","original_handoff","idea_handoff","kickoff_checks","cli_connections","layout_controls","stage_checklist","activity_log","library_images","empty_project","grid_cells","episode_specs","topic_research","revision_sessions","cooperation","project_context","director_inputs","editing_navigation","history","shot_wall","enhancements","story_skill","script_methods","home","archive_page","baselines","publish_handoff","mixed_media","h3_sound","h3_guides","interrupted_history","free_chat","image_inventory","story_handoff","prompt_signals","project_folder","browser","resources","knowledge","script_bodies","presets","delivery_page","projects","shot_refs","navigation","story_comparison","providers","method_sync","custom_methods","contact_sheet"}:
        raise ValueError(script)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, ADW_TEST_PORT=str(port))
    if script=="continuous" and env.get("ADW_CONTINUOUS_PAGE")=="preview":env["ADW_TEST_CONTINUOUS_PREVIEW"]="1"
    if script=="continuous" and env.get("ADW_CONTINUOUS_PAGE")=="direction":env["ADW_TEST_QUICKSTART"]="1"
    if script=="continuous" and env.get("ADW_CONTINUOUS_PAGE")=="images":env["ADW_TEST_CONTINUOUS_IMAGES"]="1"
    if script in ("continuous_images","fill_defaults","quickstart","continuous","auto_chat_preview","stress_repairs","asset_worksheet","project_stage_methods","stage_inputs","review_scope","supplements"):
        if script!="continuous" or env.get("ADW_CONTINUOUS_PAGE") not in ("direction","images"):
            env["ADW_TEST_"+script.upper()] = "1"
    if script == "kickoff_checks":
        env["ADW_TEST_KICKOFF_CHECKS"]="1"
    if script == "cli_connections":
        env["ADW_TEST_CLI_CONNECTIONS"]="1"
    if script == "layout_controls":
        env["ADW_TEST_LAYOUT_CONTROLS"]="1"
    if script == "stage_checklist":
        env["ADW_TEST_STAGE_CHECKLIST"]="1"
    if script == "activity_log":
        env["ADW_TEST_ACTIVITY_LOG"]="1"
    if script == "library_images":
        env["ADW_TEST_LIBRARY_IMAGES"]="1"
    if script in ("empty_project","idea_handoff","original_handoff"):
        env["ADW_TEST_EMPTY_PROJECT"]="1"
    if script == "grid_cells":
        env["ADW_TEST_GRID_CELLS"]="1"
    if script == "topic_research":
        env["ADW_TEST_TOPIC_RESEARCH"]="1"
    if script == "project_context":
        env["ADW_TEST_PROJECT_CONTEXT"]="1"
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
        source = (ROOT.parents[1]/"resources"/"知识库~MD压缩版(1)"/"全球热门与获奖影视剧本知识库MD版")
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
    test_X_T2_offline_page(script)
