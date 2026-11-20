"""Byte-level source identity; does not establish creative or perceptual equivalence."""
import hashlib
import json
from pathlib import Path


def fingerprint(path):
    path = Path(path).expanduser().resolve()
    before = path.stat()
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(f'文件在读取中变化，请重新接收：{path}')
    return {'path': str(path), 'size_bytes': after.st_size, 'sha256': result.hexdigest()}


def assert_fingerprint(path, expected):
    actual = fingerprint(path)
    if actual['sha256'] != expected['sha256'] or actual['size_bytes'] != expected['size_bytes']:
        raise ValueError(f'粗剪/草稿版本已变化：{path}；须重新核对体检、时间定位和剪辑表，不可只刷新指纹')
    return actual


def checked_analysis_source(work, rough):
    """Rendering requires an analysis of the actual current source."""
    analysis = Path(work) / '体检/analysis.json'
    if not analysis.is_file():
        raise ValueError('缺少体检报告，请先运行analyze.py')
    expected = json.loads(analysis.read_text(encoding='utf-8')).get('source_fingerprint')
    if not expected:
        raise ValueError('体检报告缺少粗剪指纹，请重新体检')
    return assert_fingerprint(rough, expected)
