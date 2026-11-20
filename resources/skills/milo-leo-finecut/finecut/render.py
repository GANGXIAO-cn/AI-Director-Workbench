# 精剪渲染：剪辑表（粗剪时间）→ 卡拍 → 逐段裁切+效果 → 拼接 → 分轨混音 → 两遍响度标准化 → 默认一版成片
# 每期在 过程稿/精剪/episode.py 里写剪辑表与配乐编排，调用这里的函数；时间线写进 timeline.json 供 QA 和剪映导出使用。
import json, os, re, subprocess
import numpy as np
from common import FPS, run, probe, frame, set_frame
from source_versions import checked_analysis_source, assert_fingerprint

from paths import BRAND, MONO_FONT as FONT_MONO, RUBIK_FONT as CAPTION_FONT
ENC = ["-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
       "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]


def fr(t):
    """时间取整到帧。"""
    return round(t * FPS) / FPS


def speed_of(c):
    return (c.get("fx") or {}).get("speed", 1.0)


def out_dur(c):
    """段在成片里的时长：变速段 = 源时长 / 速度，取整到帧。"""
    return round((c["src1"] - c["src0"]) / speed_of(c) * FPS) / FPS


class Timeline:
    """clips: [{src0, src1, fx, note}]（粗剪时间）。shots 用于卡拍时不越过镜头边界（防闪帧）。
    fx 写 speed（1.5 快进、0.5 慢放）时该段成片时长 = 源时长 / speed；同一镜头相邻几段配不同速度就是速度坡。"""

    def __init__(self, edl, shots=None):
        self.clips = [dict(src0=fr(a), src1=fr(b), fx=fx, note=n, snap=0.0) for a, b, fx, n in edl]
        self.shots = shots or []
        self.relayout()

    def relayout(self):
        t = 0.0
        for c in self.clips:
            c["out0"] = round(t, 4)
            t += out_dur(c)
            c["out1"] = round(t, 4)
        self.total = round(t, 4)

    def out(self, src_t):
        """粗剪时间 → 成片时间（落在被剪掉的部分会报错）。"""
        for c in self.clips:
            if c["src0"] - 1e-6 <= src_t <= c["src1"] + 1e-6:
                return c["out0"] + (src_t - c["src0"]) / speed_of(c)
        raise ValueError(f"粗剪 {src_t:.2f}s 不在剪辑表里")

    def shot_limits(self, c):
        for s in self.shots:
            if s["t0"] - 1e-6 <= c["src0"] < s["t1"]:
                return s["t0"], s["t1"]
        return 0.0, 1e9

    def snap_cuts(self, beat_out_times, max_shift=3 / FPS, skip=()):
        """把每个剪点（第 i 段末尾）吸附到最近拍点：只改该段源终点，不越过所在镜头边界、留 1 帧余量。
        beat_out_times(i) → 此刻有效的拍点（成片时间）列表，或 None（无音乐不卡）。"""
        report = []
        for i, c in enumerate(self.clips[:-1]):
            self.relayout()
            beats = beat_out_times(c["out1"])
            if i in skip or not beats:
                continue
            if abs(self.clips[i + 1]["src0"] - c["src1"]) < 1e-6:   # 同一镜头连续切开（速度坡）不是剪点
                continue
            b = min(beats, key=lambda x: abs(x - c["out1"]))
            d = fr(b) - c["out1"]
            if abs(d) > max_shift:
                continue
            _, s_end = self.shot_limits(c)
            new_end = fr(c["src1"] + d * speed_of(c))
            if new_end > s_end - 1 / FPS or new_end - c["src0"] < 0.5:
                continue
            c["src1"], c["snap"] = new_end, round(d, 4)
            report.append((i, round(d * FPS)))
        self.relayout()
        return report


def fill_vf(fill):
    """去掉素材自带的上下黑边：裁出 [top, bottom) 行的有效画面，等比放大到满高，左右居中裁回成片宽。"""
    W, H = frame()
    h = fill["bottom"] - fill["top"]
    w = round(W * H / h / 2) * 2
    return f"crop={W}:{h}:0:{fill['top']},scale={w}:{H}:flags=lanczos,crop={W}:{H}:{(w - W) // 2}:0"


def fill_scale(fill):
    """同一放大在 CapCut 里的等比缩放倍数（有效画面上下对称时不需要位移）。"""
    return frame()[1] / (fill["bottom"] - fill["top"])


def reframe_filter(windows, width, height):
    # Work in source frames; picture-only changes never split or fade the dialogue track.
    def expression(key, default):
        expr = str(default)
        for win in reversed(windows):
            expr = f"if(between(in,{win['first']},{win['stop']-1}),{win[key]},{expr})"
        return expr
    z = expression("zoom", 1)
    x = expression("x", 0.5)
    y = expression("y", 0.5)
    return (f"zoompan=z='{z}':x='(iw-iw/zoom)*({x})':y='(ih-ih/zoom)*({y})':"
            f"d=1:s={width}x{height}:fps={FPS}")


def cutaway_video_args(rough, clip, vf):
    """Assemble only the picture from equal-length spans. Input zero supplies the original audio."""
    base = round(clip["src0"]*FPS)
    length = round(clip["src1"]*FPS)-base
    spans = []
    cursor = 0
    for win in clip["fx"]["cutaways"]:
        first, stop = win["first"], win["stop"]
        if not cursor <= first < stop <= length:
            raise ValueError("替换镜头的帧序或范围无效")
        if first > cursor:
            spans.append((base+cursor, first-cursor))
        spans.append((win["sourceFrame"], stop-first))
        cursor = stop
    if cursor < length:
        spans.append((base+cursor, length-cursor))

    args, filters, labels = [], [], []
    for index, (start, count) in enumerate(spans, 1):
        seek = max(0, (start-.5)/FPS)
        args.extend(["-ss", f"{seek:.6f}", "-t", f"{(count+1)/FPS:.6f}", "-i", rough])
        label = f"[part{index}]"
        filters.append(f"[{index}:v]trim=end_frame={count},setpts=PTS-STARTPTS{label}")
        labels.append(label)
    # Frame units avoid accumulating sub-frame rounding at the joins.
    filters.append("".join(labels)+f"concat=n={len(spans)}:v=1:a=0,settb=1/{FPS},setpts=N,"+",".join(vf)+"[picture]")
    return [*args, "-filter_complex", ";".join(filters), "-map", "[picture]", "-map", "0:a"]


def render_video(tl, rough, work, audio_fixes=(), fill=None):
    """audio_fixes: [(粗剪起, 粗剪止, 增益dB)]，修粗剪画内声里的爆音/杂音，只作用在重叠的段上。
    fill: {top, bottom}，素材带上下黑边时等比放大铺满（见 fill_vf）。"""
    source_version = checked_analysis_source(work, rough)
    info = probe(rough)
    W, H = set_frame(info["width"], info["height"])   # 成片画幅跟随粗剪（竖 1080×1920 / 横 1920×1080）
    os.makedirs(os.path.join(work, "clips"), exist_ok=True)
    lst = []
    for i, c in enumerate(tl.clips):
        d = c["src1"] - c["src0"]
        fx = c["fx"] or {}
        sp = speed_of(c)
        od = out_dur(c)               # 成片时长；下面滤镜里的 t 都是成片时间（setpts 已按速度改过时间戳）
        vf = [f"setpts=(PTS-STARTPTS)/{sp}"] + ([fill_vf(fill)] if fill else [])
        if fx.get("reframes"):
            vf.insert(0, reframe_filter(fx["reframes"], W, H))
        if "zoom" in fx:
            # 推镜：从 zoom0（默认 1）放大到 zoom，在段内 zt=(起,止) 秒之间完成（默认整段），ease 用平滑曲线；
            # zoom0 == zoom 就是整段固定的局部放大。中心 (cx,cy) 0–1
            z0, z1 = fx.get("zoom0", 1.0), fx["zoom"]
            a, b = fx.get("zt", (0.0, od))
            p = f"clip((t-{a:.3f})/{max(b - a, 1e-3):.3f},0,1)"
            if fx.get("ease"):
                p = f"({p}*{p}*(3-2*{p}))"
            z = f"({z0:.4f}+{z1 - z0:.4f}*{p})"
            cx, cy = fx.get("center", (0.5, 0.5))
            vf += [f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame",
                   f"crop={W}:{H}:'(iw-{W})*{cx}':'(ih-{H})*{cy}'"]
        if "shake" in fx:
            # 冲击震屏：[(段内起点秒, 时长, 幅度px)]。包络从 1 二次衰减到 0；震动期间同步微放大（冲击感），
            # 衰减完回到原构图，所以和同镜头相邻段接得上
            env = [f"between(t,{t0:.3f},{t0 + du:.3f})*pow(1-(t-{t0:.3f})/{du:.3f},2)" for t0, du, _ in fx["shake"]]
            amp = max(a for _, _, a in fx["shake"])
            k = max(0.03, amp / 450)
            E = "(" + "+".join(env) + ")"
            ox = "+".join(f"{am}*{e}*sin(2*PI*17*(t-{t0:.3f}))" for (t0, _, am), e in zip(fx["shake"], env))
            oy = "+".join(f"{am * 0.8}*{e}*sin(2*PI*13*(t-{t0:.3f})+1.3)" for (t0, _, am), e in zip(fx["shake"], env))
            vf += [f"scale=w='trunc({W}*(1+{k}*{E})/2)*2':h='trunc({H}*(1+{k}*{E})/2)*2':eval=frame",
                   f"crop={W}:{H}:'(iw-{W})/2+{ox}':'(ih-{H})/2+{oy}'"]
        if "cctv" in fx:
            cc = f"fontfile={FONT_MONO}:shadowcolor=black@0.8:shadowx=2:shadowy=2"
            vf += [f"drawtext={cc}:text='REC':fontcolor=0xFF3B30:fontsize=44:x=60:y=170:enable='lt(mod(t,1),0.6)'",
                   f"drawtext={cc}:text='{fx['cctv']}':fontcolor=white:fontsize=40:x=60:y=226"]
        af = ""                       # 修音按粗剪（源）时间，放在变速之前
        for a, b, g in audio_fixes:
            if a < c["src1"] and b > c["src0"]:
                af += f"volume={g}dB:enable='between(t,{max(0, a - c['src0']):.3f},{b - c['src0']:.3f})',"
                c.setdefault("audio_fixes", []).append([a, b, g])
        if sp != 1.0:
            af += f"atempo={sp},"
        af += f"atrim=0:{od:.4f},afade=t=in:d=0.012,afade=t=out:st={od - 0.012:.3f}:d=0.012"
        if sp < 1.0:                  # 慢放：最后一帧没有时长可拉伸，会少 1 帧，复制末帧补齐（-frames:v 截到准确帧数）
            vf.insert(1, "tpad=stop_mode=clone:stop_duration=0.2")   # 放在效果之前，补的帧也照常推镜/震屏
        out = os.path.join(work, "clips", f"{i:02d}.mov")
        # 视频按帧数截：只给 -t 时 ffmpeg 常多出 1 帧，音频不多，拼接后画面逐段滞后于声音。
        # 音频从同一文件第二路输入取、多读 0.2s，atempo 变速后不会短，再用 atrim 截到成片时长（不能用输出 -t：会把慢放段末帧也截掉）
        # （这里的 ffmpeg 用 apad 补齐会卡死，2026-10-20 实测，别用）
        # 画面提前半帧 seek：src0 是整帧时间，按 %.4f 写出时 x.xx667 会进位到帧时间之后，精确 seek 就丢掉首帧、
        # 段尾多带下一帧（1025/1026/1027 段边界剪点的 1 帧短镜头都是这个，不是“渲染整体晚 1 帧”）
        vss = max(0.0, c["src0"] - 0.5 / FPS)
        audio_input = ["-ss", f"{c['src0']:.4f}", "-t", f"{d + 0.2:.4f}", "-i", rough]
        if fx.get("cutaways"):
            source_args = [*audio_input, *cutaway_video_args(rough, c, vf)]
        else:
            source_args = ["-ss", f"{vss:.4f}", "-t", f"{d + c['src0'] - vss:.4f}", "-i", rough,
                           *audio_input, "-map", "0:v", "-map", "1:a", "-vf", ",".join(vf)]
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", *source_args,
             "-frames:v", str(round(od * FPS)), "-af", af, *ENC, out])
        lst.append(f"file '{i:02d}.mov'")
    open(os.path.join(work, "clips", "list.txt"), "w").write("\n".join(lst) + "\n")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", os.path.join(work, "clips", "list.txt"),
         "-c", "copy", os.path.join(work, "cut_raw.mov")])

    if source_version:
        assert_fingerprint(rough, source_version)


