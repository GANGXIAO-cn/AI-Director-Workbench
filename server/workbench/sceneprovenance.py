"""Ported from frozen baseline_provenance.py at ab0fa75e3b2254a9341a8501a48be7577ab023eb."""
import json
import re
from .storage import sha256_bytes

REQUEST_LOG = "过程稿/实际生成输入版本.json"
REF = re.compile(r'asset-library:([a-z0-9-]+)@([A-Za-z0-9_.-]+)')


def baseline_warnings(data, settings, read_registry):
    if data.get('schema_version') != 2:
        raise ValueError('须声明 schema_version=2')
    candidates = used_scene_images(data)
    warnings = []

    def warn(a, code, message):
        warnings.append({'asset_id': a.get('id'), 'code': code, 'message': message})

    home = []
    for a in candidates:
        scope = a.get('scene_scope')
        if scope not in (settings['scope'], 'other'):
            warn(a, 'scene_scope_unchecked', '场景未标记或标记无效，未覆盖固定场景来源检查；不据图片或关键词推断归属。')
        elif scope == settings['scope']:
            home.append(a)
    if not home:
        return warnings
    try:
        fixed = read_registry('sceneBaselines')['baselines']
        library = read_registry('assetLibraryIndex')['assets']
        home_ids = {a['id'] for a in fixed if a['id'].startswith(settings['prefix'])}
        versions = {(a['id'], a['version']) for a in library}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        for a in home:
            warn(a, 'baseline_registry_unchecked', f'无法读取固定清单或复用库，来源未验证：{error}')
        return warnings
    for a in home:
        refs = REF.findall(a['source'] if isinstance(a.get('source'), str) else '')
        if not refs:
            warn(a, 'baseline_source_missing', '固定场景source缺少asset-library:ID@version；派生图也须列出实际场景基准。')
            continue
        for aid, version in refs:
            if (aid, version) not in versions:
                warn(a, 'baseline_version_unknown', f'复用库无此版本：{aid}@{version}')
        if not any(aid in home_ids and (aid, version) in versions for aid, version in refs):
            warn(a, 'home_baseline_missing', '来源未包含指定场景基准的已登记版本；普通道具或服装不能替代场景基准。')
    return warnings


def used_scene_images(data):
    def rows(value):
        return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []
    used = {r['asset_id'] for s in rows(data.get('segments')) for r in rows(s.get('references'))
            if isinstance(r.get('asset_id'), str)}
    return [a for a in rows(data.get('assets')) if isinstance(a.get('id'), str) and a['id'] in used
            and a.get('type') == 'image' and a.get('category') in ('scene', 'state', 'grid')]


def recorded_file(read, record):
    """Return a verified recorded hash; paths may be historical input copies."""
    if not isinstance(record, dict) or not isinstance(record.get('path'), str):
        raise ValueError('缺path')
    digest = record.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('缺有效sha256')
    if sha256_bytes(read(record['path'])) != digest:
        raise ValueError(f'文件与记录指纹不符：{record["path"]}')
    return digest


def scene_anchor_warnings(data, read):
    """Advisory only. Never infer scenes, requests, or adopted versions from prose."""
    if data.get('schema_version') != 2:
        raise ValueError('须声明 schema_version=2')
    images = used_scene_images(data)
    if not images:
        return []
    warnings = []

    def warn(code, message, **context):
        warnings.append(dict(code=code, message=message, **context))

    anchors = data.get('scene_anchors', {})
    if not isinstance(anchors, dict):
        anchors = {}
    grouped, unmapped = {}, []
    for asset in images:
        scenes = asset.get('scene_ids')
        if not isinstance(scenes, list) or not scenes or any(not isinstance(x, str) or not x for x in scenes):
            unmapped.append(asset.get('id'))
            continue
        for scene in dict.fromkeys(scenes):
            grouped.setdefault(scene, []).append(asset)
    if unmapped:
        warn('scene_mapping_unchecked', '未登记scene_ids；以下资产的锁定输入未覆盖，不猜场景归属。', asset_ids=unmapped)
    verified = {}
    for scene, assets in grouped.items():
        record = anchors.get(scene)
        if not isinstance(record, dict):
            warn('scene_anchor_unregistered', '场景未登记锁定图。', scene_id=scene, asset_ids=[a['id'] for a in assets])
            continue
        try:
            if not isinstance(record.get('responsibility'), str) or not record['responsibility'].strip():
                raise ValueError('缺锁定职责responsibility')
            verified[scene] = recorded_file(read, record)
        except (OSError, ValueError, TypeError) as error:
            warn('scene_anchor_invalid', str(error), scene_id=scene, asset_ids=[a['id'] for a in assets])
    # No request evidence is necessary when no registered scene can be checked.
    if not verified:
        return warnings
    try:
        records = json.loads(read(REQUEST_LOG))
        if not isinstance(records, dict):
            raise ValueError('请求记录须为对象')
    except (OSError, ValueError) as error:
        records = {}
        records_error = str(error)
    else:
        records_error = ''
    for asset in images:
        scenes = asset.get('scene_ids', [])
        if not isinstance(scenes, list):
            continue
        wanted = {s: verified[s] for s in scenes if isinstance(s, str) and s in verified}
        if not wanted:
            continue
        try:
            adopted = recorded_file(read, asset)
        except (OSError, ValueError, TypeError) as error:
            warn('scene_output_unchecked', str(error), asset_id=asset['id'])
            continue
        # An anchor's first adopted image or its edited replacement cannot reference itself.
        wanted = {s: h for s, h in wanted.items() if h != adopted}
        if not wanted:
            continue
        request = asset.get('generation_request')
        record = records.get(request) if isinstance(request, str) else None
        try:
            if not isinstance(record, dict):
                raise ValueError(records_error or '缺采用输出对应的generation_request记录')
            if recorded_file(read, record.get('output')) != adopted:
                raise ValueError('请求output不是当前采用版本')
            inputs = record.get('input_versions')
            if not isinstance(inputs, list):
                raise ValueError('缺input_versions')
            hashes = {recorded_file(read, item) for item in inputs}
        except (OSError, ValueError, TypeError) as error:
            warn('scene_inputs_unchecked', str(error), asset_id=asset['id'], request_id=request)
            continue
        for scene, digest in wanted.items():
            if digest not in hashes:
                warn('scene_anchor_missing', '采用版本的输入记录未包含此场景当前锁定图；旧版本或前态不能替代。',
                     asset_id=asset['id'], scene_id=scene, request_id=request)
    return warnings
