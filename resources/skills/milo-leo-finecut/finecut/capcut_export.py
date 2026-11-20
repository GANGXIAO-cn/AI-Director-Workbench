# 精剪时间线 → CapCut（剪映国际版 Mac 9.x）可编辑草稿（2026-10-18 在 CapCut 9.5.0 实测能打开）
# 做法：使用包内 CapCut 9.x 的原生明文草稿结构模板；不改动任何已有草稿。
#   视频轨：每个剪辑段一段（引用粗剪 mp4 的源区间，可在 CapCut 里拖剪点/变速/重排）；带效果或修过音的段引用烘焙好的 clips/NN.mov
#   音频轨：BGM 按配乐编排分段（音量=压低后的增益）；音效按重叠情况分多轨
#   字幕轨：timeline.json 里有 captions 时，克隆包内模板的文字片段，每条字幕一个可编辑文字
#   CapCut 里没有的：BGM 侧链让位、段落淡入淡出、响度标准化——成片 mp4 里有，草稿里需要时手动加
# 用法：
#   python3 capcut_export.py build <过程稿/精剪> <草稿名>      # 生成到 <过程稿/精剪>/capcut/<草稿名>
#   python3 capcut_export.py install <过程稿/精剪> <草稿名>    # 需先 Cmd+Q 退出 CapCut；装进草稿库并登记
import math
from pathlib import Path
import copy, json, os, shutil, subprocess, sys, time, uuid
from common import FPS, probe
from render import fill_scale

from paths import CAPCUT_ROOT as ROOT, DRAFT_TEMPLATE
ROOT_META = os.path.join(ROOT, "root_meta_info.json")


def us(t):
    return int(round(t * 1_000_000))


def fus(t):
    """时间先对齐到帧再换微秒：相邻段用同一个函数算边界，保证首尾严格相接。"""
    return us(round(t * FPS) / FPS)


def U():
    return str(uuid.uuid4()).upper()


def _load(d):
    return json.load(open(os.path.join(d, "draft_info.json"), encoding="utf-8"))


def find_templates(exclude=()):
    """在草稿库里找最近的明文草稿：一个有视频轨，一个有音频轨（用户自己导入过音乐的）。"""
    vid = aud = None
    names = sorted((n for n in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, n, "draft_info.json"))),
                   key=lambda n: os.path.getmtime(os.path.join(ROOT, n, "draft_info.json")), reverse=True)
    for n in names[:400]:
        if n in exclude or "精剪" in n:      # 不拿自己导出的草稿当模板
            continue
        try:
            j = _load(os.path.join(ROOT, n))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        types = [t["type"] for t in j.get("tracks", [])]
        if vid is None and "video" in types and j["materials"].get("videos"):
            vid = os.path.join(ROOT, n)
        if aud is None and j["materials"].get("audios"):
            mt = {m["id"]: m.get("type") for m in j["materials"]["audios"]}
            if any(t["type"] == "audio" and t["segments"] and t.get("attribute", 0) == 0
                   and mt.get(t["segments"][0]["material_id"]) in ("extract_music", "music")
                   for t in j["tracks"]):
                aud = os.path.join(ROOT, n)
        if vid and aud:
            break
    return vid, aud


def find_text_template(exclude=()):
    """找最近一个手动加过文字（type=text，最好带描边）的明文草稿，克隆它的文字素材/片段结构。"""
    names = sorted((n for n in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, n, "draft_info.json"))),
                   key=lambda n: os.path.getmtime(os.path.join(ROOT, n, "draft_info.json")), reverse=True)
    fallback = None
    for n in names[:400]:
        if n in exclude or "精剪" in n:
            continue
        try:
            j = _load(os.path.join(ROOT, n))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        for t in j["materials"].get("texts", []):
            if t.get("type") != "text":
                continue
            if t.get("border_color"):
                return os.path.join(ROOT, n), t["id"]
            fallback = fallback or (os.path.join(ROOT, n), t["id"])
    return fallback


def _by_id(j):
    m = {}
    for k, lst in j["materials"].items():
        if isinstance(lst, list):
            for x in lst:
                if isinstance(x, dict) and "id" in x:
                    m[x["id"]] = (k, x)
    return m


