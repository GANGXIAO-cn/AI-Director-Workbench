"""品牌动效字幕（2026-10-28 起，用户要求“大字 + 动效、有特色又符合品牌”）。

样式取自 Figma 网站文件 DR1xahLRGxrNj4S3AmPKub 的品牌片尾 283:587：
Bricolage Grotesque（opsz 14）、墨色 #202521、奶油 #FFF8E8、MILO 黄 #FFC731，句子大小写带句号（"A little MILO. Every day."）。
- 普通词：奶油字 + 墨色粗描边 + 墨色硬投影（贴纸感）
- 关键词（文案里写成 *word*，可跨词 *two words*）：墨字压在倾斜的 MILO 黄圆角胶囊上，同片尾商品胶囊
- 动效：逐词弹出（缩放回弹，错开 0.06s）；fx="shake" 弹出后抖动，fx="slam" 整句从大砸下来；结尾 0.08s 淡出
captions: [{t0, t1, text, y, fx?}]（成片时间，y 是文本块中心行）。逐帧画成透明图层，经管道叠到 src 上。
"""
import math, os, re, subprocess
from PIL import Image, ImageDraw, ImageFont
from common import FPS, run

from paths import BRAND_FONT as FONT
INK, CREAM, YELLOW = (32, 37, 33), (255, 248, 232), (255, 199, 49)
SIZE, MAX_W, W, H = 128, 960, 1080, 1920
MAX_W_LANDSCAPE, SIZE_LANDSCAPE = 1500, 96   # 16:9 成片：128px 一句就超宽折成两行、压到画框，横屏用 96px（2026-11-01 大镖客1 样帧定）


def _sync_frame(size=None):
    """按实际画面设成片宽高与折行宽：size=(宽,高) 取自要叠字幕的视频/样帧，缺省用 common.FRAME。"""
    global MAX_W, W, H, SIZE
    from common import frame, set_frame
    W, H = set_frame(*size) if size else frame()
    MAX_W = MAX_W_LANDSCAPE if W > H else 960
    SIZE = SIZE_LANDSCAPE if W > H else 128
STAGGER, POP, FADE = 0.06, 0.24, 0.08


def _font(size):
    f = ImageFont.truetype(FONT, size)
    f.set_variation_by_axes([14, 800, 100])   # opsz, wght, wdth
    return f


def _tokens(text):
    """'Wait. Is it the *grass*?' → [('Wait.',0),('Is',0),…,('grass',1),('?',…)]：*…* 内的词为关键词，标点贴在前一词上。"""
    out = []
    for part in re.split(r"(\*[^*]+\*)", text):
        if not part:
            continue
        hl = part.startswith("*")
        words = part.strip("*").split()
        if not words:
            continue
        if hl:
            out.append((" ".join(words), 1))
        else:
            if out and not part[0].isspace() and out[-1][1] == 1:   # 关键词后紧跟的标点：贴在胶囊右边
                out.append((words[0], 2)); words = words[1:]
            out += [(w, 0) for w in words]
    return out


