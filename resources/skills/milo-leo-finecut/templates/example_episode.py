# Robot Vacuum Grand Prix 精剪：剪辑表 + 配乐编排；机制在 series/milo-leo/精剪工具/finecut
import json, os, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "finecut"))
from render import Timeline, render_video, mix, mux, save_timeline, fr

NAME = "RobotVacuumGP"
ROUGH, BGM, W, OUT_DIR = sys.argv[1:5]
shots = json.load(open(f"{W}/体检/analysis.json"))["shots"]
beats = json.load(open(f"{W}/bgm_beats.json"))

# (粗剪起点, 粗剪终点, 效果, 说明)
EDL = [
    (0.00, 3.40, None, "起跑线：MILO举发带，LEO前倾"),
    (5.07, 7.80, None, "近景：发带套上红车尾翼，爪回把手"),
    (8.17, 10.13, None, "起跑，MILO抢内弯"),
    (10.40, 15.40, None, "俯拍：MILO走沙发外弯，LEO找底道（粗剪6.1秒，压掉两端空跑）"),
    (16.23, 19.30, None, "沙发底：LEO伏低穿行"),
    (19.70, 22.40, None, "LEO出洞领先，MILO绕出弯"),
    (22.40, 24.50, None, "硬切：红车已装吹风机，拨开关"),
    (24.50, 26.50, None, "风机特写，发带被吹直"),
    (27.73, 28.73, None, "MILO得意咧嘴"),
    (28.73, 29.90, None, "红车冲出"),
    (29.90, 31.07, None, "LEO在前"),
    (31.07, 32.53, None, "MILO从后追平"),
    (33.30, 37.30, None, "抢窄线，顶到洗衣篮"),
    (37.30, 40.43, None, "篮子侧翻，衣服腾起"),
    (40.43, 43.30, None, "白衬衫挂红车、紫毛巾挂蓝车，继续跑"),
    (44.80, 47.60, None, "低机位并排，脸毛被吹平"),
    (48.10, 50.03, None, "揭示：女主人在做下犬式"),
    (50.03, 51.10, None, "两车冲向瑜伽垫"),
    (51.10, 54.50, None, "从她身侧横穿"),
    (54.50, 60.50, None, "近景穿越，衣服扬起落到她背上，她缓缓转头（58.1秒落定，BGM断）"),
    # 删 60.50–65.50：她从画面里消失、两车开过空垫子，与剧情矛盾，且开头是背后臀部特写
    (65.50, 68.43, None, "硬切：她已跪坐；MILO从尾翼取发带（BGM重进）"),
    (68.43, 70.93, None, "MILO把发带套上她额头"),
    (70.93, 72.93, None, "郑重张爪颁奖"),
    (72.93, 74.93, None, "合影：她看衣服再看MILO，LEO压住庆祝爪"),
]
LAND = 58.10      # 毛巾落定：BGM 断
REENTER = 65.50   # 颁奖段起点：BGM 重进
SONG_END = 76.35  # 原曲最后一个小节头，之后衰减
# 粗剪 6.26–6.34 发带弹上尾翼的“啪”：削波到 +2.8dBFS 且前后是纯静音，压 8dB 保留声音去掉爆裂感
AUDIO_FIXES = [(6.25, 6.36, -8)]

tl = Timeline(EDL, shots)
P, DB = beats["period"], beats["downbeats"]


def music_plan(tl):
    a1 = tl.out(LAND)
    b0 = tl.out(REENTER)
    L = tl.total - b0
    # 结尾段：选一个小节头进入，让原曲收尾落在片尾后 ~0.5s（片尾淡出 0.8s）
    bgm_b = min(DB, key=lambda d: abs(d + L - (SONG_END + 0.5)))
    return [dict(out0=0.0, out1=a1, bgm0=0.0, fade_in=0.01, fade_out=0.04, note="开场到毛巾落背"),
            dict(out0=b0, out1=tl.total, bgm0=bgm_b, fade_in=0.01, fade_out=0.8, note="颁奖到片尾，接原曲结尾段")]


def beat_out_times(out_t):
    for m in music_plan(tl):
        if m["out0"] <= out_t <= m["out1"]:
            return [b - m["bgm0"] + m["out0"] for b in beats["beats"]
                    if m["out0"] <= b - m["bgm0"] + m["out0"] <= m["out1"]]
    return None


# 卡拍：毛巾落背那一刀之前的剪点、颁奖段内的剪点吸附到 ±3 帧内的拍点
snaps = tl.snap_cuts(beat_out_times)
music = music_plan(tl)
print("total", tl.total, "snaps", snaps)
for m in music:
    print(m)
EVENTS = [dict(name="洗衣篮被撞", out=round(tl.out(35.16), 3), kind="sound", window=0.4),
          dict(name="毛巾落背，BGM断", out=round(tl.out(LAND), 3), kind="music_stop"),
          dict(name="颁奖段，BGM重进", out=round(tl.out(REENTER), 3), kind="music_start")]

if __name__ == "__main__":
    render_video(tl, ROUGH, W, AUDIO_FIXES)
    mix(tl, W, bgm=BGM, music=music, bgm_gain_db=-7.0)
    outs = mux(W, OUT_DIR, NAME)
    save_timeline(tl, W, ROUGH, bgm=BGM, music=music, beats=dict(period=P, t0=beats["t0"], bpm=beats["bpm"]),
                  events=EVENTS, extra=dict(name=NAME, outputs=outs))
    print("done", outs)
