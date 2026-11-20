# 粗剪体检：镜头切点、闪帧/短镜头、黑帧、糊帧、疑似重复镜头，外加每镜头联系表
# 用法：python3 analyze.py <粗剪.mp4> <输出目录> [--draft <CapCut粗剪草稿目录>]
import argparse, json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from common import FPS, read_frames, probe
from source_versions import fingerprint, assert_fingerprint
from draft_association import associate

from paths import REPORT_FONT as FONT
AW, AH = 90, 160          # 切点/重复分析分辨率
SW, SH = 270, 480         # 清晰度分析分辨率


def hsv_hist(rgb):
    """简化颜色直方图（每通道 8 档，三通道拼接），归一化。"""
    h = [np.histogram(rgb[..., c], bins=8, range=(0, 256))[0] for c in range(3)]
    h = np.concatenate(h).astype(np.float32)
    return h / h.sum()


def detect_cuts(rgb):
    g = rgb.mean(axis=3)
    d = np.zeros(len(g))
    d[1:] = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    hists = np.array([hsv_hist(f) for f in rgb])
    hd = np.zeros(len(g))
    hd[1:] = 0.5 * np.abs(np.diff(hists, axis=0)).sum(axis=1)
    # 自适应阈值：局部中位数的倍数 + 绝对下限
    cuts = []
    for i in range(1, len(g)):
        lo, hi = max(1, i - 15), min(len(g), i + 16)
        med = np.median(d[lo:hi])
        if d[i] > max(25.0, 4 * med) or (d[i] > max(14.0, 3.5 * med) and hd[i] > 0.12) or hd[i] > 0.45:
            if not cuts or i - cuts[-1] > 1:
                cuts.append(i)
    return cuts, d, hd, hists


def single_frame_flashes(rgb):
    """第 i 帧与前后都差很多，但前后彼此接近 → 单帧闪。"""
    g = rgb.mean(axis=3)
    out = []
    for i in range(1, len(g) - 1):
        a = np.abs(g[i] - g[i - 1]).mean()
        b = np.abs(g[i] - g[i + 1]).mean()
        c = np.abs(g[i - 1] - g[i + 1]).mean()
        if a > 18 and b > 18 and c < 0.4 * min(a, b):
            out.append(i)
    return out


def sharpness(gray):
    f = gray.astype(np.float32)
    lap = (-4 * f[1:-1, 1:-1] + f[:-2, 1:-1] + f[2:, 1:-1] + f[1:-1, :-2] + f[1:-1, 2:])
    return float(lap.var())


def shot_signature(rgb, a, b):
    idx = [a + int((b - a) * q) for q in (0.2, 0.5, 0.8)]
    thumbs = [rgb[i].mean(axis=2)[::4, ::4].astype(np.float32) for i in idx]
    thumbs = [(t - t.mean()) / (t.std() + 1e-6) for t in thumbs]
    return idx, thumbs, [hsv_hist(rgb[i]) for i in idx]


def similarity(sa, sb):
    """两镜头最像的一对代表帧：构图相关系数 × 颜色直方图交集。"""
    best = 0.0
    for ta, ha in zip(sa[1], sa[2]):
        for tb, hb in zip(sb[1], sb[2]):
            corr = float((ta * tb).mean())
            inter = float(np.minimum(ha, hb).sum())
            best = max(best, max(corr, 0) * inter)
    return best


def repeated_stretches(path, fps=6, sim_thr=0.88, min_len=1.5, min_motion=0.08):
    """镜头内重复/拖沓：时间上错开 ≥2s 的两段，逐帧高度相似且段内画面确实在动
    （同一动作演了两遍，或长时间几乎不变的慢段）。返回合并后的区间。"""
    rgb = read_frames(path, 48, 84, fps=fps)
    g = rgb.mean(axis=3).astype(np.float32).reshape(len(rgb), -1)
    v = (g - g.mean(1, keepdims=True)) / (g.std(1, keepdims=True) + 1e-6)
    S = (v @ v.T) / v.shape[1]
    n = len(S)
    runs = []
    for L in range(int(2 * fps), n):
        ok = np.diagonal(S, L) > sim_thr
        i = 0
        while i < len(ok):
            if not ok[i]:
                i += 1; continue
            j = i
            while j < len(ok) and ok[j]:
                j += 1
            if j - i >= int(min_len * fps) and 1 - S[i, j - 1] > min_motion:
                runs.append((i / fps, (j + L) / fps, L / fps, (j - i) / fps))
            i = j
    runs.sort()
    merged = []
    for a, b, lag, ln in runs:
        if merged and a <= merged[-1]["t1"]:
            m = merged[-1]; m["t1"] = max(m["t1"], b)
            if ln > m["len"]:
                m.update(lag=lag, len=ln)
        else:
            merged.append(dict(t0=a, t1=b, lag=lag, len=ln))
    return [m for m in merged if m["t1"] - m["t0"] >= 3.5]


