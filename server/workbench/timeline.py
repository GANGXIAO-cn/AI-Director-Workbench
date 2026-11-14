"""Shot / segment timing on an integer-millisecond base.

Cards, tables and the segment bar all read the same shot list: a segment's length is the sum of
its shots, segments run back to back in order, and the episode total is derived the same way.
"""
import copy

MIN_SEGMENT_MS = 5_000
MAX_SEGMENT_MS = 15_000
SEGMENT_STEP_MS = 1_000

def shooting_contract():
    return (f"剧本同时提供顶层 shootingSegments 数组：每项 id、sceneId、order、duration_ms、action、dialogue、sound、startState、endState。"
            f"当前提交入口每拍摄段 {MIN_SEGMENT_MS//1000}–{MAX_SEGMENT_MS//1000} 秒、{SEGMENT_STEP_MS//1000} 秒步长，不要求用满。"
            "完整 scenes 保留；按动作结果与对白语义先拆拍摄段，段落覆盖原剧情，不新增故事。短动作可合段，不加无效等待。"
            "分镜继承该段号和预算，再在段内展开观看窗口；提示词照分镜表达。锁定原文时仅补顶层制作拆分，不改 scenes 或准确对白。")



def layout(plan: dict) -> dict:
    segments = sorted(plan.get("segments", []), key=lambda s: s["order"])
    shots = plan.get("shots", [])
    by_seg = {s["id"]: [] for s in segments}
    for shot in sorted(shots, key=lambda s: (s.get("order", 0))):
        if shot["segment"] not in by_seg:
            raise ValueError(f"镜头 {shot['id']} 指向不存在的生成段 {shot['segment']}")
        by_seg[shot["segment"]].append(shot)
    clock, out_segments, out_shots = 0, [], {}
    for seg in segments:
        local = 0
        for shot in by_seg[seg["id"]]:
            out_shots[shot["id"]] = dict(segment=seg["id"], seg_start_ms=local, seg_end_ms=local + shot["duration_ms"],
                                         start_ms=clock + local, end_ms=clock + local + shot["duration_ms"])
            local += shot["duration_ms"]
        out_segments.append(dict(id=seg["id"], start_ms=clock, end_ms=clock + local, duration_ms=local,
                                 shots=[s["id"] for s in by_seg[seg["id"]]], strategy=seg.get("strategy", "flex")))
        clock += local
    return dict(segments=out_segments, shots=out_shots, total_ms=clock)


def script_budget(script, plan, segments):
    """Attribute each shot's milliseconds to its declared scene within the selected segments."""
    scenes = {scene["id"]: scene for scene in script.get("scenes", [])}
    source_text = {}
    for scene_id, scene in scenes.items():
        fields = (scene.get(k) for k in ("action", "performance", "choice", "response", "change",
                                         "payoff", "dialogue", "sound"))
        source_text[scene_id] = [line for field in fields for line in (field if isinstance(field, list) else [field])
                                 if isinstance(line, str)]
    shots = {shot["id"]: shot for shot in plan.get("shots", [])}
    rows, traced = [], set()
    for segment in segments:
        by_scene = {}
        for shot_id in segment["shots"]:
            shot = shots[shot_id]
            origin = shot.get("origin") if isinstance(shot.get("origin"), dict) else {}
            scene_id = origin.get("scene") if isinstance(origin.get("scene"), str) else None
            scene = scenes.get(scene_id)
            group = by_scene.setdefault(scene_id, dict(scene=scene_id, known=scene is not None,
                                                       duration_ms=0, shots=[], unverifiedQuotes=[]))
            group["duration_ms"] += shot["duration_ms"]
            group["shots"].append(shot_id)
            quote = origin.get("line")
            if scene is not None:
                traced.add(scene_id)
            if not isinstance(quote, str) or not quote.strip() or not any(quote in line for line in source_text.get(scene_id, [])):
                group["unverifiedQuotes"].append(shot_id)
        rows.append(dict(segment=segment["id"], episode_start_ms=segment["start_ms"],
                         duration_ms=segment["duration_ms"], scenes=list(by_scene.values())))
    return dict(segments=rows, total_ms=sum(r["duration_ms"] for r in rows),
                scenesOutsideSelection=[key for key in scenes if key not in traced])