def _sprite(word, kind, f):
    """画一个词：kind 0 普通，1 关键词胶囊，2 紧贴前词的标点。返回 RGBA（含投影边距）。"""
    st, sh = max(8, f.size // 8), max(8, f.size // 10)
    l, t, r, b = f.getbbox(word, anchor="ls")
    asc = -f.getbbox("H", anchor="ls")[1]
    if kind == 1:
        px, py = int(f.size * 0.26), int(f.size * 0.16)
        bw, bh = int(r - l) + 2 * px, int(asc + f.size * 0.22) + 2 * py
        pad = st + sh + 6
        im = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        rad = bh // 2.6
        d.rounded_rectangle((pad, pad + sh, pad + bw, pad + bh + sh), rad, fill=INK + (255,))          # 硬投影
        d.rounded_rectangle((pad, pad, pad + bw, pad + bh), rad, fill=YELLOW + (255,), outline=INK + (255,), width=st // 2 + 2)
        d.text((pad + px - l, pad + py + asc), word, font=f, fill=INK + (255,), anchor="ls")
        return im.rotate(-3.5, resample=Image.BICUBIC, expand=True)
    pad = st + sh + 4
    im = Image.new("RGBA", (int(r - l) + 2 * pad, int(asc + f.size * 0.3) + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    o = (pad - l, pad + asc)
    d.text((o[0], o[1] + sh), word, font=f, fill=INK + (255,), anchor="ls", stroke_width=st, stroke_fill=INK + (255,))
    d.text(o, word, font=f, fill=CREAM + (255,), anchor="ls", stroke_width=st, stroke_fill=INK + (255,))
    return im


def layout(text, size=None):
    """排版：按宽度折行（最多 3 行，放不下就缩字号）。返回 [(sprite, cx, cy)]，坐标相对文本块中心；以及块高。"""
    size = size or SIZE                                  # 调用时取当前画幅的字号（默认参数在定义时就固定了）
    while True:
        f = _font(size)
        toks = _tokens(text)
        sps = [(_sprite(w, k, f), k) for w, k in toks]
        gap = int(size * 0.2)
        lines, cur, cw = [], [], 0
        for sp, k in sps:
            bb = sp.getbbox()                                  # 按可见像素排版，描边/投影/胶囊旋转留的透明边不算
            w = bb[2] - bb[0]
            off = sp.width / 2 - (bb[0] + bb[2]) / 2
            g = -int(size * 0.02) if k == 2 else (gap if cur else 0)
            if cur and cw + g + w > MAX_W and k != 2:
                lines.append(cur); cur, cw, g = [], 0, 0
            cur.append((sp, w, g, off)); cw += g + w
        lines.append(cur)
        if len(lines) <= 3 or size < 70:
            break
        size -= 8
    lh = int(size * 1.22)
    out, top = [], -lh * len(lines) / 2
    for i, ln in enumerate(lines):
        tw = sum(g + w for _, w, g, _ in ln)
        x = -tw / 2
        for sp, w, g, off in ln:
            x += g
            out.append((sp, x + w / 2 + off, top + lh * (i + 0.5)))
            x += w
    return out, lh * len(lines)


def _back(u):   # easeOutBack，0→1 带回弹
    c = 1.9
    u -= 1
    return 1 + (c + 1) * u ** 3 + c * u ** 2


def render_frame(caps, t, canvas):
    for c in caps:
        if not (c["t0"] <= t < c["t1"]):
            continue
        fade = min(1.0, (c["t1"] - t) / FADE)
        for i, (sp, cx, cy) in enumerate(c["_lay"]):
            u = (t - c["t0"] - (0 if c.get("fx") == "slam" else i * STAGGER)) / POP
            if u <= 0:
                continue
            if c.get("fx") == "slam":
                s = 1 + 0.9 * (1 - min(u, 1)) ** 3
                a = min(1.0, u * 3)
            else:
                s = max(0.05, _back(min(u, 1)) if u < 1 else 1.0)
                a = min(1.0, u * 4)
            dx = 0
            if c.get("fx") == "shake":
                k = t - c["t0"] - len(c["_lay"]) * STAGGER - POP
                if 0 < k < 0.45:
                    dx = 14 * math.sin(k * 70) * (1 - k / 0.45)
            a *= fade
            w, h = max(1, int(sp.width * s)), max(1, int(sp.height * s))
            im = sp.resize((w, h), Image.BICUBIC) if (w, h) != sp.size else sp
            if a < 1:
                im = im.copy(); im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
            canvas.alpha_composite(im, (int(W / 2 + cx + dx - w / 2), int(c["y"] + cy - h / 2)))


def burn_brand_captions(work, captions, src="cut_raw.mov", dst="cut_sub.mov"):
    from common import probe
    v = probe(os.path.join(work, src))
    _sync_frame((v["width"], v["height"]))
    for c in captions:
        c["_lay"], c["h"] = layout(c["text"])
    n = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", os.path.join(work, src)],
                           capture_output=True, text=True).stdout)
    from render import ENC
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(work, src),
                          "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                          "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v]", "-map", "[v]", "-map", "0:a",
                          "-frames:v", str(n), *ENC[:10], "-c:a", "copy", os.path.join(work, dst)], stdin=subprocess.PIPE)
    blank = Image.new("RGBA", (W, H), (0, 0, 0, 0)).tobytes()
    for k in range(n):
        t = k / FPS
        if any(c["t0"] <= t < c["t1"] for c in captions):
            cv = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            render_frame(captions, t, cv)
            p.stdin.write(cv.tobytes())
        else:
            p.stdin.write(blank)
    p.stdin.close()
    if p.wait():
        raise RuntimeError("字幕叠加失败")
    for c in captions:
        c.pop("_lay", None)
    return os.path.join(work, dst)


def preview(captions, frames_at, out, bg=None):
    """出样帧：frames_at 是 [(成片时间, 背景图路径)]，拼成一张横图看样式和动效阶段。"""
    _sync_frame(Image.open(frames_at[0][1]).size if frames_at else None)
    for c in captions:
        c["_lay"], c["h"] = layout(c["text"])
    tiles = []
    for t, img in frames_at:
        cv = Image.open(img).convert("RGBA").resize((W, H))
        render_frame(captions, t, cv)
        tiles.append(cv.resize((360, 360 * H // W)))
    sheet = Image.new("RGB", (360 * len(tiles), 360 * H // W))
    for i, tl in enumerate(tiles):
        sheet.paste(tl, (360 * i, 0))
    sheet.save(out)
    for c in captions:
        c.pop("_lay", None)