def _seg_proto(j, ttype):
    t = next(t for t in j["tracks"] if t["type"] == ttype and t["segments"])
    seg = t["segments"][0]
    idx = _by_id(j)
    extras = [idx[r] for r in seg["extra_material_refs"] if r in idx]
    kind, mat = idx[seg["material_id"]]
    return t, seg, kind, mat, extras


def build(work, name):
    tl = json.load(open(os.path.join(work, "timeline.json"), encoding="utf-8"))
    vt = at = str(DRAFT_TEMPLATE)
    if not name or os.path.basename(name) != name or name in (".", ".."):
        raise ValueError("草稿名称不能包含路径")
    J = _load(vt)
    A = _load(at)
    v_track, v_seg, v_kind, v_mat, v_extra = _seg_proto(J, "video")
    a_track, a_seg, a_kind, a_mat, a_extra = _seg_proto(A, "audio")

    stage = os.path.join(work, "capcut", name)
    if os.path.exists(stage):
        raise FileExistsError("已有同名草稿，请给新版本另取名称")
    res = os.path.join(stage, "Resources")
    os.makedirs(res)
    final = os.path.join(ROOT, name)

    out = copy.deepcopy(J)
    # 画布按成片画幅（timeline.json 的 frame；旧时间线没有就是竖屏），不沿用模板草稿的画布
    FW, FH = tl.get("frame") or (1080, 1920)
    out["canvas_config"] = dict(out.get("canvas_config") or {}, width=FW, height=FH, ratio="original")  # CapCut 9.5 自存草稿用 original
    # 主轨原声必须开着：模板草稿若关过原声（config.video_mute），克隆后整片画内声都被静音（2026-11-01 大镖客1 用户剪辑时踩到）
    out.setdefault("config", {})["video_mute"] = False
    for k, v in out["materials"].items():
        if isinstance(v, list):
            out["materials"][k] = []
    out["tracks"] = []
    tid = U()
    # 片尾（render.outro）接在正片后面，草稿总长要算上
    outro_d = round(probe(tl["outro"])["duration"] * FPS) / FPS if tl.get("outro") else 0.0
    total = tl["total"] + outro_d
    out.update(id=tid, name="", duration=us(total), keyframes={k: [] for k in J["keyframes"]}
               if isinstance(J.get("keyframes"), dict) else J.get("keyframes"))
    media = {}   # 源路径 → (最终路径, 素材id, 信息)

    def add_media(src, kind):
        if src in media:
            return media[src]
        fn = os.path.basename(src)
        if os.path.exists(os.path.join(res, fn)):
            fn = U() + "_" + fn
        shutil.copy2(src, os.path.join(res, fn))
        info = probe(src)
        mid = U()
        if kind == "video":
            m = copy.deepcopy(v_mat)
            m.update(id=mid, path=os.path.join(final, "Resources", fn), material_name=fn,
                     duration=us(info["duration"]), width=info["width"], height=info["height"],
                     has_audio=bool(info.get("has_audio")), local_material_id="", unique_id="")
            out["materials"]["videos"].append(m)
        else:
            m = copy.deepcopy(a_mat)
            m.update(id=mid, path=os.path.join(final, "Resources", fn), name=fn, duration=us(info["duration"]),
                     type="extract_music", video_id="", local_material_id=str(uuid.uuid4()), unique_id="",
                     music_id="", wave_points=[])
            out["materials"].setdefault("audios", []).append(m)
        media[src] = (m["path"], mid, info, kind)
        return media[src]

    def add_segment(proto, extras, mat_id, src0, dur, tgt0, volume, track_index):
        s = copy.deepcopy(proto)
        refs = []
        for kind, x in extras:
            y = copy.deepcopy(x)
            y["id"] = U()
            out["materials"].setdefault(kind, []).append(y)
            refs.append(y["id"])
        s.update(id=U(), material_id=mat_id, extra_material_refs=refs, volume=volume, last_nonzero_volume=volume or 1.0,
                 source_timerange={"start": fus(src0), "duration": fus(tgt0 + dur) - fus(tgt0)},
                 target_timerange={"start": fus(tgt0), "duration": fus(tgt0 + dur) - fus(tgt0)},
                 speed=1.0, keyframe_refs=[], common_keyframes=[], track_render_index=track_index,
                 track_attribute=0, visible=True)
        return s

    # 视频轨
    vtrack = copy.deepcopy(v_track)
    vtrack.update(id=U(), segments=[], attribute=0, flag=0)
    for i, c in enumerate(tl["clips"]):
        d = c["out1"] - c["out0"]          # 变速/震屏/推镜都烤进了渲染段，按成片时长引用
        baked = c.get("fx") or c.get("audio_fixes")
        src = os.path.join(work, "clips", f"{i:02d}.mov") if baked else tl["rough"]
        _, mid, _, _ = add_media(src, "video")
        seg = add_segment(v_seg, v_extra, mid, 0.0 if baked else c["src0"], d, c["out0"], 1.0, 0)
        if tl.get("fill") and not baked:   # 渲染过的段已经放大过；直接引用粗剪的段在 CapCut 里等比放大去黑边
            k = fill_scale(tl["fill"])
            seg.setdefault("clip", {})["scale"] = {"x": k, "y": k}
        vtrack["segments"].append(seg)
    if outro_d:                            # 片尾自带提示音，跟视频段一起
        _, mid, _, _ = add_media(tl["outro"], "video")
        vtrack["segments"].append(add_segment(v_seg, v_extra, mid, 0.0, outro_d, tl["total"], 1.0, 0))
    out["tracks"].append(vtrack)

    # BGM 轨
    if tl.get("bgm") and tl.get("music"):
        _, mid, _, _ = add_media(tl["bgm"], "audio")
        tr = copy.deepcopy(a_track)
        tr.update(id=U(), segments=[], name="BGM", is_default_name=False, attribute=0, flag=0)
        g = 10 ** (tl.get("bgm_gain_db", 0) / 20)
        bgm_dur = add_media(tl["bgm"], "audio")[2]["duration"]
        for m in tl["music"]:
            # 段在原曲末尾收住时，按帧取整可能比文件多出一帧：截到文件内（只差原曲最后的衰减尾巴）
            d = min(m["out1"] - m["out0"], math.floor((bgm_dur - round(m["bgm0"] * FPS) / FPS) * FPS) / FPS)
            tr["segments"].append(add_segment(a_seg, a_extra, mid, m["bgm0"], d, m["out0"], g, 1))
        out["tracks"].append(tr)
    # 音效：重叠的分到不同轨
    lanes = []
    for s in sorted(tl.get("sfx", []), key=lambda s: s["at"]):
        _, mid, info, _ = add_media(s["file"], "audio")
        at = round(s["at"] * FPS) / FPS     # 起点对齐到帧、时长向下取整到帧，按帧取整后才不会超出素材
        dur = min(math.floor(info["duration"] * FPS) / FPS, tl["total"] - at)
        lane = next((k for k, end in enumerate(lanes) if end <= at), None)
        if lane is None:
            lanes.append(0); lane = len(lanes) - 1
            tr = copy.deepcopy(a_track); tr.update(id=U(), segments=[], name=f"音效{lane + 1}", is_default_name=False,
                                                   attribute=0, flag=0)
            out["tracks"].append(tr)
        lanes[lane] = at + dur
        tr = [t for t in out["tracks"] if t.get("name") == f"音效{lane + 1}"][0]
        tr["segments"].append(add_segment(a_seg, a_extra, mid, 0.0, dur, at, 10 ** (s.get("gain_db", 0) / 20), 2 + lane))

    # 字幕轨：每条字幕一个可编辑的文字片段（样式同烧录版：Rubik Bold 黄字黑描边；字号是估的，CapCut 里可整体调）
    if tl.get("captions"):
        tt = (str(DRAFT_TEMPLATE), "text-material")
        T = _load(tt[0])
        tidx = _by_id(T)
        t_mat = tidx[tt[1]][1]
        t_track = next(t for t in T["tracks"] if t["type"] == "text" and any(sg["material_id"] == tt[1] for sg in t["segments"]))
        t_seg = next(sg for sg in t_track["segments"] if sg["material_id"] == tt[1])
        t_extra = [tidx[r] for r in t_seg["extra_material_refs"] if r in tidx]
        from render import CAPTION_FONT, CAPTION_FILL
        rgb = [round(c / 255, 4) for c in CAPTION_FILL]
        tr = copy.deepcopy(t_track)
        tr.update(id=U(), segments=[], name="字幕", is_default_name=False, attribute=0, flag=0)
        for c in tl["captions"]:
            m = copy.deepcopy(t_mat)
            if c.get("markup"):   # 品牌动效字幕（brand_captions.py）：草稿里只有静态样式——奶油字墨描边，关键词黄字；动效只在烧录版
                import re as _re
                from brand_captions import FONT as BF, INK, CREAM, YELLOW
                styles, pos = [], 0
                for part in _re.split(r"(\*[^*]+\*)", c["markup"]):
                    if not part:
                        continue
                    txt, col = (part.strip("*"), YELLOW) if part.startswith("*") else (part, CREAM)
                    styles.append({"fill": {"content": {"solid": {"color": [round(v / 255, 4) for v in col]}, "render_type": "solid"}},
                                   "range": [pos, pos + len(txt)],
                                   "strokes": [{"width": 0.12, "mode": 0, "content": {"solid": {"color": [round(v / 255, 4) for v in INK]}, "render_type": "solid"}}],
                                   "useLetterColor": True, "size": 24.0, "bold": False,
                                   "font": {"path": BF, "id": ""}})
                    pos += len(txt)
                content = {"styles": styles, "text": c["text"]}
                m.update(id=U(), content=json.dumps(content, ensure_ascii=False), type="text", font_path=BF,
                         font_size=24.0, text_color="#%02x%02x%02x" % CREAM, border_color="#%02x%02x%02x" % INK,
                         border_width=0.12, alignment=1)
            else:
                content = {"styles": [{"fill": {"content": {"solid": {"color": rgb}, "render_type": "solid"}},
                                       "range": [0, len(c["text"])],
                                       "strokes": [{"width": 0.08, "mode": 0,
                                                    "content": {"solid": {"color": [0, 0, 0]}, "render_type": "solid"}}],
                                       "useLetterColor": True, "size": 15.0,
                                       "font": {"path": CAPTION_FONT, "id": "7148699606082130433"}}],
                           "text": c["text"]}
                m.update(id=U(), content=json.dumps(content, ensure_ascii=False), type="text", font_path=CAPTION_FONT,
                         font_size=15.0, text_color="#%02x%02x%02x" % CAPTION_FILL, border_color="#000000",
                         border_width=0.08, alignment=1)
            font_file = Path(m["font_path"])
            bundled_font = Path(res) / font_file.name
            if not bundled_font.exists():
                shutil.copy2(font_file, bundled_font)
            font_path = os.path.join(final, "Resources", font_file.name)
            for style in content["styles"]:
                style["font"]["path"] = font_path
            m.update(font_path=font_path, content=json.dumps(content, ensure_ascii=False))
            for k in ("base_content", "recognize_text", "recognize_task_id"):
                if k in m:
                    m[k] = "" if k != "base_content" else m["content"]
            out["materials"].setdefault("texts", []).append(m)
            sg = copy.deepcopy(t_seg)
            refs = []
            for kind, x in t_extra:
                y = copy.deepcopy(x); y["id"] = U()
                out["materials"].setdefault(kind, []).append(y); refs.append(y["id"])
            sg.update(id=U(), material_id=m["id"], extra_material_refs=refs, source_timerange=None,
                      target_timerange={"start": fus(c["t0"]), "duration": fus(c["t1"]) - fus(c["t0"])},
                      keyframe_refs=[], common_keyframes=[], track_render_index=9, track_attribute=0, visible=True)
            sg.setdefault("clip", {}).update(scale={"x": 0.78, "y": 0.78}, rotation=0.0,
                                             transform={"x": 0.0, "y": round((FH / 2 - c["y"]) / (FH / 2), 4)})
            tr["segments"].append(sg)
        out["tracks"].append(tr)

    # 写文件：根目录 + Timelines/<id>/ 各一份 draft_info.json（与 CapCut 9.5 自存格式一致）
    now = int(time.time() * 1_000_000)
    os.makedirs(os.path.join(stage, "Timelines", tid))
    for p in (os.path.join(stage, "draft_info.json"), os.path.join(stage, "Timelines", tid, "draft_info.json")):
        json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"config": {"color_space": -1, "hdr_vivid": False, "mixed_track_mode_on": False,
                          "render_index_track_mode_on": False, "use_float_render": False},
               "create_time": now, "id": U(), "main_timeline_id": tid,
               "timelines": [{"create_time": now, "id": tid, "is_marked_delete": False, "name": "时间线01",
                              "update_time": now}], "update_time": now, "version": 0},
              open(os.path.join(stage, "Timelines", "project.json"), "w"), ensure_ascii=False)
    json.dump({"dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [tid], "timelineNames": ["时间线01"]}],
               "layoutOrientation": 1}, open(os.path.join(stage, "timeline_layout.json"), "w"), ensure_ascii=False)
    meta = json.load(open(os.path.join(vt, "draft_meta_info.json"), encoding="utf-8"))
    size = sum(os.path.getsize(os.path.join(res, f)) for f in os.listdir(res))
    records = []
    for src, (path, mid, info, kind) in media.items():
        records.append({"ai_group_type": "", "create_time": now // 1_000_000, "duration": us(info["duration"]),
                        "enter_from": 0, "extra_info": os.path.basename(path), "file_Path": path,
                        "height": info.get("height", 0), "id": str(uuid.uuid4()), "import_time": now // 1_000_000,
                        "import_time_ms": now, "item_source": 1, "material_color_tag": "", "md5": "",
                        "metetype": "video" if kind == "video" else "music",
                        "roughcut_time_range": {"duration": us(info["duration"]), "start": 0},
                        "sub_time_range": {"duration": -1, "start": -1}, "type": 0, "width": info.get("width", 0)})
    meta.update(draft_id=U(), draft_fold_path=final, draft_root_path=ROOT, draft_name=name,
                tm_draft_create=now, tm_draft_modified=now, tm_duration=us(total),
                draft_timeline_materials_size_=size, draft_cover="draft_cover.jpg")
    for grp in meta["draft_materials"]:
        grp["value"] = records if grp["type"] == 0 else []
    json.dump(meta, open(os.path.join(stage, "draft_meta_info.json"), "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(stage, "draft_settings"), "w").write(
        f"[General]\ndraft_create_time={now // 1_000_000}\ndraft_last_edit_time={now // 1_000_000}\n"
        "real_edit_keys=0\nreal_edit_seconds=0\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", tl["outputs"][0], "-frames:v", "1",
                    "-vf", "scale=360:-2", os.path.join(stage, "draft_cover.jpg")], check=True)
    info = dict(draft_id=meta["draft_id"], fold=final, duration=us(total), size=size, tm=now,
                templates=[vt, at], tracks=[(t["type"], t.get("name", ""), len(t["segments"])) for t in out["tracks"]])
    json.dump(info, open(os.path.join(stage, "..", f"{name}.install.json"), "w"), ensure_ascii=False, indent=1)
    return stage, info


