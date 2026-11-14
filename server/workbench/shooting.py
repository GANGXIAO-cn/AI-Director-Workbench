"""Production intent shared by the writer and continuous expert, without a new review stage."""
from . import timeline


def writing_problems(stage, content, script=None):
    """Feedback for the existing writing retry, not a new adoption/approval gate.

    Historical manuscripts without shootingSegments remain readable and adoptable.
    """
    segments = content.get('shootingSegments') if stage == 'script' else (script or {}).get('shootingSegments')
    if segments is None:
        return []
    if not isinstance(segments, list) or not segments or any(not isinstance(s, dict) for s in segments):
        return ['shootingSegments 应为实际拍摄段落数组']
    problems = []
    ids = [s.get('id') for s in segments]
    if any(not isinstance(s, str) or not s for s in ids) or len(set(ids)) != len(ids):
        return ['拍摄段落须使用唯一段号']
    for s in segments:
        d = s.get('duration_ms')
        if type(d) is not int or d % timeline.SEGMENT_STEP_MS or not timeline.MIN_SEGMENT_MS <= d <= timeline.MAX_SEGMENT_MS:
            problems.append(f"{s['id']} 拍摄预算须为当前入口5–15秒整秒，调整自然段界，不填无效等待")
    if stage == 'plan':
        lay = timeline.layout(content)
        if [s['id'] for s in lay['segments']] != ids:
            problems.append('分镜须沿用剧本拍摄段号及顺序，不另按场次重新分段')
        expected = {s['id']: s.get('duration_ms') for s in segments}
        for s in lay['segments']:
            if s['duration_ms'] != expected.get(s['id']):
                problems.append(f"{s['id']} 段内窗口合计须对应剧本拍摄预算")
        for shot in content.get('shots', []):
            if type(shot.get('duration_ms')) is not int or shot['duration_ms'] not in (1000, 2000, 3000):
                problems.append(f"{shot['id']} 观看窗口须为1–3秒整秒；同镜可平铺延续，不强制切镜")
    return problems
