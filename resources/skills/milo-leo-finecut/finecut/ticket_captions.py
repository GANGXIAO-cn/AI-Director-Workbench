"""小票卡字幕（2026-10-31 起，用户要求“有字幕，但和以前的形式有所差别”）。品牌动效字幕的姊妹款，同字体同配色、换形式。

与 brand_captions（居中巨字、逐词弹出、黄胶囊）的区别：
- 整句写在一张奶油色圆角卡片上（墨色描边 + 墨色硬投影），墨色字、左对齐，像一张订单小票
- 卡片左上角可挂黄色小标签 kicker（PROBLEM / PLAN B 之类步骤名，大写小字），把一集串成一张任务单
- 关键词 *word* 用黄色荧光笔从左往右划出来，不是胶囊
- 动效：整卡带倾角从下方回弹入场，再划荧光笔；fx="stamp" 整卡像盖章一样从大砸下，fx="shake" 入场后左右抖；离场 0.12s 上浮淡出
- 卡片角度按条交替 ±2.5°，默认放画面上部（以往在中下）
captions: [{t0, t1, text, y, x?, kicker?, fx?}]（成片时间；y/x 是卡片中心）。接口与 brand_captions 相同。
"""
import math, os, re, subprocess
from PIL import Image, ImageDraw, ImageFont
from common import FPS

from paths import BRAND_FONT as FONT
INK, CREAM, YELLOW = (32, 37, 33), (255, 248, 232), (255, 199, 49)
SIZE, MAX_W, W, H = 92, 840, 1080, 1920
MAX_W_LANDSCAPE = 1300   # 16:9 成片的折行宽度；字号不变（按短边 1080 设计）


def _sync_frame(size=None):
    """按实际画面设成片宽高与折行宽：size=(宽,高) 取自要叠字幕的视频/样帧，缺省用 common.FRAME。"""
    global MAX_W, W, H
    from common import frame, set_frame
    W, H = set_frame(*size) if size else frame()
    MAX_W = MAX_W_LANDSCAPE if W > H else 840
PX, PY, BORDER, SHADOW, RAD = 46, 34, 7, 12, 30
ENTER, WIPE, FADE = 0.30, 0.22, 0.12


def _font(size, wght=800):
    f = ImageFont.truetype(FONT, size)
    f.set_variation_by_axes([14, wght, 100])
    return f


def _tokens(text):
    """'Tilt it? *The toppings slide.*' → [(词, 是否关键词)]"""
    out = []
    for part in re.split(r"(\*[^*]+\*)", text):
        hl = part.startswith("*")
        out += [(w, hl) for w in part.strip("*").split()]
    return out


def build(c, size=SIZE):
    """排好一张卡：返回 dict(card=无荧光的卡片底, text=文字层, marks=[荧光笔矩形], 尺寸/锚点)。"""
    f = _font(size)
    sp = f.getlength(" ")
    lines, cur, cw = [], [], 0
    for w, hl in _tokens(c["text"]):
        ww = f.getlength(w)
        if cur and cw + sp + ww > MAX_W:
            lines.append(cur); cur, cw = [], 0
        cur.append((w, hl, ww)); cw += (sp if len(cur) > 1 else 0) + ww
    lines.append(cur)
    lh = int(size * 1.18)
    asc = -f.getbbox("H", anchor="ls")[1]
    tw = max(sum(ww for _, _, ww in ln) + sp * (len(ln) - 1) for ln in lines)
    bw, bh = int(tw + 2 * PX), int(lh * len(lines) - (lh - asc) + 2 * PY + size * 0.12)
    kick = c.get("kicker")
    top = 0
    kf = None
    if kick:
        kf = _font(50, 800)
        top = 58                                   # 标签压在卡片上沿，向上探出
    pad = 20
    cw_, ch_ = bw + SHADOW + 2 * pad, bh + SHADOW + top + 2 * pad
    card = Image.new("RGBA", (cw_, ch_), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    x0, y0 = pad, pad + top
    d.rounded_rectangle((x0 + SHADOW, y0 + SHADOW, x0 + bw + SHADOW, y0 + bh + SHADOW), RAD, fill=INK + (255,))
    d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), RAD, fill=CREAM + (255,), outline=INK + (255,), width=BORDER)
    # 小票齿孔：卡片右沿一排墨点，和片尾/网站的贴纸边呼应
    for k in range(3):
        cy = y0 + bh * (k + 1) / 4
        d.ellipse((x0 + bw - 22, cy - 5, x0 + bw - 12, cy + 5), fill=INK + (255,))
    text = Image.new("RGBA", card.size, (0, 0, 0, 0))
    dt = ImageDraw.Draw(text)
    marks = []
    for i, ln in enumerate(lines):
        x = x0 + PX
        base = y0 + PY + asc + i * lh
        for j, (w, hl, ww) in enumerate(ln):
            if hl:
                ext = sp if j + 1 < len(ln) and ln[j + 1][1] else 0      # 连续关键词之间的空格也划上
                marks.append((x - 8, base - asc * 0.62, x + ww + ext + 8, base + size * 0.10))
            dt.text((x, base), w, font=f, fill=INK + (255,), anchor="ls")
            x += ww + sp
    tag = None
    if kick:
        l, t, r, b = kf.getbbox(kick, anchor="ls")
        tw_, th_ = int(r - l) + 40, int(-t) + 30
        tag = Image.new("RGBA", (tw_ + 10, th_ + 10), (0, 0, 0, 0))
        dg = ImageDraw.Draw(tag)
        dg.rounded_rectangle((6, 6, tw_ + 6, th_ + 6), 12, fill=INK + (255,))
        dg.rounded_rectangle((0, 0, tw_, th_), 12, fill=YELLOW + (255,), outline=INK + (255,), width=5)
        dg.text((20 - l, 15 - t), kick, font=kf, fill=INK + (255,), anchor="ls")
        tag = tag.rotate(4, resample=Image.BICUBIC, expand=True)
        card.alpha_composite(tag, (x0 + 26, pad - 6))
    return dict(card=card, text=text, marks=marks, w=cw_, h=ch_, box=(x0, y0, bw, bh))


