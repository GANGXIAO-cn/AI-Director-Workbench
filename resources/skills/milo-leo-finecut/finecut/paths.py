"""Paths resolved inside the distributed skill, regardless of the launch directory."""
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "runtime" / (platform.system().lower() + "-" + platform.machine().lower())
if (NATIVE / "ffmpeg").is_file():
    os.environ["PATH"] = os.pathsep.join((str(NATIVE), os.environ.get("PATH", "")))
BRAND = ROOT / "品牌片尾"
BRAND_FONT = str(BRAND / "src/fonts/BricolageGrotesque-VF.ttf")
REPORT_FONT = str(ROOT / "fonts/NotoSansSC.ttf")
MONO_FONT = str(ROOT / "fonts/RobotoMono.ttf")
RUBIK_FONT = str(ROOT / "fonts/Rubik.ttf")
DRAFT_TEMPLATE = ROOT / "templates/capcut"
CAPCUT_ROOT = os.environ.get("ADW_CAPCUT_ROOT") or str(Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft")