def check(stage):
    """自检：段首尾相接（微秒级无缝/无重叠）、引用的素材都存在、媒体文件在 Resources 里、时长一致。"""
    j = _load(stage)
    ids = _by_id(j)
    errs = []
    if (j.get("config") or {}).get("video_mute"):
        errs.append("主轨原声被关闭（config.video_mute），CapCut 里整片没有画内声")
    for t in j["tracks"]:
        if t.get("attribute", 0) or any(sg.get("track_attribute", 0) for sg in t["segments"]):
            errs.append(f"{t['type']} 轨 {t.get('name', '')} 带非零 attribute（会被静音/锁定）")
        segs = sorted(t["segments"], key=lambda s: s["target_timerange"]["start"])
        for a, b in zip(segs, segs[1:]):
            ea = a["target_timerange"]["start"] + a["target_timerange"]["duration"]
            if t["type"] == "video" and ea != b["target_timerange"]["start"]:
                errs.append(f"视频段接缝 {ea} ≠ {b['target_timerange']['start']}")
            if ea > b["target_timerange"]["start"]:
                errs.append(f"{t['type']} 段重叠 @ {ea}")
        for sg in segs:
            for r in [sg["material_id"]] + sg["extra_material_refs"]:
                if r not in ids:
                    errs.append(f"缺素材 {r}")
            k, m = ids.get(sg["material_id"], (None, {}))
            st = sg["source_timerange"]
            if m and st and st["start"] + st["duration"] > m.get("duration", 1 << 62) + 1000:
                errs.append(f"源区间超出素材时长：{os.path.basename(m['path'])}")
    for k in ("videos", "audios"):
        for m in j["materials"].get(k, []):
            if not os.path.exists(os.path.join(stage, "Resources", os.path.basename(m["path"]))):
                errs.append(f"媒体未打包：{m['path']}")
    v = [t for t in j["tracks"] if t["type"] == "video"][0]["segments"]
    end = max(s["target_timerange"]["start"] + s["target_timerange"]["duration"] for s in v)
    if abs(end - j["duration"]) > 40000:
        errs.append(f"视频轨长度 {end} 与草稿时长 {j['duration']} 不一致")
    return errs