def _back(u, c=1.7):
    u -= 1
    return 1 + (c + 1) * u ** 3 + c * u ** 2


def _frame_card(L, k):
    """荧光笔进度 k（0..len(marks)）时的整卡图。"""
    im = L["card"].copy()
    if k > 0 and L["marks"]:
        hl = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(hl)
        for i, (a, b, c, e) in enumerate(L["marks"]):
            p = max(0.0, min(1.0, k - i))
            x1 = a + (c - a) * p
            if x1 - a >= 2:
                d.rounded_rectangle((a, b, x1, e), min(8, (x1 - a) / 2 - 1), fill=YELLOW + (255,))
        im.alpha_composite(hl)
    im.alpha_composite(L["text"])
    return im


def render_frame(caps, t, canvas):
    for c in caps:
        if not (c["t0"] <= t < c["t1"]):
            continue
        L = c["_lay"]
        lt = t - c["t0"]
        ang = c.get("_ang", 2.5)
        u = min(1.0, lt / ENTER)
        if c.get("fx") == "stamp":
            v = min(1.0, lt / 0.16)
            s = 1 + 0.35 * (1 - v) ** 2
            dy, rot, a = 0, ang * (1 + 1.5 * (1 - v)), min(1.0, lt / 0.06)
        else:
            e = _back(u)
            s = 0.72 + 0.28 * e
            dy, rot, a = 90 * (1 - e), ang + 7 * (1 - e), min(1.0, lt / 0.1)
        dx = 0
        if c.get("fx") in ("shake", "stamp"):
            k = lt - (0.16 if c.get("fx") == "stamp" else ENTER)
            if 0 < k < 0.4:
                dx = (16 if c.get("fx") == "shake" else 9) * math.sin(k * 65) * (1 - k / 0.4)
        out = max(0.0, (c["t1"] - t))
        if out < FADE:
            a *= out / FADE; dy -= 30 * (1 - out / FADE)
        k = max(0.0, (lt - (0.18 if c.get("fx") == "stamp" else ENTER - 0.06)) / WIPE)
        im = _frame_card(L, k)
        if s != 1:
            im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BICUBIC)
        im = im.rotate(rot, resample=Image.BICUBIC, expand=True)
        if a < 1:
            im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
        cx = c.get("x", W / 2) + dx
        canvas.alpha_composite(im, (int(cx - im.width / 2), int(c["y"] + dy - im.height / 2)))


def _prep(captions):
    for i, c in enumerate(captions):
        c["_lay"] = build(c)
        c["_ang"] = c.get("angle", 2.5 if i % 2 == 0 else -2.5)


def burn_ticket_captions(work, captions, src="cut_raw.mov", dst="cut_sub.mov"):
    from common import probe
    v = probe(os.path.join(work, src))
    _sync_frame((v["width"], v["height"]))
    _prep(captions)
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
        c.pop("_lay", None); c.pop("_ang", None)
    return os.path.join(work, dst)


def preview(captions, frames_at, out):
    """出样帧：frames_at 是 [(成片时间, 背景图路径)]，拼成一张横图。"""
    _sync_frame(Image.open(frames_at[0][1]).size if frames_at else None)
    _prep(captions)
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
        c.pop("_lay", None); c.pop("_ang", None)
