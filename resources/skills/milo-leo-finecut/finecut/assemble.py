# 分段粗剪拼接：粗剪稿/<MMDD>/<名字>/ 里是 01·xxx_V1.mp4 … 这样的生成段、没有整条粗剪 mp4 时，
# 按顺序拼成一条“粗剪”给 analyze/render 用（2026-10-28 起各期手写 concat.sh 的通用版）。
# 画幅跟随素材：竖素材铺满 1080×1920，横素材铺满 1920×1080（等比放大、居中裁掉多出的边）；转 30fps，
# 每段截到整帧 floor(时长×30)，音频 48k 立体声同长。段落版本、指纹、在拼接稿里的起止写进 segments.json。
#   $P assemble.py <段落目录> <输出.mp4> [--files 01·a_V2.mp4 02·b_V1.mp4 …]
# 同一序号有多个版本（V1/V2）时不自行挑选：报错，由 --files 写明用哪几个（按用户选择和实际交接确认）。
import argparse, hashlib, json, math, os, re, subprocess, sys
from common import FPS, probe

FRAMES = {False: (1080, 1920), True: (1920, 1080)}   # 是否横屏 → 成片画幅


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pick(folder, files):
    if files:
        return [os.path.join(folder, f) for f in files]
    mp4 = sorted(f for f in os.listdir(folder) if f.lower().endswith(".mp4") and re.match(r"\d", f))
    by_no = {}
    for f in mp4:
        by_no.setdefault(re.match(r"\d+", f).group(), []).append(f)
    dup = {k: v for k, v in by_no.items() if len(v) > 1}
    if dup:
        sys.exit("同一序号有多个文件，用 --files 写明用哪一个：" + "；".join(f"{k}: {', '.join(v)}" for k, v in dup.items()))
    return [os.path.join(folder, f) for f in mp4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("out")
    ap.add_argument("--files", nargs="+", help="按顺序列出要用的段落文件名")
    a = ap.parse_args()
    paths = pick(a.folder, a.files)
    assemble(paths, a.out)


def assemble(paths, output):
    infos = [probe(p) for p in paths]
    land = {i["width"] > i["height"] for i in infos}
    if len(land) != 1:
        sys.exit("段落里横竖混用，先确认成片画幅")
    W, H = FRAMES[land.pop()]
    args, fc, cat, segs, t0 = [], "", "", [], 0.0
    for k, (p, info) in enumerate(zip(paths, infos)):
        nf = math.floor(info["duration"] * FPS + 1e-6)
        t = nf / FPS
        args += ["-i", p]
        # 先把短的一边放满（另一边 -2 取偶数），再居中裁：与 10-28 起各期 concat.sh 逐帧一致
        fit = f"{W}:-2" if info["width"] / info["height"] < W / H else f"-2:{H}"
        fc += (f"[{k}:v]scale={fit}:flags=lanczos,crop={W}:{H},setsar=1,"
               f"fps={FPS},trim=end_frame={nf},setpts=PTS-STARTPTS[v{k}];"
               )
        audio = f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,apad" if info.get("has_audio") else "anullsrc=r=48000:cl=stereo"
        fc += f"{audio},atrim=end={t:.6f},asetpts=PTS-STARTPTS[a{k}];"
        cat += f"[v{k}][a{k}]"
        segs.append(dict(order=k + 1, file=os.path.abspath(p), bytes=os.path.getsize(p), sha256=sha256(p),
                         width=info["width"], height=info["height"], rough_t0=round(t0, 4), rough_t1=round(t0 + t, 4)))
        print(f"{k + 1:02d} {os.path.basename(p)}  {info['width']}×{info['height']}  {nf} 帧 → {t0 + t:.3f}s")
        t0 += t
    fc += f"{cat}concat=n={len(paths)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", output], check=True)
    meta = dict(frame=[W, H], total=round(t0, 4), segments=segs)
    with open(os.path.join(os.path.dirname(os.path.abspath(output)), "segments.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"拼接稿 {output}：{W}×{H}，{t0:.3f}s，{len(paths)} 段；segments.json 已写")

    return meta


if __name__ == "__main__":
    main()