def _loudnorm(inp, out, target=-14, tp=-1.5, extra_af=""):
    """两遍 loudnorm：先量再线性校正；动态范围太大做不到线性时 ffmpeg 自动退回动态模式（会在报告里注明）。"""
    pre = (extra_af + ",") if extra_af else ""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", inp, "-af",
                        f"{pre}loudnorm=I={target}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = json.loads(re.findall(r"\{[^{}]*\}", p.stderr)[-1])
    af = (f"{pre}loudnorm=I={target}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    run(["ffmpeg", "-v", "error", "-y", "-i", inp, "-af", af, "-ar", "48000", "-c:a", "pcm_s24le", out])
    return m


def loudness(path):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = json.loads(re.findall(r"\{[^{}]*\}", p.stderr)[-1])
    return float(m["input_i"]), float(m["input_tp"])


def mix(tl, work, bgm=None, music=(), sfx=(), bgm_gain_db=-7.0, duck=True, *, include_no_bgm=False):
    """music: [{out0, out1, bgm0, fade_in, fade_out}]（成片时间 / BGM 时间）；
    sfx: [{file, at(成片时间, 指样本起点), gain_db}]。产出 stems 与 master。"""
    raw = os.path.join(work, "cut_raw.mov")
    T = tl.total
    st = os.path.join(work, "stems"); os.makedirs(st, exist_ok=True)
    # 画内声补静音到成片全长：变速段让音轨比画面短零点几秒，短的那条会让后面带 BGM 的混音提前收尾
    run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-vn", "-af", f"apad,atrim=0:{T:.4f}", "-ar", "48000", "-ac", "2",
         "-c:a", "pcm_s24le", os.path.join(st, "dialog.wav")])
    stems = {"dialog": os.path.join(st, "dialog.wav")}
    if bgm and music:
        parts, labels = [], []
        for k, m in enumerate(music):
            d = m["out1"] - m["out0"]
            fi, fo = m.get("fade_in", 0.01), m.get("fade_out", 0.04)
            # 前面垫真实静音再拼接：ffmpeg 8 的 adelay 只挪时间戳，wav 输出会把音乐放回 0 秒
            fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
            parts.append(f"[0:a]atrim={m['bgm0']:.4f}:{m['bgm0'] + d:.4f},asetpts=PTS-STARTPTS,{fmt},"
                         f"afade=t=in:d={fi},afade=t=out:st={d - fo:.4f}:d={fo}"
                         + (f"[b{k}];anullsrc=r=48000:cl=stereo,atrim=0:{m['out0']:.4f},{fmt}[z{k}];"
                            f"[z{k}][b{k}]concat=n=2:v=0:a=1[m{k}]" if m["out0"] > 0 else f"[m{k}]"))
            labels.append(f"[m{k}]")
        fc = ";".join(parts) + f";{''.join(labels)}amix=inputs={len(labels)}:normalize=0,apad,atrim=0:{T:.4f}[out]"
        run(["ffmpeg", "-v", "error", "-y", "-i", bgm, "-filter_complex", fc, "-map", "[out]", "-ar", "48000",
             "-ac", "2", "-c:a", "pcm_s24le", os.path.join(st, "bgm.wav")])
        stems["bgm"] = os.path.join(st, "bgm.wav")
    if sfx:
        ins, parts = [], []
        for k, s in enumerate(sfx):
            ins += ["-i", s["file"]]
            parts.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={s.get('gain_db', 0)}dB,"
                         f"adelay={int(round(s['at'] * 1000))}:all=1[s{k}]")
        fc = ";".join(parts) + f";{''.join(f'[s{k}]' for k in range(len(sfx)))}amix=inputs={len(sfx)}:normalize=0,apad,atrim=0:{T:.4f}[out]"
        run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc, "-map", "[out]", "-c:a", "pcm_s24le",
             os.path.join(st, "sfx.wav")])
        stems["sfx"] = os.path.join(st, "sfx.wav")

    # 带 BGM 版：画内声 + 音效 + BGM（BGM 压低并对画内声/音效侧链让位）
    # 各路先多垫 2s 静音、混完再裁回全长：sidechaincompress+amix 在流结尾的最后约 1s 输出不稳定（每次跑不一样，
    # 2026-11-01 实测会把片尾 0.5–1.1s 的 BGM 吞掉），让结尾落在裁掉的部分里，结果逐样本可复现
    pad = f"apad=whole_dur={T + 2:.4f}"
    ins = ["-i", stems["dialog"]]
    fg = f"[0:a]{pad}[d]"
    fx_in = "[d]"
    if "sfx" in stems:
        ins += ["-i", stems["sfx"]]
        fg += f";[{len(ins) // 2 - 1}:a]{pad}[sp];[d][sp]amix=inputs=2:normalize=0[ds]"
        fx_in = "[ds]"
    if "bgm" in stems:
        ins += ["-i", stems["bgm"]]
        bi = len(ins) // 2 - 1
        if duck:
            fg += (f";{fx_in}asplit[fg][key];[{bi}:a]{pad},volume={bgm_gain_db}dB[bg];"
                   f"[bg][key]sidechaincompress=threshold=0.06:ratio=3:attack=20:release=350:makeup=1[duck];"
                   f"[fg][duck]amix=inputs=2:normalize=0,atrim=0:{T:.4f}[mix]")
        else:
            fg += f";[{bi}:a]{pad},volume={bgm_gain_db}dB[bg];{fx_in}[bg]amix=inputs=2:normalize=0,atrim=0:{T:.4f}[mix]"
    else:
        fg += f";{fx_in}atrim=0:{T:.4f}[mix]"
    run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fg, "-map", "[mix]", "-c:a", "pcm_s24le",
         os.path.join(work, "premix.wav")])
    # 先限峰再两遍标准化：粗剪里常有削波的撞击声，限峰后线性标准化才拉得到 -14
    lim = "alimiter=limit=0.5:attack=3:release=60:level=disabled"
    _loudnorm(os.path.join(work, "premix.wav"), os.path.join(work, "master.wav"), extra_af=lim)
    masters = [("master.wav", "BGM" if "bgm" in stems else "原声")]
    if include_no_bgm and "bgm" in stems:
        # 无 BGM 版：画内声 + 音效
        ins2 = ["-i", stems["dialog"]] + (["-i", stems["sfx"]] if "sfx" in stems else [])
        fg2 = "[0:a][1:a]amix=inputs=2:normalize=0[m]" if "sfx" in stems else "[0:a]anull[m]"
        run(["ffmpeg", "-v", "error", "-y", *ins2, "-filter_complex", fg2, "-map", "[m]", "-c:a", "pcm_s24le",
             os.path.join(work, "premix_noBGM.wav")])
        _loudnorm(os.path.join(work, "premix_noBGM.wav"), os.path.join(work, "master_noBGM.wav"), extra_af=lim)
        masters.append(("master_noBGM.wav", "无BGM"))
    with open(os.path.join(work, "audio_masters.json"), "w", encoding="utf-8") as stream:
        json.dump(masters, stream, ensure_ascii=False)
    return stems


