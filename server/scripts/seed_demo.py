"""Synthetic demo data for UI walkthroughs and Figma comparison screenshots.

Everything goes through the real service commands with fake executors and the fake image provider;
nothing is sent to any model or paid service. Images are simple drawn placeholders (no third-party art).

  ADW_DATA_DIR=/tmp/adw-demo python -B server/scripts/seed_demo.py
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from workbench.executors.fake import FakeExecutor, draft, proposal, review  # noqa: E402
from workbench.images import FakeImageProvider  # noqa: E402
from workbench.service import Workbench  # noqa: E402

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
SKY, GRASS, ORANGE, BLUE = (150, 196, 228), (110, 168, 92), (238, 150, 60), (70, 110, 180)


def scene(label, w=288, h=512, night=False, grid=False, seed=0):
    img = Image.new("RGB", (w, h), (24, 36, 70) if night else SKY)
    d = ImageDraw.Draw(img)
    cells = [(c % 3, c // 3) for c in range(9)] if grid else [(0, 0)]
    cw, ch = (w // 3, h // 3) if grid else (w, h)
    for n, (cx, cy) in enumerate(cells):
        x0, y0 = cx * cw, cy * ch
        d.rectangle([x0, y0 + ch * 0.62, x0 + cw, y0 + ch], fill=(30, 50, 40) if night else GRASS)
        d.rectangle([x0 + cw * 0.62, y0 + ch * 0.18, x0 + cw * 0.74, y0 + ch * 0.66], fill=(222, 190, 160))
        d.ellipse([x0 + cw * 0.58, y0 + ch * 0.12, x0 + cw * 0.78, y0 + ch * 0.2], fill=(214, 80, 70))
        px = x0 + cw * (0.2 + 0.04 * ((n + seed) % 4))
        d.ellipse([px, y0 + ch * 0.48, px + cw * 0.26, y0 + ch * 0.74], fill=ORANGE)
        d.ellipse([px + cw * 0.03, y0 + ch * 0.36, px + cw * 0.23, y0 + ch * 0.52], fill=ORANGE)
        if grid:
            d.rectangle([x0, y0, x0 + cw - 1, y0 + ch - 1], outline=(10, 10, 10), width=2)
            d.text((x0 + 6, y0 + 4), str(n + 1), fill=(255, 255, 255), font=ImageFont.truetype(FONT, 14))
    if night:
        d.ellipse([w * 0.7, h * 0.08, w * 0.85, h * 0.16], fill=(240, 232, 190))
    d.text((8, h - 26), label, fill=(255, 255, 255), font=ImageFont.truetype(FONT, 16))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def synthetic_film(path: Path):
    """24 s colour-bar clip with hard cuts at 9 s, 12 s and 18 s and no audio track (real ffmpeg)."""
    parts = []
    for i, (dur, colour) in enumerate([(9, "0x2b3a55"), (3, "0x6b4f2a"), (6, "0xc9a227"), (6, "0x1f5f4a")]):
        p = path.parent / f"part{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={colour}:s=320x180:d={dur}:r=24",
                        "-pix_fmt", "yuv420p", str(p)], check=True)
        parts.append(p)
    lst = path.parent / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(path)],
                   check=True)


STORY_N04 = dict(
    synopsis=("周六下午，女主人在屋里午睡。MILO 在后院拖来一个巨大的纸箱，绕着它转了三圈，眼睛一亮：这是一枚火箭。\n"
              "他用胶带把三个纸箱叠起来，剪出两片“机翼”，把正在草坪椅上假寐的 LEO 叫来扶着。LEO 一脸不情愿，却还是扶稳了。\n"
              "燃料是橙汁。MILO 每倒一杯，就踮脚确认漏斗里的液面；倒到第三杯时，LEO 叹了口气，低头看一眼腕上的秒表，才把它掏出来按下。\n"
              "点火按钮一按，纸箱底部冒出泡泡和烟雾，火箭轻轻颤动、离地，又落回草坪。LEO 按停秒表：3.00。MILO 不服气，又拖来一个纸箱。"),
    cast="|角色ID|身份与作用|独立身份基准|\n|---|---|---|\n|milo|主角，造火箭|需要|\n|leo|扶火箭、计时|需要|",
    handoff="目标时长约 86 秒；火箭离地高度须一眼可读；秒表读数 3.00 由后期叠字。",
)


def main():
    root = Path(os.environ.get("ADW_DATA_DIR", "/tmp/adw-demo"))
    codex, claude = FakeExecutor("codex"), FakeExecutor("claude")
    ag = FakeExecutor("antigravity", isolation="unverified", reads_images=False)
    wb = Workbench(root, {"codex": codex, "claude": claude, "antigravity": ag},
                   image_providers={"fake-image": FakeImageProvider("fake-image")})

    def drain(run_id):
        q = [run_id]
        while q:
            q += wb.execute_run(q.pop(0))

    def art(eid, stage):
        return next(a for a in wb.episode_view(eid)["artifacts"] if a["stage"] == stage)

    def save_adopt(eid, stage, content, tag):
        a = art(eid, stage)
        v = wb.save_manual(a["id"], a["adopted_version_id"], content, f"{tag}-{stage}-s")["versionId"]
        wb.adopt(a["id"], v, a["adopted_version_id"], f"{tag}-{stage}-a", and_continue=True)
        return v

    plan_exec = {"create": {"provider": "claude"}, "review": {"provider": "codex"}}
    pid = wb.create_project("puggy", command_id="demo-puggy")["projectId"]
    wb.set_publish_settings(pid, json.loads((Path.home() / "Documents/自媒体 2/series/milo-leo/发布设置.json").read_text())
                            if (Path.home() / "Documents/自媒体 2/series/milo-leo/发布设置.json").is_file() else
                            dict(title_prefix="MILO ", body_fixed_lines=["New MILO & LEO chaos every day."],
                                 fixed_tags=["cat", "milo"], max_extra_tags=2))
    eps = {}
    for code, title in [("N03", "Bigger Pets"), ("N04", "MILO's Backyard Rocket"), ("N05", "Night Shift: Raccoon Gang"),
                        ("N06", "Operation Roast Chicken"), ("N08", "Band Night 2"), ("N09", "LEO 的生日蛋糕")]:
        eps[code] = wb.create_episode(pid, code, title, ["厨房门上了锁"] if code == "N06" else [],
                                      command_id=f"demo-ep-{code}")["episodeId"]

    def build(code, story=STORY_N04, deliver=True, segments=None):
        eid = eps[code]
        wb.start_episode(eid, "idea", plan_exec, idea_text=story["synopsis"].splitlines()[0], command_id=f"k-{code}")
        claude.script = [lambda r, s=story: draft(r, **s)]
        t = wb.create_task(eid, "story", "按选题写故事稿", command_id=f"t-{code}")
        drain(t["runId"])
        v = art(eid, "story")["versions"][-1]["id"]
        drain(wb.submit_review(v, f"r-{code}")["runId"])
        wb.adopt(art(eid, "story")["id"], v, None, f"a-{code}", and_continue=True)
        save_adopt(eid, "script", dict(scenes=[
            dict(id="阶段1", heading="外景 后院 下午", action="MILO 在后院拖来一个巨大的纸箱，绕着它转了三圈，眼睛一亮。LEO 在草坪椅上翻了个身，假装没看见。"),
            dict(id="阶段2", heading="外景 后院 下午", action="MILO 用胶带把三个纸箱叠起来，又剪出两片“机翼”。LEO 被叫来扶着，一脸不情愿。"),
            dict(id="阶段3", heading="外景 后院 下午", action="LEO 坐在草坪椅上，眯眼看着；MILO 每倒一杯橙汁，就踮脚确认漏斗里的液面。MILO 倒第三杯时，LEO 叹了口气，低头看一眼腕上的秒表，才把它掏出来按下；MILO 的尾巴随着每一声“嘀”抖一下。"),
            dict(id="阶段4", heading="外景 后院 下午", action="MILO 按下按钮。纸箱底部冒出泡泡和烟雾，火箭轻轻颤动……"),
            dict(id="阶段5", heading="外景 后院 下午", action="火箭落回草坪，LEO 按停秒表：3.00。MILO 不服气地又拖来一个纸箱，LEO 举起小牌（牌上文字后期叠加）。")]), code)
        segs = segments or {"G01": [4000, 4000, 4000], "G02": [5000, 4000, 5000], "G03": [3000, 2000, 4000, 4000],
                            "G04": [3000, 4000, 4000], "G05": [5000, 5000, 5000], "G06a": [5000, 6000], "G06b": [5000, 5000]}
        actions = ["MILO 发现纸箱，眼睛一亮", "LEO 翻身假装没看见", "MILO 绕纸箱三圈", "叠纸箱", "剪机翼", "LEO 扶着",
                   "MILO 踮脚倒橙汁，确认液面", "LEO 眯眼，叹一口气", "MILO 转身，比出三根爪子", "LEO 掏出秒表按下；尾巴随“嘀”抖",
                   "爪子按下按钮，停 0.5–1 秒", "冒泡、火箭离地又落回", "MILO 和 LEO 盯着落回的火箭", "LEO 按停秒表 3.00",
                   "MILO 不服气", "又拖来一个纸箱", "LEO 举起小牌", "收尾"]
        shots, segs_out, n = [], [], 0
        for order, (sid, durs) in enumerate(segs.items(), 1):
            segs_out.append(dict(id=sid, order=order, strategy="grid" if sid in ("G03", "G04") else "flex"))
            for d in durs:
                n += 1
                shots.append(dict(id=f"S{n:02d}", segment=sid, order=n, duration_ms=d,
                                  camera=["近景 平视 固定", "特写 固定", "中景 缓推", "远景 低机位"][n % 4],
                                  action=actions[(n - 1) % len(actions)], origin=dict(scene=f"阶段{min(5, 1 + n // 4)}"),
                                  mustKeep=n in (10, 12)))
        plan = dict(shots=shots, segments=segs_out, notes="依据剧本 R03；镜头时长加总等于段长。")
        save_adopt(eid, "plan", plan, code)
        imgs = {}
        for i, sid in enumerate(segs, 1):
            grid = sid in ("G03", "G04")
            data = scene(f"{code} {sid}{' 九宫格' if grid else ''}", grid=grid, seed=i)
            imgs[sid] = wb.register_asset(pid, f"{sid} {'九宫格' if grid else '场面图'}", data, "grid" if grid else "state",
                                          episode_id=eid, grid=grid, roles=["milo", "leo"])
        from workbench import timeline
        pr = {}
        for seg in timeline.layout(plan)["segments"]:
            d = seg["duration_ms"]
            pr[seg["id"]] = dict(refs=[dict(assetVersionId=imgs[seg["id"]]["versionId"], label="图片1", kind="image",
                                            role="本段九宫格" if seg["strategy"] == "grid" else "场面", grid=seg["strategy"] == "grid")],
                                 txt=(f"【基本设定】双后腿直立的胖橘猫 MILO 与虎斑猫 LEO，普通家猫尺寸。\n【开场】{seg['id']} 后院，下午阳光。\n"
                                      f"00:00.000-00:{d // 1000:02d}.000 按图片1 顺序推进："
                                      + "；".join(s["action"] for s in shots if s["segment"] == seg["id"]) + "。"),
                                 sound=dict(bgm=False, ambience=["后院鸟叫"], dialogue=[]))
        pv = save_adopt(eid, "prompts", dict(segments=pr), code)
        for seg in segs:
            for p in ("cold_read", "comparison"):
                drain(wb.start_prompt_review(pv, seg, p, f"pr-{code}-{seg}-{p}")["runId"])
        save_adopt(eid, "publish", dict(title="Backyard Rocket", body="MILO builds a cardboard rocket. LEO times it.",
                                        tags=["rocket"], suno_title="Cardboard Countdown", suno_style="playful lofi, ukulele",
                                        suno_exclude="vocals, heavy drums",
                                        cues=[dict(at="00:26.000", action="压低"), dict(at="00:39.000", action="停"),
                                              dict(at="00:47.000", action="重新进入")]), code)
        return eid, pv, wb.deliver(eid, "full", f"d-{code}") if deliver else None

    build("N03")
    n04, pv04, d04 = build("N04")
    build("N06")
    claude.script = [lambda r: proposal(r, [dict(category="signature", content="MILO 不服输：失败后立刻再拖来一个纸箱",
                                                 source=dict(objectId="story", versionId="R03", unit="paragraph", anchor="第4段",
                                                             evidence="MILO 不服气，又拖来一个纸箱"),
                                                 suggestedStrength="variable", rationale="可成为连续集的招牌反应"),
                                            dict(category="prop", content="LEO 的腕上秒表", source=dict(
                                                objectId="story", versionId="R03", unit="paragraph", anchor="第3段",
                                                evidence="低头看一眼腕上的秒表"), suggestedStrength="variable", rationale="计时道具可复用"),
                                            dict(category="space", content="后院草坪与草坪椅的位置", source=dict(
                                                objectId="plan", versionId="R02", unit="shot", anchor="S05",
                                                evidence="草坪椅在纸箱左侧"), suggestedStrength="required", rationale="空间连续")])]
    wb.start_closure(d04["deliveryId"], "close-n04")

    # N05: story drafted and under review; space lock pending for G04
    n05 = eps["N05"]
    wb.start_episode(n05, "idea", plan_exec, idea_text="深夜，三只浣熊来偷猫粮，MILO 三次设防", command_id="k-N05")
    claude.script = [lambda r: draft(r, synopsis=("深夜，后院的猫粮桶第三次被翻倒。门铃监控里，三只浣熊排着队溜进院子。\n"
                                                   "第一回合：MILO 把猫粮桶搬上高台。浣熊老大推来垃圾桶当台阶，轻松够到。\n"
                                                   "第二回合：LEO 想回屋睡觉，却被 MILO 拉来守夜。LEO 打开自动喷水器——浣熊被淋湿，MILO 也被淋湿。\n"
                                                   "第三回合：MILO 用他拆装小家电的本领，把旧闹钟改成警报器。警报响起，浣熊逃走；女主人也被吵醒。"),
                                     cast="|角色ID|身份与作用|独立身份基准|\n|---|---|---|\n|milo|主角|需要|\n|leo|守夜|需要|\n|raccoon-boss|浣熊老大|需要|",
                                     handoff="竖屏 9:16，60–120 秒；桶的高度待空间锁定确认。")]
    t5 = wb.create_task(n05, "story", "按方向①写故事稿", command_id="t-N05")
    drain(t5["runId"])
    wb.add_space_item(n05, "桶的高度", "猫粮桶在高台上，比垃圾桶顶高一个爪子", "剧本阶段 2", ["G04"], command_id="space-n05")

    # N08 drafting, N09 story adopted
    wb.start_episode(eps["N08"], "idea", plan_exec, idea_text="乐队之夜第二场", command_id="k-N08")
    claude.script = [lambda r: draft(r, synopsis=("周五晚上，女主人在客厅练吉他。MILO 搬来锅碗瓢盆，组了一支只有厨具的乐队。\n"
                                                   "LEO 被拉来当鼓手，却总是慢半拍；MILO 看了一眼，转身躺回沙发，让 LEO 自己去想办法。\n"
                                                   "最后 LEO 用汤勺敲出节拍器的节奏，女主人也跟着弹了起来。"),
                                     cast=STORY_N04["cast"], handoff="目标约 70 秒。")]
    drain(wb.create_task(eps["N08"], "story", "写故事稿", command_id="t-N08")["runId"])
    v8 = art(eps["N08"], "story")["versions"][-1]["id"]
    codex.script = [lambda r: review(r, "return", [
        dict(category="setting", settingId="milo-active", anchor="第2段", evidence="MILO 看了一眼，转身躺回沙发",
             reason="MILO 发现乐队出问题却随手走开，没有凭本领改变局面，与必守设定冲突"),
        dict(category="clarity", anchor="第3段", evidence="LEO 用汤勺敲出节拍器的节奏", reason="结尾笑点不够清楚：观众看不出节拍器从哪来")])]
    drain(wb.submit_review(v8, "r-N08")["runId"])
    wb.start_episode(eps["N09"], "idea", plan_exec, idea_text="LEO 的生日蛋糕", command_id="k-N09")
    claude.script = [lambda r: draft(r, synopsis="LEO 生日，MILO 偷偷烤了一个歪掉的蛋糕。", cast=STORY_N04["cast"], handoff="无")]
    drain(wb.create_task(eps["N09"], "story", "写故事稿", command_id="t-N09")["runId"])
    v9 = art(eps["N09"], "story")["versions"][-1]["id"]
    drain(wb.submit_review(v9, "r-N09")["runId"])
    wb.adopt(art(eps["N09"], "story")["id"], v9, None, "a-N09")

    # N04: a script draft R04 changing stage 3 (J24 inline diff + impact), a pending local redraw (J18), library entry (J48)
    sa = art(n04, "script")
    sc = json.loads(json.dumps(sa["versions"][-1]["content"]))
    sc["scenes"][2]["action"] = ("LEO 坐在草坪椅上，眯眼看着；MILO 每倒一杯橙汁，就踮脚确认漏斗里的液面。MILO 倒第三杯时，LEO 叹了口气，"
                                  "低头看一眼腕上的秒表，才把它掏出来按下；MILO 的尾巴随着每一声“嘀”抖一下。")
    sc["scenes"][2]["action"] = sc["scenes"][2]["action"].replace("LEO 叹了口气，低头看一眼腕上的秒表，才把它掏出来按下",
                                                                  "LEO 叹了口气，低头看一眼腕上的秒表，才把它掏出来按下，按下前先瞄了一眼漏斗")
    wb.save_manual(sa["id"], sa["adopted_version_id"], sc, "n04-script-r04")
    g03 = next(a for a in wb.episode_assets(n04) if a["name"].startswith("G03"))
    wb.mark_image(n04, g03["id"], "required", command_id="mark-g03")
    alt = wb.register_asset(pid, "G03 九宫格 候选B", scene("N04 G03 候选B", grid=True, seed=5), "grid", episode_id=n04, grid=True)
    wb.mark_image(n04, alt["versionId"], "backup", command_id="mark-g03b")
    wb.redraw_cells(n04, g03["id"], [5], [alt["versionId"]], "G03 用 A。把 B 第 5 格的表情换到 A 的第 5 格", "fake-image", "96x64",
                    budget_cents=200, command_id="redraw-g03")
    yard = wb.register_asset(pid, "后院空景", scene("后院空景", seed=2), "scene", episode_id=n04)
    entry = wb.submit_reuse(yard["versionId"], "后院白天空景", "仅白天、竖屏", "草坪椅在纸箱左侧", "9:16", command_id="reuse-yard")
    wb.review_library(entry["entryId"], "approved", command_id="approve-yard")

    # N07: imported story waiting for kickoff (J49 → J50)
    n07 = wb.create_episode(pid, "N07", "Car Wash", command_id="demo-ep-N07")["episodeId"]
    wb.import_file(pid, "N07_洗车店.md", "# N07 洗车店\nMILO 在洗车店看见自动泡沫枪，决定自己给车洗澡。\n".encode(), "story_source",
                   n07, command_id="imp-n07")
    for code, prem, mech in [("T21", "MILO 把吸尘器改成喂鸟器", "用途错位"), ("T22", "LEO 偷偷参加猫咪选美", "反转回收")]:
        wb.add_topic(pid, code, prem, mech, command_id=f"topic-{code}")

    # 夜班柴犬: new project, empty settings, first episode drafted
    sid = wb.create_project("_template", "夜班柴犬", "一只认真过头的柴犬在便利店值夜班", command_id="demo-shiba")["projectId"]
    wb.create_episode(sid, "E01", "第一次值夜班", command_id="demo-shiba-e1")   # J53: kickoff not yet confirmed

    # 末班渡轮: reference film → pack (real ffprobe / ffmpeg on a synthetic clip)
    fid = wb.create_project("_template", "末班渡轮", "借检票亭里认人的节奏", command_id="demo-ferry")["projectId"]
    f1 = wb.create_episode(fid, "N01", "末班渡轮", command_id="demo-ferry-n1")["episodeId"]
    with tempfile.TemporaryDirectory() as tmp:
        film = Path(tmp) / "末班渡轮_参考.mp4"
        synthetic_film(film)
        src = wb.import_reference(fid, film.name, film.read_bytes(), command_id="ref-ferry")
    pack = wb.create_pack(src["sourceId"], f1, request="借检票亭里认人的节奏，把故事放到末班渡轮上", command_id="pack-ferry")
    for s in wb.pack_view(pack["packId"])["shots"]:
        wb.edit_cuts(pack["packId"], "confirm", s["code"], fields=dict(camera="近景 平视", motion="固定", narrative="揭示"),
                     command_id=f"cf-{s['code']}")
    wb.add_beat(pack["packId"], "B03", ["S03"], "两半印痕合拢", "观众先认出两人关系", command_id="beat-b03")
    wb.bookmark(f1, pack["packId"], "S03", "同框揭示，观众先认出关系", "两半印痕都在画面内", command_id="bm-s03")
    print(json.dumps(dict(dataDir=str(root), puggy=pid, n04=n04, n05=n05, n07=n07, shiba=sid, ferry=fid, pack=pack["packId"]),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
