# 成片质检：画面映射核对、成片切点/闪帧复查、音效与音乐断点对齐测量、剪点卡拍表、响度、时间线图
# 用法：python3 qa.py <过程稿/精剪 目录>（读 timeline.json 与 stems/）
import json, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from common import FPS, probe, read_frames, read_audio
from source_versions import assert_fingerprint, checked_analysis_source
from analyze import detect_cuts, single_frame_flashes
from render import loudness, fill_vf

from paths import REPORT_FONT as FONT
SR = 22050


def frame_at(path, t, w=90, h=160, pre_vf=""):
    import subprocess
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
                        "-vf", f"{pre_vf + ',' if pre_vf else ''}scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.uint8).reshape(h, w).astype(np.float32)


def onset_near(y, t, window, sr=SR):
    """window 内最强起音的时刻（能量突增点）。"""
    a, b = int(max(0, t - window) * sr), int((t + window) * sr)
    seg = y[a:b]
    h = 128
    e = np.sqrt(np.convolve(seg ** 2, np.ones(h) / h, mode="same"))[::h // 2]
    rise = np.diff(e, prepend=e[0])
    i = int(np.argmax(rise))
    return a / sr + i * (h // 2) / sr, float(20 * np.log10(e.max() + 1e-9))


def music_edges(y, sr=SR, thr_db=-45):
    h = int(sr * 0.01)
    e = 20 * np.log10(np.sqrt(np.convolve(y ** 2, np.ones(h) / h, mode="same"))[::h] + 1e-9)
    on = e > thr_db
    edges = np.flatnonzero(np.diff(on.astype(int)))
    return [(round((i + 1) * h / sr, 3), "start" if on[i + 1] else "stop") for i in edges]


def qa(work):
    with open(os.path.join(work, "timeline.json"), encoding="utf-8") as stream:
        tl = json.load(stream)
    out_v = tl["outputs"][0]
    rough = tl["rough"]
    if tl.get("source_fingerprint"):
        assert_fingerprint(rough, tl["source_fingerprint"])
    checked_analysis_source(work, rough)
    clips = tl["clips"]
    R = {"mapping": [], "unexpected_cuts": [], "short_shots": [], "flashes": [], "events": [], "cut_beats": []}

    # 1) 画面映射：成片每段中点 vs 粗剪对应时刻
    for i, c in enumerate(clips):
        mid = (round((c["out0"] + c["out1"]) / 2 * FPS) + 0.5) / FPS   # 取帧中心，避免落在帧边界
        if c.get("fx"):
            continue
        d = np.abs(frame_at(out_v, mid) - frame_at(rough, c["src0"] + mid - c["out0"], pre_vf=fill_vf(tl["fill"]) if tl.get("fill") else "")).mean()
        R["mapping"].append((i, round(float(d), 2)))
    # 2) 成片切点复查
    rgb = read_frames(out_v, 90, 160)
    # 品牌片尾接在正片之后：只复查正片部分，片尾自带的弹出动画不算切点/闪帧
    R["outro_frames"] = len(rgb) - round(tl["total"] * FPS)
    rgb = rgb[:round(tl["total"] * FPS)]
    cuts, _, _, _ = detect_cuts(rgb)
    expect = [round(c["out0"] * FPS) for c in clips[1:]]
    for f in cuts:
        if all(abs(f - e) > 1 for e in expect):
            R["unexpected_cuts"].append(round(f / FPS, 3))
    bounds = sorted(set([0] + cuts + expect + [len(rgb)]))
    for a, b in zip(bounds, bounds[1:]):
        if 0 < b - a <= 5:
            R["short_shots"].append((round(a / FPS, 3), b - a))
    R["flashes"] = [round(i / FPS, 3) for i in single_frame_flashes(rgb)]
    # 3) 音频事件
    dia = read_audio(os.path.join(work, "stems", "dialog.wav"), SR)
    bgm_p = os.path.join(work, "stems", "bgm.wav")
    bgm = read_audio(bgm_p, SR) if os.path.exists(bgm_p) else None
    edges = music_edges(bgm) if bgm is not None else []
    for ev in tl["events"]:
        if ev["kind"] == "sound":
            t, lv = onset_near(dia, ev["out"], ev.get("window", 0.3))
            R["events"].append(dict(name=ev["name"], expect=ev["out"], measured=round(t, 3),
                                    delta_frames=round((t - ev["out"]) * FPS, 1), level_db=round(lv, 1)))
        else:
            want = "stop" if ev["kind"] == "music_stop" else "start"
            cand = [t for t, k in edges if k == want]
            t = min(cand, key=lambda x: abs(x - ev["out"])) if cand else None
            R["events"].append(dict(name=ev["name"], expect=ev["out"], measured=t,
                                    delta_frames=None if t is None else round((t - ev["out"]) * FPS, 1)))
    # 4) 剪点 vs 拍点
    bt = tl.get("beats")
    for i, c in enumerate(clips[1:], 1):
        t = c["out0"]
        for m in tl["music"]:
            if m["out0"] <= t <= m["out1"] and bt:
                bg = t - m["out0"] + m["bgm0"]
                k = round((bg - bt["t0"]) / bt["period"])
                off = bg - (bt["t0"] + k * bt["period"])
                R["cut_beats"].append((i, round(t, 3), round(off * FPS, 1), clips[i - 1].get("snap", 0)))
    # 5) 响度
    R["loudness"] = {os.path.basename(p): loudness(p) for p in tl["outputs"]}
    R["timeline_png"] = timeline_png(tl, dia, bgm, work)
    R["sheet"] = cut_sheet(out_v, clips, work)
    json.dump(R, open(os.path.join(work, "qa.json"), "w"), ensure_ascii=False, indent=1)
    write_md(R, tl, os.path.join(work, "质检报告.md"))
    return R


def timeline_png(tl, dia, bgm, work, W=2400, H=540):
    T = tl["total"]
    img = Image.new("RGB", (W, H), (20, 20, 22))
    dr = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, 18)
    x = lambda t: int(40 + (W - 80) * t / T)

    def wave(y, top, hgt, col):
        n = W - 80
        step = max(1, len(y) // n)
        for i in range(n):
            s = y[i * step:(i + 1) * step]
            if len(s):
                a = float(np.abs(s).max())
                dr.line([(40 + i, top + hgt / 2 - a * hgt / 2), (40 + i, top + hgt / 2 + a * hgt / 2)], fill=col)
    wave(dia, 60, 150, (120, 190, 255))
    dr.text((44, 40), "画内声", fill=(120, 190, 255), font=f)
    if bgm is not None:
        wave(bgm, 250, 150, (255, 170, 80))
        dr.text((44, 230), "BGM", fill=(255, 170, 80), font=f)
        bt = tl.get("beats")
        for m in tl["music"]:
            dr.rectangle([x(m["out0"]), 412, x(m["out1"]), 418], fill=(255, 170, 80))
            if bt:
                k0 = int(np.ceil((m["bgm0"] - bt["t0"]) / bt["period"]))
                while True:
                    b = bt["t0"] + k0 * bt["period"] - m["bgm0"] + m["out0"]
                    if b > m["out1"]:
                        break
                    dr.line([(x(b), 404), (x(b), 410)], fill=(200, 140, 60))
                    k0 += 1
    for i, c in enumerate(tl["clips"]):
        dr.line([(x(c["out0"]), 30), (x(c["out0"]), 420)], fill=(90, 90, 90))
        dr.text((x(c["out0"]) + 3, 8), str(i), fill=(160, 160, 160), font=f)
    for k, ev in enumerate(tl["events"]):
        y = 446 + 22 * (k % 2)          # 相邻标签上下错开
        dr.line([(x(ev["out"]), 30), (x(ev["out"]), y)], fill=(255, 80, 80), width=2)
        dr.text((x(ev["out"]) + 4, y), ev["name"], fill=(255, 120, 120), font=f)
    for s in range(0, int(T) + 1, 5):
        dr.text((x(s) - 8, 512), f"{s}s", fill=(130, 130, 130), font=f)
    fn = os.path.join(work, "qa_timeline.png")
    img.save(fn)
    return fn


def cut_sheet(out_v, clips, work, tw=120):
    """每个剪点前后各一帧（出点末帧 | 入点首帧），肉眼查跳帧/闪帧/穿帮。"""
    v = probe(out_v)
    th = round(tw * v["height"] / v["width"] / 2) * 2   # 按成片宽高比
    f = ImageFont.truetype(FONT, 16)
    rgb = read_frames(out_v, tw, th)
    cols = 6
    rows = max(1, (len(clips) - 1 + cols - 1) // cols)
    img = Image.new("RGB", (cols * (2 * tw + 16), rows * (th + 26)), (20, 20, 22))
    dr = ImageDraw.Draw(img)
    for k, c in enumerate(clips[1:]):
        fi = round(c["out0"] * FPS)
        r, q = divmod(k, cols)
        X, Y = q * (2 * tw + 16), r * (th + 26)
        for j, ff in enumerate((fi - 1, fi)):
            img.paste(Image.fromarray(rgb[min(max(ff, 0), len(rgb) - 1)]), (X + j * tw, Y + 22))
        dr.text((X + 2, Y + 2), f"剪点{k + 1}  {c['out0']:.2f}s", fill=(255, 210, 80), font=f)
    fn = os.path.join(work, "qa_cuts.jpg")
    img.save(fn, quality=85)
    return fn


def write_md(R, tl, fn):
    L = [f"# 质检报告：{tl.get('name', '')}", "", f"- 正片 {tl['total']:.2f}s，{len(tl['clips'])} 段", ""]
    if tl.get("outro"):
        L[-1:] = [f"- 品牌片尾 {os.path.basename(tl['outro'])}：成片末尾实测 {R.get('outro_frames', 0)} 帧"
                  f"（应为 {round(probe(tl['outro'])['duration'] * FPS)}），含片尾成片 {probe(tl['outputs'][0])['duration']:.2f}s", ""]
    bad = [m for m in R["mapping"] if m[1] > 6]
    L += ["## 画面映射", f"- 逐段中点与粗剪对应帧比对：{len(R['mapping'])} 段，" +
          ("全部一致" if not bad else f"偏差大的段：{bad}"), ""]
    L += ["## 切点与闪帧",
          f"- 剪辑表外多出来的切点：{R['unexpected_cuts'] or '无'}",
          f"- ≤5帧的短镜头：{R['short_shots'] or '无'}",
          f"- 单帧闪：{R['flashes'] or '无'}", ""]
    L += ["## 声音事件对齐（成片时间）", "|事件|预期|实测|偏差(帧)|", "|---|---|---|---|"]
    L += [f"|{e['name']}|{e['expect']:.2f}|{e['measured'] if e['measured'] is not None else '未测到'}|"
          f"{e['delta_frames']}|" for e in R["events"]]
    L += ["", "## 剪点卡拍（有音乐的段落）", "|剪点|成片时间|离最近拍点(帧)|卡拍调整(秒)|", "|---|---|---|---|"]
    L += [f"|{i}|{t:.2f}|{o:+.1f}|{s:+.3f}|" for i, t, o, s in R["cut_beats"]]
    L += ["", "## 响度"] + [f"- {k}：{v[0]:.1f} LUFS，真峰值 {v[1]:.1f} dBTP" for k, v in R["loudness"].items()]
    L += ["", f"时间线图：`{os.path.basename(R['timeline_png'])}`；剪点前后帧：`{os.path.basename(R['sheet'])}`"]
    open(fn, "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    qa(sys.argv[1])
    print(open(os.path.join(sys.argv[1], "质检报告.md")).read())
