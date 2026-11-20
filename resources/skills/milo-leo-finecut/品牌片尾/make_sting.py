"""Render the PUGGY ROOM end sting (2.0s, 1080x1920, 30fps) from the Figma final frame,
and the long-form 16:9 end screen (10.0s, 1920x1080, 30fps).

Figma: 网站 file, page "PUGGY ROOM · Shop", section "PR/Video/End sting · 20261028" (283:586),
final frame 283:587. src/ref_final_frame.png is that frame exported at 1x; layers are cut from it.
16:9: section "PR/Video/End screen 16:9 · long-form · 20261101" (300:586), final frame 300:587 →
src/ref_endscreen_16x9.png. It is a YouTube end screen, not a resized sting: long videos need 5–20s and
two video slots + a subscribe circle where YouTube Studio places its clickable elements.

    python3 make_sting.py                  # 9:16 sting: tote, mug, tee
    python3 make_sting.py tote             # one product
    python3 make_sting.py --landscape      # 16:9 end screen: tote, mug, tee
"""
import subprocess, sys, wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "finecut"))
import paths  # activate the bundled media tools
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SRC, OUT = HERE / "src", HERE / "out"
W, H, FPS, N = 1080, 1920, 30, 60          # 2.0s
SR = 48000
CREAM = (255, 248, 232)

# Final-frame geometry, copied from the Figma frame 283:587.
LOCKUP = (340, 280, 740, 400)              # crop box around lockup 359,290 361x100
HEADLINE = (60, 410, 1020, 650)            # crop box around headline 90,420 900x208
CIRCLE = (240, 700, 600)                   # x, y, diameter
MILO = (323, 650, 434, 640)                # x, y, w, h
CHIP = (248, 1306, 584, 180, 36)           # x, y, w, h, radius
PRODUCT = (16, 16, 132, 148, 22)           # inside chip
# Merch mockups exported 1x from the 品牌产品设计 file (canvases 4:104 / 4:151 / 4:222);
# the region picks one item out of each mockup.
PRODUCT_SRC = {"tote": ("tote.png", (0, 0, 792, 720)),
               "mug": ("mug.png", (50, 260, 390, 560)),          # side A / MILO
               "tee": ("tee.png", (400, 130, 780, 550))}         # back / the big idea


def ease_out_back(t, s=1.70158):
    t = min(max(t, 0.0), 1.0) - 1
    return t * t * ((s + 1) * t + s) + 1


def ease_out_cubic(t):
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def prog(f, f0, f1):
    return (f - f0) / (f1 - f0)


def color_to_alpha(im, bg=CREAM):
    """Un-blend a layer exported on the cream background so it composites cleanly over other layers."""
    c = np.asarray(im.convert("RGB"), dtype=np.float32)
    b = np.array(bg, dtype=np.float32)
    up = np.where(c >= b, (c - b) / np.maximum(255 - b, 1), (b - c) / np.maximum(b, 1))
    a = up.max(axis=2, keepdims=True)
    fg = np.where(a > 0, (c - (1 - a) * b) / np.maximum(a, 1e-6), 0)
    out = np.concatenate([np.clip(fg, 0, 255), a * 255], axis=2).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def rounded_mask(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), r, fill=255)
    return m


def product_thumb(product, size):
    """Crop one item from its mockup, pad to the slot's aspect on the mockup grey."""
    name, region = PRODUCT_SRC[product]
    im = Image.open(SRC / name).convert("RGB")
    bg = im.getpixel((5, 5))
    sub = im.crop(region)
    diff = np.abs(np.asarray(sub, dtype=np.int16) - np.array(bg)).max(axis=2) > 14
    ys, xs = np.nonzero(diff)
    x0, x1, y0, y1 = xs.min() + region[0], xs.max() + 1 + region[0], ys.min() + region[1], ys.max() + 1 + region[1]
    aspect = size[0] / size[1]
    h = max(y1 - y0, (x1 - x0) / aspect) * 1.1
    w = h * aspect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    box = tuple(round(v) for v in (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    canvas = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), bg)
    clip = (max(box[0], 0), max(box[1], 0), min(box[2], im.width), min(box[3], im.height))
    canvas.paste(im.crop(clip), (clip[0] - box[0], clip[1] - box[1]))
    return canvas.resize(size, Image.LANCZOS)