def audio_clicks(path, sr=48000):
    """孤立爆音：<200ms 的突发声，峰值 > -3dBFS（或削波），前后 300ms 近乎静音。"""
    from common import read_audio
    y = np.abs(read_audio(path, sr))
    h = int(sr * 0.01)
    e = y[: len(y) // h * h].reshape(-1, h).max(axis=1)        # 每 10ms 峰值
    loud = e > 10 ** (-3 / 20)
    out = []
    for i in np.flatnonzero(loud):
        if out and i * 0.01 - out[-1]["t1"] < 0.05:
            out[-1]["t1"] = (i + 1) * 0.01; continue
        j = i
        while j < len(e) and e[j] > 0.02:
            j += 1
        k = i
        while k > 0 and e[k - 1] > 0.02:
            k -= 1
        quiet = lambda a, b: e[max(0, a):max(0, b)].max(initial=0) < 0.02
        if (j - k) * 0.01 < 0.2 and quiet(k - 30, k) and quiet(j, j + 30):
            out.append(dict(t0=round(k * 0.01, 2), t1=round(j * 0.01, 2), peak_db=round(float(20 * np.log10(e[i:j].max())), 1)))
    return out


def _thumb_h(path, tw):
    """联系表缩略图高度：按粗剪实际宽高比（竖 9:16、横 16:9），取偶数。"""
    v = probe(path)
    return round(tw * v["height"] / v["width"] / 2) * 2


def contact_sheets(path, shots, out_dir, per_sheet=8, tw=180):
    font = ImageFont.truetype(FONT, 22)
    th = _thumb_h(path, tw)
    rgb = read_frames(path, tw, th)
    files = []
    for k in range(0, len(shots), per_sheet):
        chunk = shots[k:k + per_sheet]
        sheet = Image.new("RGB", (tw * 3 + 230, (th + 8) * len(chunk)), (24, 24, 24))
        dr = ImageDraw.Draw(sheet)
        for r, s in enumerate(chunk):
            a, b = s["f0"], s["f1"]
            for c, i in enumerate([a + 1, (a + b) // 2, max(a + 1, b - 2)]):
                i = min(i, len(rgb) - 1)
                sheet.paste(Image.fromarray(rgb[i]), (230 + c * tw, r * (th + 8)))
            dr.text((10, r * (th + 8) + 10), f"#{s['id']:02d}", fill=(255, 210, 80), font=font)
            dr.text((10, r * (th + 8) + 44), f"{s['t0']:.2f}–{s['t1']:.2f}", fill="white", font=font)
            dr.text((10, r * (th + 8) + 78), f"{s['t1'] - s['t0']:.2f}s", fill=(170, 170, 170), font=font)
        fn = os.path.join(out_dir, f"shots_{k // per_sheet + 1:02d}.jpg")
        sheet.save(fn, quality=85)
        files.append(fn)
    return files


def pair_image(path, shots, pairs, out_dir, tw=200):
    if not pairs:
        return None
    font = ImageFont.truetype(FONT, 22)
    th = _thumb_h(path, tw)
    rgb = read_frames(path, tw, th)
    img = Image.new("RGB", (tw * 4 + 40, (th + 40) * len(pairs)), (24, 24, 24))
    dr = ImageDraw.Draw(img)
    for r, p in enumerate(pairs):
        for c, sid in enumerate((p["a"], p["b"])):
            s = shots[sid]
            for q, i in enumerate([s["f0"] + (s["f1"] - s["f0"]) // 3, s["f0"] + 2 * (s["f1"] - s["f0"]) // 3]):
                img.paste(Image.fromarray(rgb[min(i, len(rgb) - 1)]), (c * (2 * tw + 40) + q * tw, r * (th + 40) + 36))
        dr.text((8, r * (th + 40) + 4), f"#{p['a']:02d} {shots[p['a']]['t0']:.1f}s  vs  #{p['b']:02d} "
                f"{shots[p['b']]['t0']:.1f}s   相似度 {p['score']:.2f}", fill=(255, 210, 80), font=font)
    fn = os.path.join(out_dir, "dup_pairs.jpg")
    img.save(fn, quality=85)
    return fn


def analyze(path, out_dir, draft=None, dup_threshold=0.55, draft_verified=False, draft_verification_note=""):
    source_version = fingerprint(path)
    info = probe(path)
    gen, draft_check = associate(path, info["duration"], draft, draft_verified, draft_verification_note)
    os.makedirs(out_dir, exist_ok=True)
    rgb = read_frames(path, AW, AH)
    n = len(rgb)
    cuts, d, hd, _ = detect_cuts(rgb)
    if gen:  # 仅人工核对关联后的主轨边界用于辅助分段
        forced = [round(s["rough_start"] * FPS) for s in gen[1:]]
        cuts = sorted(set(cuts) | {f for f in forced if 0 < f < n and all(abs(f - c) > 2 for c in cuts)})
    bounds = [0] + cuts + [n]
    shots = [dict(id=i, f0=a, f1=b, t0=a / FPS, t1=b / FPS) for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]

    gray = read_frames(path, SW, SH, gray=True)
    sharp = np.array([sharpness(f) for f in gray[:n]])
    luma = rgb.mean(axis=(1, 2, 3))

    issues = []
    for s in shots:
        if s["f1"] - s["f0"] <= 5:
            issues.append(dict(kind="短镜头/闪帧", t0=s["t0"], t1=s["t1"], note=f"#{s['id']} 仅 {s['f1'] - s['f0']} 帧"))
    for i in single_frame_flashes(rgb):
        issues.append(dict(kind="单帧闪", t0=i / FPS, t1=(i + 1) / FPS, note=f"第 {i} 帧"))
    blk = np.where(luma < 18)[0]
    for run_ in np.split(blk, np.where(np.diff(blk) > 1)[0] + 1) if len(blk) else []:
        issues.append(dict(kind="黑帧", t0=run_[0] / FPS, t1=(run_[-1] + 1) / FPS, note=f"{len(run_)} 帧"))
    for s in shots:
        seg = sharp[s["f0"]:s["f1"]]
        if len(seg) < 6:
            continue
        med = np.median(seg)
        bad = np.where(seg < 0.4 * med)[0]
        if len(bad) >= 3:
            for run_ in np.split(bad, np.where(np.diff(bad) > 2)[0] + 1):
                if len(run_) >= 3:
                    issues.append(dict(kind="疑似糊帧", t0=(s["f0"] + run_[0]) / FPS, t1=(s["f0"] + run_[-1] + 1) / FPS,
                                       note=f"#{s['id']} 清晰度降到镜头中位数的 {seg[run_].mean() / med:.0%}"))
        s["sharpness"] = round(float(med), 1)

    sigs = [shot_signature(rgb, s["f0"], s["f1"]) if s["f1"] - s["f0"] > 3 else None for s in shots]
    pairs = []
    for i in range(len(shots)):
        for j in range(i + 1, len(shots)):
            if sigs[i] is None or sigs[j] is None:
                continue
            sc = similarity(sigs[i], sigs[j])
            if sc >= dup_threshold:
                pairs.append(dict(a=i, b=j, score=round(sc, 3), adjacent=(j == i + 1)))
    pairs.sort(key=lambda p: -p["score"])
    for c in audio_clicks(path):
        issues.append(dict(kind="疑似爆音", t0=c["t0"], t1=c["t1"],
                           note=f"孤立突发声，峰值 {c['peak_db']} dBFS（>0 即削波），前后近乎静音；可能是故意的短音效，需判断"))
    for m in repeated_stretches(path):
        issues.append(dict(kind="疑似重复/拖沓段", t0=m["t0"], t1=m["t1"],
                           note=f"画面隔 {m['lag']:.1f}s 几乎重演（连续 {m['len']:.1f}s 相似）：同一动作拍了两遍，或长时间没进展"))

    assert_fingerprint(path, source_version)
    if draft_check.get("boundaries_used"):
        assert_fingerprint(os.path.join(draft_check["path"], "draft_info.json"), draft_check["draft_fingerprint"])
    res = dict(source=path, source_fingerprint=source_version, draft_association=draft_check,
               info=info, frames=n, shots=shots, issues=sorted(issues, key=lambda x: x["t0"]),
               dup_pairs=pairs, generated_segments=gen)
    res["contact_sheets"] = contact_sheets(path, shots, out_dir)
    res["dup_image"] = pair_image(path, shots, [p for p in pairs if not p["adjacent"]][:8], out_dir)
    json.dump(res, open(os.path.join(out_dir, "analysis.json"), "w"), ensure_ascii=False, indent=1)
    write_report(res, os.path.join(out_dir, "体检报告.md"))
    assert_fingerprint(path, source_version)
    return res


def write_report(r, fn):
    L = [f"# 粗剪体检：{os.path.basename(r['source'])}", "",
         f"- 时长 {r['info']['duration']:.2f}s，{r['frames']} 帧，检测到 {len(r['shots'])} 个镜头", ""]
    version = r.get("source_fingerprint")
    if version:
        L += [f"- 当前粗剪版本：SHA-256 {version['sha256']}，{version['size_bytes']}字节", ""]
    association = r.get("draft_association", {})
    L += ["## 草稿关联", f"- 状态：{association.get('status', 'not_associated')}；是否使用草稿边界：{association.get('boundaries_used', False)}"]
    if association.get("verification_note"):
        L += ["- 人工核对依据：" + association["verification_note"]]
    for candidate in association.get("candidates", []):
        L += ["- 候选：" + candidate["path"] + "（同名/同长不是内容对应证明）"]
    L.append("")
    if r["generated_segments"]:
        L += ["## 生成段（来自人工核对关联的 CapCut 粗剪草稿）", "", "|粗剪区间|原始文件|文件起点|", "|---|---|---|"]
        L += [f"|{s['rough_start']:.2f}–{s['rough_end']:.2f}|{os.path.basename(s['file'])}|{s['file_start']:.2f}|"
              for s in r["generated_segments"]]
        L.append("")
    L += ["## 镜头表", "", "|#|区间(秒)|时长|清晰度|", "|---|---|---|---|"]
    L += [f"|{s['id']}|{s['t0']:.2f}–{s['t1']:.2f}|{s['t1'] - s['t0']:.2f}|{s.get('sharpness', '')}|" for s in r["shots"]]
    L += ["", "## 问题帧", ""]
    L += [f"- **{x['kind']}** {x['t0']:.2f}–{x['t1']:.2f}s：{x['note']}" for x in r["issues"]] or ["- 无"]
    L += ["", "## 疑似重复镜头（构图+配色相似；需看图确认是不是同一动作拍了两遍）", ""]
    L += [f"- #{p['a']}（{r['shots'][p['a']]['t0']:.2f}s） vs #{p['b']}（{r['shots'][p['b']]['t0']:.2f}s）"
          f" 相似度 {p['score']:.2f}{'（相邻，可能是同一镜头被误切）' if p['adjacent'] else ''}"
          for p in r["dup_pairs"][:15]] or ["- 无"]
    open(fn, "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("out_dir"); ap.add_argument("--draft")
    ap.add_argument("--dup", type=float, default=0.55)
    ap.add_argument("--draft-verified", action="store_true", help="实际核对草稿与当前视频后才能使用其主轨边界；不代表机器验证内容")
    ap.add_argument("--draft-verification-note", default="", help="记录实际核对依据")
    a = ap.parse_args()
    r = analyze(a.video, a.out_dir, a.draft, a.dup, a.draft_verified, a.draft_verification_note)
    print(open(os.path.join(a.out_dir, "体检报告.md")).read())