def capcut_running():
    return bool(subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True, text=True).stdout.strip())


def install(work, name):
    if capcut_running():
        raise RuntimeError("CapCut 正在运行，请先 Cmd+Q 完全退出再安装")
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(name)
    stage = os.path.join(work, "capcut", name)
    info = json.load(open(os.path.join(work, "capcut", f"{name}.install.json"), encoding="utf-8"))
    target = os.path.join(ROOT, name)
    if os.path.exists(target):
        raise RuntimeError(f"草稿库里已有同名草稿：{target}（不覆盖，请换名或先在 CapCut 里删掉）")
    root = json.load(open(ROOT_META, encoding="utf-8"))
    bak = ROOT_META + time.strftime(".%Y%m%d-%H%M%S.bak")
    shutil.copy2(ROOT_META, bak)
    shutil.copytree(stage, target)
    if target != info["fold"]:
        def rebase(value):
            if isinstance(value, dict):
                return {k: rebase(v) for k,v in value.items()}
            if isinstance(value, list):
                return [rebase(v) for v in value]
            if isinstance(value, str):
                if value == os.path.dirname(info["fold"]):
                    return ROOT
                if value.startswith(info["fold"] + os.sep) or value == info["fold"]:
                    return target + value[len(info["fold"]):]
                if value.startswith("{") and info["fold"] in value:
                    return json.dumps(rebase(json.loads(value)), ensure_ascii=False)
            return value
        for path in [*Path(target).rglob("draft_info.json"), Path(target)/"draft_meta_info.json"]:
            data = rebase(json.loads(path.read_bytes()))
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmpl = copy.deepcopy(next(iter(root["all_draft_store"]), {}))
    tmpl.update(draft_cover=os.path.join(target, "draft_cover.jpg"), draft_fold_path=target, draft_id=info["draft_id"],
                draft_json_file=os.path.join(target, "draft_info.json"), draft_name=name, draft_root_path=ROOT,
                draft_timeline_materials_size=info["size"], tm_draft_create=info["tm"], tm_draft_modified=info["tm"],
                tm_draft_removed=0, tm_duration=info["duration"], draft_is_invisible=False)
    root["all_draft_store"].insert(0, tmpl)
    if isinstance(root.get("draft_ids"), int):
        root["draft_ids"] += 1
    tmp = ROOT_META + ".tmp"
    json.dump(root, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, ROOT_META)
    return target, bak


if __name__ == "__main__":
    cmd, work, name = sys.argv[1:4]
    if cmd == "build":
        stage, info = build(work, name)
        print(stage); print(json.dumps(info, ensure_ascii=False, indent=1))
        errs = check(stage)
        print("自检：" + ("通过" if not errs else "\n".join(errs)))
        sys.exit(int(bool(errs)))
    elif cmd == "install":
        print(install(work, name))