def load_layers(product):
    ref = Image.open(SRC / "ref_final_frame.png").convert("RGB")
    lockup = color_to_alpha(ref.crop(LOCKUP))
    headline = color_to_alpha(ref.crop(HEADLINE))

    x, y, w, h, r = CHIP
    chip = ref.crop((x, y, x + w, y + h)).convert("RGBA")
    if product != "tote":                  # Figma frame holds the tote; swap the thumbnail
        px, py, pw, ph, pr = PRODUCT
        chip.paste(product_thumb(product, (pw, ph)), (px, py), rounded_mask((pw, ph), pr))
    chip.putalpha(rounded_mask((w, h), r))

    milo = Image.open(SRC / "milo_cut.png").convert("RGBA").resize(MILO[2:], Image.LANCZOS)
    return lockup, headline, chip, milo


def paste_scaled(canvas, layer, box_xy, scale, anchor, alpha=1.0, dy=0):
    """Paste layer at its final position, scaled about anchor (fractions of layer size)."""
    if scale <= 0.01 or alpha <= 0.01:
        return
    w, h = layer.size
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    im = layer if (sw, sh) == (w, h) else layer.resize((sw, sh), Image.BICUBIC)
    if alpha < 1:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
    ax, ay = box_xy[0] + anchor[0] * w, box_xy[1] + anchor[1] * h
    canvas.alpha_composite(im, (round(ax - anchor[0] * sw), round(ay - anchor[1] * sh + dy)))


