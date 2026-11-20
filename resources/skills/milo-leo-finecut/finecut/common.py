# 精剪工具共用：ffmpeg 读帧/读音频、CapCut 粗剪草稿映射
import json, os, subprocess
import numpy as np

FPS = 30
# 成片画幅（2026-11-01 起支持 16:9）：跟随实际粗剪的宽高，render_video 开头按粗剪设定；推镜/震屏/字幕/片尾/草稿画布都读这里。
# 默认竖屏 1080×1920。横屏粗剪先拼成 1920×1080（见 assemble.py）。
FRAME = [1080, 1920]


def set_frame(w, h):
    FRAME[:] = [int(w), int(h)]
    return tuple(FRAME)


def frame():
    return tuple(FRAME)


def landscape():
    return FRAME[0] > FRAME[1]


def run(cmd):
    subprocess.run(cmd, check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,width,height,r_frame_rate",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    info = {"duration": float(j["format"]["duration"])}
    for s in j["streams"]:
        if s["codec_type"] == "video":
            n, d = s["r_frame_rate"].split("/")
            info.update(width=s["width"], height=s["height"], fps=float(n) / float(d))
        if s["codec_type"] == "audio":
            info["has_audio"] = True
    return info


def read_frames(path, w, h, gray=False, fps=FPS):
    """整段解码为 (N,h,w[,3]) uint8；用于分析，分辨率要小。"""
    pix = "gray" if gray else "rgb24"
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale={w}:{h}:flags=area",
                        "-f", "rawvideo", "-pix_fmt", pix, "-"], capture_output=True, check=True)
    c = 1 if gray else 3
    a = np.frombuffer(p.stdout, np.uint8)
    n = a.size // (w * h * c)
    a = a[: n * w * h * c]
    return a.reshape((n, h, w) if gray else (n, h, w, 3))


def read_audio(path, sr=22050, mono=True, start=None, dur=None):
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "1" if mono else "2", "-ar", str(sr), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True, check=True)
    a = np.frombuffer(p.stdout, np.float32).copy()
    return a if mono else a.reshape(-1, 2)


def grab(path, t, out, w=None):
    vf = ["-vf", f"scale={w}:-2"] if w else []
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", *vf, "-q:v", "3", out])


# ---------- CapCut 粗剪草稿：粗剪时间 ↔ 原始生成段 ----------
from paths import CAPCUT_ROOT


def load_roughcut_map(draft_dir):
    """读用户在 CapCut 里拼粗剪的草稿，返回主视频轨每段：
    {rough_start, rough_end, file, file_start, speed, width, height}（秒）。只读，不改草稿。"""
    with open(os.path.join(draft_dir, "draft_info.json"), encoding="utf-8") as stream:
        j = json.load(stream)
    vids = {v["id"]: v for v in j["materials"]["videos"]}
    vt = [t for t in j["tracks"] if t["type"] == "video" and t["segments"]]
    if not vt:
        return []
    main = vt[0]
    segs = []
    for s in main["segments"]:
        v = vids[s["material_id"]]
        tr, sr = s["target_timerange"], s["source_timerange"]
        segs.append(dict(rough_start=tr["start"] / 1e6, rough_end=(tr["start"] + tr["duration"]) / 1e6,
                         file=v["path"], file_start=sr["start"] / 1e6, speed=s.get("speed", 1.0),
                         width=v["width"], height=v["height"]))
    return segs


def resolve(segs, t0, t1):
    """把粗剪区间 [t0,t1) 拆成落在原始文件上的若干片：[(file, file_t0, file_t1, rough_t0), ...]"""
    out = []
    for s in segs:
        a, b = max(t0, s["rough_start"]), min(t1, s["rough_end"])
        if b - a > 1e-4:
            k = s["speed"]
            out.append((s["file"], s["file_start"] + (a - s["rough_start"]) * k,
                        s["file_start"] + (b - s["rough_start"]) * k, a))
    return out
