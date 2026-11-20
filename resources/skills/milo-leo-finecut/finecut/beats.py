# BGM 节拍网格：速度、拍点、小节头、逐小节能量（找乐句起点/低谷用）
# 用法：python3 beats.py <BGM> [输出json]
import json, sys
import numpy as np
from common import read_audio

SR, HOP, NFFT = 22050, 256, 1024


def onset_envelope(y):
    n = 1 + (len(y) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(y[idx] * np.hanning(NFFT), axis=1))
    logs = np.log1p(100 * spec)
    flux = np.maximum(0, np.diff(logs, axis=0)).sum(axis=1)
    flux = np.r_[0, flux]
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")   # 去慢变趋势
    return np.maximum(flux, 0), SR / HOP


def tempo(env, fr, lo=70, hi=180):
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    bpm = 60 * fr / np.maximum(lags, 1)
    ok = (bpm >= lo) & (bpm <= hi)
    # 加权偏好 ~110 BPM 附近，避免倍频/半频
    w = np.exp(-0.5 * (np.log2(bpm / 110) / 0.9) ** 2)
    score = np.where(ok, ac * w, 0)
    L = int(np.argmax(score))
    # 抛物线插值求亚帧周期
    if 1 <= L < len(ac) - 1:
        a, b, c = ac[L - 1], ac[L], ac[L + 1]
        L = L + 0.5 * (a - c) / (a - 2 * b + c + 1e-9)
    return (60 * fr / L, L / fr) if L else (None, None)


def refine_phase(y, t0, period, hop=64, nfft=512):
    """高分辨率起音峰的加权圆周平均，把拍点相位精修到 ~3ms（只用落在拍点 ±1/4 拍内的峰）。"""
    n = 1 + (len(y) - nfft) // hop
    idx = np.arange(nfft)[None, :] + hop * np.arange(n)[:, None]
    S = np.log1p(100 * np.abs(np.fft.rfft(y[idx] * np.hanning(nfft), axis=1)))
    fl = np.r_[0, np.maximum(0, np.diff(S, axis=0)).sum(1)]
    t = np.arange(n) * hop / SR + nfft / 2 / SR
    pk = np.array([i for i in range(3, n - 3) if fl[i] == fl[i - 3:i + 4].max()], dtype=int)
    pk = pk[np.argsort(-fl[pk])][:400]
    ph = ((t[pk] - t0) / period + 0.5) % 1 - 0.5          # 相对当前网格，-0.5..0.5
    keep = np.abs(ph) < 0.25
    if keep.sum() < 10:
        return t0
    w = fl[pk][keep]
    shift = np.angle((w * np.exp(2j * np.pi * ph[keep])).sum()) / (2 * np.pi) * period
    return float((t0 + shift) % period)


def beat_grid(path):
    y = read_audio(path, SR)
    if not len(y):
        raise ValueError("配乐中没有音频样本")
    empty = dict(file=path, bpm=None, period=None, t0=None, beats=[], downbeats=[], bar_rms_db=[], duration=len(y) / SR)
    if len(y) < SR or not np.any(y):
        return empty
    env, fr = onset_envelope(y)
    bpm, period = tempo(env, fr)
    if not period:
        return empty
    t = np.arange(len(env)) / fr
    # 相位：恒定速度网格与起音包络的最大对齐
    phases = np.linspace(0, period, 200, endpoint=False)
    sc = [np.interp(np.arange(ph, t[-1], period), t, env).sum() for ph in phases]
    t0 = refine_phase(y, float(phases[int(np.argmax(sc))]), period)
    beats = np.arange(t0, len(y) / SR, period)
    # 小节头：4 拍里哪一相位的起音最强
    strength = np.interp(beats, t, env)
    k = int(np.argmax([(strength[i::4].mean() if i < len(strength) else -1) for i in range(4)]))
    downbeats = beats[k::4]
    # 逐小节 RMS（dB），找段落/低谷
    rms = []
    for a, b in zip(downbeats, np.r_[downbeats[1:], len(y) / SR]):
        seg = y[int(a * SR):int(b * SR)]
        rms.append(float(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9)) if len(seg) else -99)
    return dict(file=path, bpm=round(float(bpm), 2), period=float(period), t0=t0,
                beats=[round(float(b), 4) for b in beats], downbeats=[round(float(b), 4) for b in downbeats],
                bar_rms_db=[round(r, 1) for r in rms], duration=len(y) / SR)


def snap(t, grid, max_shift=0.1):
    """把时间吸附到最近的拍点；超出 max_shift 秒就不动。返回 (新时间, 偏移)。"""
    g = np.asarray(grid)
    i = int(np.argmin(np.abs(g - t)))
    d = float(g[i] - t)
    return (float(g[i]), d) if abs(d) <= max_shift else (t, 0.0)


if __name__ == "__main__":
    r = beat_grid(sys.argv[1])
    if len(sys.argv) > 2:
        json.dump(r, open(sys.argv[2], "w"), indent=1)
    if not r["beats"]:
        print("未检测到可靠的节拍")
        sys.exit(0)
    print(f"BPM {r['bpm']}  首拍 {r['t0']:.3f}s  拍长 {r['period']:.4f}s  小节头 {r['downbeats'][:4]}")
    for i, (db, rm) in enumerate(zip(r["downbeats"], r["bar_rms_db"])):
        print(f"小节{i + 1:3d} {db:7.2f}s  {rm:6.1f} dB  " + "█" * max(0, int((rm + 40) / 1.5)))