# 字幕：沿用频道之前在 CapCut 里用的样式——Rubik Bold 全大写、黄字黑描边（0404 那批草稿）
CAPTION_FILL = (240, 255, 0)


def caption_png(text, out, size=74, max_w=940):
    """一条字幕画成成片宽的透明 PNG：自动折行、居中，黑描边。返回 (图路径, 文本块高度)。
    纵向位置由 overlay 决定，这里只画在顶端，方便 CapCut 和烧录共用同一个 y。"""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(CAPTION_FONT, size)
    f.set_variation_by_axes([700])
    words = text.split()
    if f.getlength(text) <= max_w:
        lines = [text]
    else:   # 折两行时取两行最均衡的断点，避免末行只剩一个词
        cuts = [(max(f.getlength(" ".join(words[:i])), f.getlength(" ".join(words[i:]))), i) for i in range(1, len(words))]
        i = min(cuts)[1]
        lines = [" ".join(words[:i]), " ".join(words[i:])]
    lh = int(size * 1.18)
    stroke = max(4, round(size * 0.09))
    h = lh * len(lines) + stroke * 2
    im = Image.new("RGBA", (frame()[0], h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((frame()[0] // 2, stroke + i * lh), ln, font=f, fill=CAPTION_FILL + (255,), anchor="ma",
               stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    im.save(out)
    return out, h


# 品牌片尾（2 秒）：series/milo-leo/品牌片尾/make_sting.py 渲染。2026-10-28 起每期默认都接（用户要求）。
# 片尾接在正片之后、不进 Timeline：响度也按正片单独标准化（片尾提示音约 -18 LUFS）。
OUTRO_DIR = str(BRAND / "out")
OUTRO_PRODUCTS = ("tote", "mug", "tee")    # 商品位按 ISO 周轮换，一周内各期一致
AUTO = "auto"


def outro_clip(product=None):
    if product is None:
        import datetime
        product = OUTRO_PRODUCTS[datetime.date.today().isocalendar()[1] % len(OUTRO_PRODUCTS)]
    p = os.path.normpath(os.path.join(OUTRO_DIR, f"PuggyRoom_EndSting_{product}.mp4"))
    if not os.path.exists(p):
        raise FileNotFoundError(f"没有片尾 {p}，先跑 品牌片尾/make_sting.py {product}")
    return p


STING_BG = "0xFEF8E8"   # 片尾奶油底色（实测竖版片尾背景）


def fit_outro(path):
    """片尾按成片画幅取用：竖版用 2 秒品牌片尾；16:9 长片换成同商品的 10 秒 YouTube 结束画面
    （PuggyRoom_EndScreen16x9_<商品>.mp4，Figma 300:587，make_sting.py --landscape 生成；两个视频位 + 订阅圈留给
    YouTube Studio 的结束画面元素）。其他尺寸才退回“竖版片尾等高居中、两侧补奶油底”，缓存在 品牌片尾/out/<宽>x<高>/。"""
    if not path:
        return path
    W, H = frame()
    info = probe(path)
    if (info["width"], info["height"]) == (W, H):
        return path
    m = re.match(r"PuggyRoom_EndSting_(\w+)\.mp4$", os.path.basename(path))
    if m and (W, H) == (1920, 1080):
        es = os.path.join(os.path.dirname(path), f"PuggyRoom_EndScreen16x9_{m.group(1)}.mp4")
        if not os.path.exists(es):
            raise FileNotFoundError(f"没有 16:9 结束画面 {es}，先跑 品牌片尾/make_sting.py --landscape {m.group(1)}")
        return es
    d = os.path.join(os.path.dirname(path), f"{W}x{H}")
    out = os.path.join(d, os.path.basename(path))
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
        os.makedirs(d, exist_ok=True)
        run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf",
             f"scale=-2:{H}:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={STING_BG},setsar=1",
             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "copy", out])
    return out


def mux(work, out_dir, name, video="cut_raw.mov", outro=AUTO):
    """outro：默认按本周商品接片尾；也可传 outro_clip("mug") 指定，或 None 不接（仅用户明确要求时）。
    save_timeline 用同样的默认，CapCut 草稿才会带上同一个片尾。"""
    if outro == AUTO:
        outro = outro_clip()
    outro = fit_outro(outro)
    outs = []
    manifest = os.path.join(work, "audio_masters.json")
    if os.path.isfile(manifest):
        with open(manifest, encoding="utf-8") as stream:
            masters = json.load(stream)
    else:
        # 兼容尚未重新混音的旧工程，只封装已有主混音。
        tag = "BGM" if os.path.isfile(os.path.join(work, "stems", "bgm.wav")) else "原声"
        masters = [("master.wav", tag)]
    for wav, tag in masters:
        fn = os.path.join(out_dir, f"{name}_精剪_{tag}.mp4")
        ins = ["-i", os.path.join(work, video), "-i", os.path.join(work, wav)]
        if outro:
            # 正片按画面帧数截齐再拼片尾：master 比画面长出的零头会把片尾推后
            n = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
                                    "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
                                    os.path.join(work, video)], capture_output=True, text=True).stdout)
            T = n / FPS
            fc = (f"[0:v]trim=end_frame={n},setpts=PTS-STARTPTS[v0];"
                  f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{T:.6f},asetpts=PTS-STARTPTS[a0];"
                  "[2:v]setpts=PTS-STARTPTS[v1];"
                  "[2:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
                  "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
            maps = ["-i", outro, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-r", str(FPS)]
        else:
            maps = ["-map", "0:v", "-map", "1:a", "-shortest"]
        run(["ffmpeg", "-v", "error", "-y", *ins, *maps, "-c:v", "libx264", "-preset", "slow", "-crf", "18",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "256k", fn])
        outs.append(fn)
    return outs


def save_timeline(tl, work, rough, bgm=None, music=(), sfx=(), beats=None, events=(), bgm_gain_db=-7.0, extra=None,
                  outro=AUTO):
    source_version = checked_analysis_source(work, rough)
    j = dict(fps=FPS, rough=rough, total=tl.total, clips=tl.clips, bgm=bgm, bgm_gain_db=bgm_gain_db,
             music=list(music), sfx=list(sfx), events=list(events),
             beats=beats, outro=fit_outro(outro_clip() if outro == AUTO else outro), frame=list(frame()),
             **(extra or {}))
    if source_version:
        j["source_fingerprint"] = source_version
    with open(os.path.join(work, "timeline.json"), "w", encoding="utf-8") as stream:
        json.dump(j, stream, ensure_ascii=False, indent=1)
    return j