def check(plan: dict, packaging: bool = False) -> list[dict]:
    """Structural problems as [{segment, problem}]; packaging adds the H3 5–15 s integer rule."""
    problems = []
    ids = [s["id"] for s in plan.get("shots", [])]
    if len(set(ids)) != len(ids):
        problems.append(dict(segment=None, problem="镜号重复"))
    seg_ids = [s["id"] for s in plan.get("segments", [])]
    if len(set(seg_ids)) != len(seg_ids):
        problems.append(dict(segment=None, problem="段号重复"))
    try:
        lay = layout(plan)
    except ValueError as exc:
        return problems + [dict(segment=None, problem=str(exc))]
    for seg in lay["segments"]:
        d = seg["duration_ms"]
        if d <= 0:
            problems.append(dict(segment=seg["id"], problem="生成段为空"))
        elif d > MAX_SEGMENT_MS:
            problems.append(dict(segment=seg["id"], problem=f"生成段 {d / 1000:g} 秒，超过 15 秒，须在动作完成处拆段"))
        elif packaging and (d % SEGMENT_STEP_MS or not MIN_SEGMENT_MS <= d <= MAX_SEGMENT_MS):
            problems.append(dict(segment=seg["id"], problem=f"打包要求 5–15 秒整数，当前 {d / 1000:g} 秒"))
    return problems


def preview(old: dict | None, new: dict) -> dict:
    """What changes if `new` is adopted instead of `old`: mapping, shifts, joints and downstream items."""
    new_lay = layout(new)
    if not old:
        return dict(totalBefore=None, totalAfter=new_lay["total_ms"], changedSegments=[s["id"] for s in new_lay["segments"]],
                    shifted=[], joints=[], removed=[], added=[s["id"] for s in new_lay["segments"]], downstream=[])
    old_lay = layout(old)
    old_seg = {s["id"]: s for s in old_lay["segments"]}
    new_seg = {s["id"]: s for s in new_lay["segments"]}
    old_shots = {s["id"]: s for s in old.get("shots", [])}
    new_shots = {s["id"]: s for s in new.get("shots", [])}
    old_records = {s['id']: s for s in old.get('segments', [])}
    new_records = {s['id']: s for s in new.get('segments', [])}
    changed = set()
    for sid in set(old_shots) | set(new_shots):
        a, b = old_shots.get(sid), new_shots.get(sid)
        if a != b:
            changed.add((b or a)["segment"])
            if a and b and a["segment"] != b["segment"]:
                changed.add(a["segment"])
    for sid in set(old_seg) & set(new_seg):
        if old_seg[sid]["shots"] != new_seg[sid]["shots"] or old_records[sid] != new_records[sid]:
            changed.add(sid)
    changed.update(set(new_seg) - set(old_seg))
    shifted = [dict(segment=sid, startBefore=old_seg[sid]["start_ms"], startAfter=new_seg[sid]["start_ms"])
               for sid in new_seg if sid in old_seg and old_seg[sid]["start_ms"] != new_seg[sid]["start_ms"]]
    order = [s["id"] for s in new_lay["segments"]]
    joints = sorted({tuple(order[i:i + 2]) for i, sid in enumerate(order[:-1])
                     if sid in changed or order[i + 1] in changed})
    removed = sorted(set(old_seg) - set(new_seg))
    added = sorted(set(new_seg) - set(old_seg))
    downstream = []
    for sid in sorted(changed - set(removed)):
        downstream += [dict(target=f"提示词 {sid}", reason="段内镜头或时长变化，TXT 时间句需复核"),
                       dict(target=f"关键帧 {sid}", reason="段内动作时刻变化")]
    if shifted or old_lay["total_ms"] != new_lay["total_ms"]:
        downstream += [dict(target="声音与音乐对位表", reason="全片时间变化"),
                       dict(target="发布包对位时间", reason="全片时间变化")]
    return dict(totalBefore=old_lay["total_ms"], totalAfter=new_lay["total_ms"], changedSegments=sorted(changed),
                shifted=shifted, joints=[list(j) for j in joints], removed=removed, added=added, downstream=downstream)


def split_segment(plan: dict, segment_id: str, first_shot_of_second: str) -> tuple[dict, dict]:
    """Split one segment before the given shot. Returns (new plan, id mapping)."""
    new = copy.deepcopy(plan)
    seg = next(s for s in new["segments"] if s["id"] == segment_id)
    shots = sorted([s for s in new["shots"] if s["segment"] == segment_id], key=lambda s: s["order"])
    idx = [s["id"] for s in shots].index(first_shot_of_second)
    if idx == 0:
        raise ValueError("拆分点须在段内动作完成处，不能是首镜")
    a, b = segment_id + "a", segment_id + "b"
    for s in new["segments"]:
        if s["order"] > seg["order"]:
            s["order"] += 1
    new["segments"].remove(seg)
    new["segments"] += [dict(seg, id=a), dict(seg, id=b, order=seg["order"] + 1)]
    for i, shot in enumerate(shots):
        shot["segment"] = a if i < idx else b
    return new, {segment_id: [a, b]}
