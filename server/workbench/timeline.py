"""Shot / segment timing on an integer-millisecond base.

Cards, tables and the segment bar all read the same shot list: a segment's length is the sum of
its shots, segments run back to back in order, and the episode total is derived the same way.
"""
import copy

MAX_SEGMENT_MS = 15_000


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
        elif packaging and (d % 1000 or not 5000 <= d <= 15000):
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
    changed = set()
    for sid in set(old_shots) | set(new_shots):
        a, b = old_shots.get(sid), new_shots.get(sid)
        if a != b:
            changed.add((b or a)["segment"])
            if a and b and a["segment"] != b["segment"]:
                changed.add(a["segment"])
    for sid in set(old_seg) & set(new_seg):
        if old_seg[sid]["shots"] != new_seg[sid]["shots"] or old_seg[sid]["strategy"] != new_seg[sid]["strategy"]:
            changed.add(sid)
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
