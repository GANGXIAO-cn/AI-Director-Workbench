"""Run a packaged skill script, including on an offline Apple Silicon workstation."""
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    script = (ROOT / sys.argv[1]).resolve()
    if not script.is_relative_to(ROOT) or script.suffix != ".py":
        raise ValueError("请选择包内脚本")
    native = ROOT / "runtime" / (platform.system().lower() + "-" + platform.machine().lower())
    env = os.environ.copy()
    if (native / "ffmpeg").is_file():
        env["PATH"] = str(native) + os.pathsep + env.get("PATH", "")
    try:
        import numpy
        from PIL import ImageFont
    except ImportError:
        wheels = native / "wheels"
        if sys.version_info[:2] != (3, 11) or not wheels.is_dir():
            raise SystemExit("当前平台请先为工作台 Python 安装 numpy 和 Pillow，并配置 FFmpeg 和 ffprobe")
        cache = Path(env.get("ADW_FINECUT_CACHE", Path.home() / ".cache/adw-finecut")) / "cp311-arm64-2.2.6-9.5.0"
        env["PYTHONPATH"] = str(cache) + os.pathsep + env.get("PYTHONPATH", "")
        check = [sys.executable, "-c", "import numpy; from PIL import ImageFont"]
        if subprocess.run(check, env=env, capture_output=True).returncode:
            subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                            "--find-links", str(wheels), "--target", str(cache), "numpy==2.2.6", "Pillow==9.5.0"],
                           check=True, env=env)
    os.execve(sys.executable, [sys.executable, str(script), *sys.argv[2:]], env)


if __name__ == "__main__":
    main()
