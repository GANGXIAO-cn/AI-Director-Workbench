"""Draft discovery is advisory; only an explicit reviewed association supplies boundaries."""
import math
from pathlib import Path

from common import CAPCUT_ROOT, load_roughcut_map
from source_versions import fingerprint, assert_fingerprint


def find_candidates(video, duration, draft_root=None):
    root = Path(draft_root or CAPCUT_ROOT).expanduser()
    if not root.is_dir():
        return []
    stem = Path(video).stem
    prefix = stem.split(' ')[0]
    candidates = []
    try:
        folders = sorted(root.iterdir(), key=lambda p: (p.name != stem, p.name))
    except OSError:
        return []
    for folder in folders:
        if not folder.is_dir() or not folder.name.startswith(prefix) or '精剪' in folder.name:
            continue
        try:
            segments = load_roughcut_map(str(folder))
            end = max(float(row['rough_end']) for row in segments)
            if abs(end - duration) < 0.1:
                candidates.append({'path': str(folder.resolve()), 'duration_s': end,
                                   'status': 'candidate_only',
                                   'reason': '仅名称前缀和时长近似，不证明与当前导出内容一致'})
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            continue
    return candidates


def associate(video, duration, draft=None, verified=False, note='', draft_root=None):
    if verified and (not draft or not isinstance(note, str) or not note.strip()):
        raise ValueError('使用草稿边界须明确--draft并填写--draft-verification-note实际核对依据')
    if note and not verified:
        raise ValueError('核对依据须与--draft-verified一起使用')
    candidates = find_candidates(video, duration, draft_root) if not draft else [
        {'path': str(Path(draft).expanduser().resolve()), 'status': 'candidate_only',
         'reason': '显式指定候选；尚未声明已核对内容'}]
    record = {'status': 'candidate_only' if candidates else 'not_associated',
              'candidates': candidates, 'boundaries_used': False}
    if not verified:
        return None, record
    folder = Path(draft).expanduser().resolve()
    version = fingerprint(folder / 'draft_info.json')
    segments = sorted(load_roughcut_map(str(folder)), key=lambda row: row['rough_start'])
    if not segments:
        raise ValueError('指定草稿没有可用主视频轨，继续用视频检测前先移除草稿关联参数')
    end = 0.0
    for row in segments:
        values = [row['rough_start'], row['rough_end'], row['file_start'], row['speed']]
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
            raise ValueError('草稿主轨时间/速度无效')
        if (abs(row['rough_start'] - end) > 0.001 or row['rough_start'] >= duration
                or row['rough_end'] <= row['rough_start']
                or row['file_start'] < 0 or row['speed'] <= 0):
            raise ValueError('草稿主视频轨有空隙、重叠或无效范围，不能用作当前粗剪边界')
        end = row['rough_end']
    if abs(end - duration) >= 0.1:
        raise ValueError('草稿主轨时长与当前粗剪不一致，不使用其边界')
    assert_fingerprint(folder / 'draft_info.json', version)
    record.update(status='human_verified', path=str(folder), draft_fingerprint=version,
                  verification_note=note.strip(), boundaries_used=True,
                  scope='只记录人工核对声明、时间与文件版本；不证明视频逐帧对应或实际进行了核对')
    return segments, record