def circle_layer(d):
    ss = 4
    im = Image.new("RGBA", (d * ss, d * ss), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((0, 0, d * ss - 1, d * ss - 1), fill=(255, 199, 49, 255))
    return im.resize((d, d), Image.LANCZOS)


def frame(f, layers, circle):
    lockup, headline, chip, milo = layers
    cv = Image.new("RGBA", (W, H), CREAM + (255,))
    cx, cy, d = CIRCLE
    # starts at -3 so the hard-cut frame 0 already shows a third of the circle, never a blank card
    paste_scaled(cv, circle, (cx, cy), ease_out_back(prog(f, -3, 9), 2.2), (0.5, 0.5))
    t = prog(f, 9, 15)
    paste_scaled(cv, headline, HEADLINE[:2], 1, (0.5, 0.5), ease_out_cubic(t), dy=40 * (1 - ease_out_cubic(t)))
    t = prog(f, 8, 14)
    paste_scaled(cv, lockup, LOCKUP[:2], 1, (0.5, 0.5), ease_out_cubic(t), dy=-30 * (1 - ease_out_cubic(t)))
    t = prog(f, 3, 14)
    bob = 5 * np.sin(2 * np.pi * max(f - 14, 0) / 36) if f > 14 else 0
    paste_scaled(cv, milo, MILO[:2], 0.55 + 0.45 * ease_out_back(t, 2.0), (0.5, 1.0),
                 min(1, prog(f, 3, 6)), dy=140 * (1 - ease_out_cubic(t)) - bob)
    t = prog(f, 16, 25)
    paste_scaled(cv, chip, CHIP[:2], 0.8 + 0.2 * ease_out_back(t, 2.4), (0.5, 0.5), ease_out_cubic(prog(f, 16, 20)))
    return cv.convert("RGB")


def jingle(path):
    """Sonic logo: three rising mallet notes with the pops, a soft C-major landing on the chip."""
    n = int(SR * N / FPS)
    t = np.arange(n) / SR
    out = np.zeros(n)

    def mallet(start, f0, amp, decay):
        i = int(start * SR)
        tt = t[: n - i]
        env = np.exp(-tt / decay) * np.minimum(tt / 0.003, 1)
        out[i:] += amp * env * (np.sin(2 * np.pi * f0 * tt) + 0.25 * np.sin(2 * np.pi * 4 * f0 * tt) * np.exp(-tt / 0.04))

    C6, E6, G6, C7 = 1046.5, 1318.5, 1568.0, 2093.0
    mallet(0 / FPS, C6, 0.55, 0.22)
    mallet(4 / FPS, E6, 0.50, 0.22)
    mallet(8 / FPS, G6, 0.50, 0.25)
    for f0, a in ((C6, 0.30), (E6, 0.26), (G6, 0.24), (C7, 0.20)):
        mallet(16 / FPS, f0, a, 0.55)
    fade = np.ones(n)
    k = int(0.12 * SR)
    fade[-k:] = np.linspace(1, 0, k)
    out *= fade
    # peak -9 dBFS ≈ -18 LUFS: a soft tag under episodes that sit near -14 LUFS
    out *= 10 ** (-9 / 20) / np.abs(out).max()
    pcm = (np.repeat(out[:, None], 2, axis=1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def render(product):
    OUT.mkdir(exist_ok=True)
    layers = load_layers(product)
    circle = circle_layer(CIRCLE[2])
    wav = OUT / "jingle.wav"
    jingle(wav)
    dst = OUT / f"PuggyRoom_EndSting_{product}.mp4"
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", str(wav),
         "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-profile:v", "high",
         "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2",
         "-frames:v", str(N), "-movflags", "+faststart", str(dst)],
        stdin=subprocess.PIPE)
    for f in range(N):
        ff.stdin.write(frame(f, layers, circle).tobytes())
    ff.stdin.close()
    if ff.wait():
        sys.exit(f"ffmpeg failed for {product}")
    if product == "tote":                  # final-frame check against the Figma export
        last = np.asarray(frame(N - 1, layers, circle), dtype=np.int16)
        ref = np.asarray(Image.open(SRC / "ref_final_frame.png").convert("RGB"), dtype=np.int16)
        print(f"final frame vs Figma: mean abs diff {np.abs(last - ref).mean():.2f}")
    print(dst)


# ---------------------------------------------------------------- 16:9 long-form end screen
LW, LH, LN = 1920, 1080, 300               # 10.0s
# Layer boxes (x, y, w, h) copied from Figma frame 300:587; everything sits on cream, nothing overlaps.
L_SLOT1 = (120, 300, 800, 450)             # sage slots: YouTube Video elements go here
L_SLOT2 = (1000, 300, 800, 450)
L_HEAD = (110, 70, 800, 110)               # "Another little MILO?"
L_SUB = (116, 196, 360, 52)                # "New stories every day."
L_LOCK = (1500, 86, 310, 100)
L_SUBSCRIBE = (108, 808, 224, 224)         # yellow ring + MILO avatar: YouTube Subscribe element
L_LABEL = (346, 872, 400, 100)
L_CHIP = (900, 830, 596, 180, 36)
L_CHIP_PRODUCT = (16, 16, 132, 148, 22)    # inside the chip, same as the 9:16 chip
L_MILO = (1520, 760, 380, 320)             # peeking MILO, cut off by the frame bottom


def load_landscape(product):
    ref = Image.open(SRC / "ref_endscreen_16x9.png").convert("RGB")
    cut = lambda b: color_to_alpha(ref.crop((b[0], b[1], b[0] + b[2], b[1] + b[3])))
    x, y, w, h, r = L_CHIP
    chip = ref.crop((x, y, x + w, y + h)).convert("RGBA")
    if product != "tote":                  # Figma frame holds the tote; swap the thumbnail
        px, py, pw, ph, pr = L_CHIP_PRODUCT
        chip.paste(product_thumb(product, (pw, ph)), (px, py), rounded_mask((pw, ph), pr))
    chip.putalpha(rounded_mask((w, h), r))
    return dict(slot1=cut(L_SLOT1), slot2=cut(L_SLOT2), head=cut(L_HEAD), sub=cut(L_SUB), lock=cut(L_LOCK),
                subscribe=cut(L_SUBSCRIBE), label=cut(L_LABEL), chip=chip, milo=cut(L_MILO))


def frame_landscape(f, ly):
    """0.0–1.0s everything builds in (slots first, so frame 0 after the hard cut is never a blank card),
    then a calm 9s hold with MILO breathing — the time YouTube's end-screen cards sit on the slots."""
    cv = Image.new("RGBA", (LW, LH), CREAM + (255,))
    for key, box, f0 in (("slot1", L_SLOT1, -4), ("slot2", L_SLOT2, 0)):
        t = prog(f, f0, f0 + 12)
        paste_scaled(cv, ly[key], box[:2], 0.86 + 0.14 * ease_out_back(t, 1.6), (0.5, 0.5), ease_out_cubic(prog(f, f0, f0 + 6)))
    t = ease_out_cubic(prog(f, 3, 12))
    paste_scaled(cv, ly["head"], L_HEAD[:2], 1, (0, 0.5), t, dy=36 * (1 - t))
    t = ease_out_cubic(prog(f, 8, 16))
    paste_scaled(cv, ly["sub"], L_SUB[:2], 1, (0, 0.5), t, dy=24 * (1 - t))
    t = ease_out_cubic(prog(f, 7, 15))
    paste_scaled(cv, ly["lock"], L_LOCK[:2], 1, (1, 0.5), t, dy=-24 * (1 - t))
    paste_scaled(cv, ly["subscribe"], L_SUBSCRIBE[:2], ease_out_back(prog(f, 12, 22), 2.2), (0.5, 0.5))
    t = ease_out_cubic(prog(f, 16, 24))
    paste_scaled(cv, ly["label"], L_LABEL[:2], 1, (0, 0.5), t, dy=18 * (1 - t))
    t = prog(f, 20, 30)
    paste_scaled(cv, ly["chip"], L_CHIP[:2], 0.8 + 0.2 * ease_out_back(t, 2.4), (0.5, 0.5), ease_out_cubic(prog(f, 20, 25)))
    t = prog(f, 14, 28)
    bob = 5 * np.sin(2 * np.pi * (f - 28) / 72) if f > 28 else 0
    paste_scaled(cv, ly["milo"], L_MILO[:2], 1, (0.5, 1.0), min(1, prog(f, 14, 17)),
                 dy=260 * (1 - ease_out_back(t, 1.4)) - bob)
    return cv.convert("RGB")


def jingle_long(path):
    """Same sonic logo as the sting (three rising notes, chord on the merch chip), then a soft mallet bed
    (C–G–E–G at 100 BPM, ~12 dB under the logo) so the 9s hold is not dead air; fades over the last 1.5s."""
    n = int(SR * LN / FPS)
    t = np.arange(n) / SR
    out = np.zeros(n)

    def mallet(start, f0, amp, decay):
        i = int(start * SR)
        if i >= n:
            return
        tt = t[: n - i]
        env = np.exp(-tt / decay) * np.minimum(tt / 0.003, 1)
        out[i:] += amp * env * (np.sin(2 * np.pi * f0 * tt) + 0.25 * np.sin(2 * np.pi * 4 * f0 * tt) * np.exp(-tt / 0.04))

    C5, E5, G5, C6, E6, G6, C7 = 523.25, 659.25, 783.99, 1046.5, 1318.5, 1568.0, 2093.0
    mallet(0 / FPS, C6, 0.55, 0.22)
    mallet(4 / FPS, E6, 0.50, 0.22)
    mallet(8 / FPS, G6, 0.50, 0.25)
    for f0, a in ((C6, 0.30), (E6, 0.26), (G6, 0.24), (C7, 0.20)):
        mallet(20 / FPS, f0, a, 0.55)
    beat = 60 / 100
    k = 0
    while 1.6 + k * beat < LN / FPS:
        mallet(1.6 + k * beat, (C5, G5, E5, G5)[k % 4], 0.13 if k % 4 == 0 else 0.09, 0.35)
        k += 1
    fade = np.ones(n)
    k = int(1.5 * SR)
    fade[-k:] = np.linspace(1, 0, k)
    out *= fade
    out *= 10 ** (-9 / 20) / np.abs(out).max()
    pcm = (np.repeat(out[:, None], 2, axis=1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def render_landscape(product):
    OUT.mkdir(exist_ok=True)
    ly = load_landscape(product)
    wav = OUT / "jingle_16x9.wav"
    jingle_long(wav)
    dst = OUT / f"PuggyRoom_EndScreen16x9_{product}.mp4"
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{LW}x{LH}", "-r", str(FPS), "-i", "-",
         "-i", str(wav),
         "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-profile:v", "high",
         "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2",
         "-frames:v", str(LN), "-movflags", "+faststart", str(dst)],
        stdin=subprocess.PIPE)
    for f in range(LN):
        ff.stdin.write(frame_landscape(f, ly).tobytes())
    ff.stdin.close()
    if ff.wait():
        sys.exit(f"ffmpeg failed for {product}")
    if product == "tote":                  # a settled frame (bob at zero crossing) against the Figma export
        last = np.asarray(frame_landscape(28 + 72, ly), dtype=np.int16)
        ref = np.asarray(Image.open(SRC / "ref_endscreen_16x9.png").convert("RGB"), dtype=np.int16)
        print(f"settled frame vs Figma: mean abs diff {np.abs(last - ref).mean():.2f}")
    print(dst)


if __name__ == "__main__":
    args = sys.argv[1:]
    landscape = "--landscape" in args
    for p in [a for a in args if not a.startswith("--")] or ["tote", "mug", "tee"]:
        render_landscape(p) if landscape else render(p)
