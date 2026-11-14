"""Checks of declared production evidence, ported from the frozen short-video skill.

Source commit ab0fa75e3b2254a9341a8501a48be7577ab023eb: validate_episode,
character_contract and review_evidence. All media and records come from the receipt.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct
from .specs import grid_dimensions, frame_geometry

from . import rhythm, readable, promptreceipt, depictions, shotrefs, sceneprovenance, promptsignals


LABEL_PATTERN = r'(?<![A-Za-z0-9_])(?:图片\s*[0-9]+|视频\s*[0-9]+|音频\s*[0-9]+|@(?:Image|Video|Audio)[0-9]+|<(?:Picture|Video|Audio)\s+[0-9]+>)'


def check_complete_standard(archive, segment, text):
    if "promptStandard" not in archive.metadata["standards"]:
        raise ValueError("导入时没有可核对的项目视频提示词标准")
    current = json.loads(archive.store.read_blob(archive.metadata["standards"]["promptStandard"]["sha256"]))
    if current.get('schema_version') != 1:
        raise ValueError('未知视频提示词标准版本')
    if segment['reference_strategy'] == 'flex':
        definitions, body = text.split('【基本设定】', 1)
        settings, body = body.split('【开场】', 1)
        sound = body.split('【声音与文字】', 1)[1]
        regions = {'reference_use': definitions, 'visual_style': settings, 'motion': settings,
                   'cats_silent': sound, 'frame_text': sound}
    else:
        definitions = text.split('【主角】', 1)[0]
        opening = text.split('【环境】', 1)[1].split('摘要与衔接：', 1)[0]
        sound = text.split('声音与音乐：', 1)[1].split('画面文字：', 1)[0]
        regions = {'reference_use': definitions, 'grid_reference': definitions,
                   'visual_style': opening, 'motion': opening, 'cats_silent': sound,
                   'frame_text': text.split('画面文字：', 1)[1]}
    if not set(archive.metadata.get('silentRoles', ())) & set(segment.get('on_screen_roles', [])):
        regions.pop('cats_silent', None)
    for key, region in regions.items():
        block = current.get('blocks', {}).get(key, {})
        if not block.get('version') or not block.get('zh'):
            raise ValueError(f'视频标准缺失：{key}')
        if text.count(block['zh']) != 1 or region.count(block['zh']) != 1:
            raise ValueError(f'视频标准须在对应栏目逐字且仅引用一次：{key}')
    return {'status': 'passed', 'blocks': list(regions),
            'scope': '仅文件标准及引用位置检查；不证明实际风格/运动或声音'}


def appearance(archive, data):
    """Check the episode's declared on-screen roles against the frozen project standard."""
    standard = archive.metadata["standards"].get("characterStandard")
    if not standard:
        raise ValueError("导入时没有可核对的项目角色外观标准")
    current = json.loads(archive.store.read_blob(standard["sha256"]))
    roles = current.get("roles", {})
    cast = data.get("cast", {})
    locked = archive.metadata["fixed"]
    assets = {a["id"]: a for a in data["assets"]}
    for segment in data["segments"]:
        present = segment.get("on_screen_roles")
        if (not isinstance(present, list) or any(not isinstance(r, str) or r not in roles and r not in cast for r in present)
                or len(set(present)) != len(present)):
            raise ValueError(f'{segment["id"]}：on_screen_roles缺失或含未知/重复角色')
        text = archive.text(segment["prompt"])
        flex = segment["reference_strategy"] == "flex"
        if flex:
            region = text.split("【基本设定】", 1)[0]
        else:
            region = text.partition("【主角】")[2].partition("【环境】")[0]
        for rid in present:
            if data.get("character_contract_version") == 1 and rid in cast and rid not in locked:
                continue
            role, lock = roles.get(rid), locked.get(rid)
            if not role or not lock:
                raise ValueError(f"角色标准或基准缺项：{rid}")
            for key in ("role_version", "sha256", "video_zh", "video_zh_version"):
                if not isinstance(role.get(key), str) or not role[key].strip():
                    raise ValueError(f"{rid}视频短标准缺项：{key}")
            if role["role_version"] != lock["version"] or role["sha256"] != lock["sha256"]:
                raise ValueError(f"{rid}短标准不匹配现行角色图")
            block = role["video_zh"]
            if text.count(block) != 1 or region.count(block) != 1:
                raise ValueError(f"{rid}视频短标准须在角色定义区逐字且仅引用一次")
            if flex:
                refs = [r for r in segment["references"] if assets[r["asset_id"]].get("fixed_role") == rid]
                if len(refs) != 1 or refs[0]["label"] + "：" + block not in region.splitlines():
                    raise ValueError(f"{rid}短标准未绑定本段实际角色输入")
        for pair_id, pair in current.get("pair_rules", {}).items():
            if set(pair_id.split("+")) <= set(present):
                block = pair.get("video_zh")
                if not block or not pair.get("video_zh_version"):
                    raise ValueError("同框视频短标准缺失")
                if text.count(block) != 1 or region.count(block) != 1:
                    raise ValueError("同框视频短标准须在定义区逐字且仅引用一次")
    return dict(segments=len(data["segments"]), standardSha256=standard["sha256"])


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'非法时间数值：{value}')
    return value


def required_text(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'缺少文本字段：{key}')
    return value


def image_size(name, raw):
    # Probe metadata only. This does not establish full decoding or visual review.
    if raw[:8] == b'\x89PNG\r\n\x1a\n' and len(raw) >= 24:
        return struct.unpack('>II', raw[16:24])
    if raw[:3] == b'\xff\xd8\xff' or (raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'):
        return None
    raise ValueError(f'无法识别图片文件头：{Path(name).name}')


def v2_assets(archive, data):
    records = data.get('assets', [])
    if not isinstance(records, list) or not records:
        raise ValueError('缺少资产表')
    result, files = {}, set()
    for asset in records:
        aid = required_text(asset, 'id')
        if aid in result:
            raise ValueError('重复资产ID')
        kind = asset.get('type')
        if kind not in ('image', 'video', 'audio'):
            raise ValueError('非法资产媒体类型')
        if asset.get('category') not in ('character', 'scene', 'prop', 'state', 'grid', 'motion', 'sound'):
            raise ValueError('非法资产类别')
        for field in ('purpose', 'source', 'version', 'usage'):
            required_text(asset, field)
        if asset.get('origin') not in ('reuse', 'new') or asset.get('review_status') != 'passed':
            raise ValueError('资产来源方式非法或未审核')
        required_text(asset, 'path')
        path, raw = archive.asset(asset)
        if path in files:
            raise ValueError('同一文件不能重复注册为多个资产')
        files.add(path)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != asset.get('sha256'):
            raise ValueError(f'资产指纹不符：{aid}')
        if kind == 'image':
            size = image_size(path, raw)
            if size is not None and (size[0] <= 0 or size[1] <= 0):
                raise ValueError('图片尺寸非法')
        result[aid] = (asset, path)
    return result


def segment_inputs(archive, data, assets, *, check_prompts=False):
    segments = data.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('缺少生成段')
    elapsed = 0
    ids, prompts = set(), set()
    summary = []
    for seg in segments:
        ident = required_text(seg, 'id')
        if ident in ids:
            raise ValueError('重复段号')
        ids.add(ident)
        duration, start = number(seg['duration_s']), number(seg['start_s'])
        if not 0 < duration <= 15 or not math.isclose(start, elapsed, rel_tol=0, abs_tol=1e-6):
            raise ValueError('时间不连续或本段时长不在(0,15]内')
        strategy = seg.get('reference_strategy')
        if strategy not in ('grid', 'flex'):
            raise ValueError('非法reference_strategy')
        style = seg.get('label_style', 'chinese')
        prefixes = {'chinese': {'image': '图片', 'video': '视频', 'audio': '音频'},
                    'at': {'image': '@Image', 'video': '@Video', 'audio': '@Audio'}}
        if style not in prefixes:
            raise ValueError('未知label_style；使用前需明确入口标签约定')
        refs = seg.get('references')
        if not isinstance(refs, list) or not refs:
            raise ValueError('缺少实际输入')
        counts = {'image': 0, 'video': 0, 'audio': 0}
        labels, used_paths, used_hashes = set(), set(), set()
        for order, ref in enumerate(refs, 1):
            if type(ref.get('upload_order')) is not int or ref['upload_order'] != order:
                raise ValueError('输入上传顺序不连续或与列表不一致')
            if ref.get('asset_id') not in assets:
                raise ValueError('引用未声明资产')
            asset, file = assets[ref['asset_id']]
            kind = asset['type']; counts[kind] += 1
            label = required_text(ref, 'label')
            if label in labels:
                raise ValueError('重复标签')
            if label != prefixes[style][kind] + str(counts[kind]):
                raise ValueError('输入顺序与标签不一致')
            if file in used_paths or asset['sha256'] in used_hashes:
                raise ValueError('同段重复输入同一文件或相同内容')
            labels.add(label); used_paths.add(file); used_hashes.add(asset['sha256'])
            required_text(ref, 'purpose'); required_text(ref, 'exclusions')
            shots = ref.get('shots')
            if not isinstance(shots, list) or not shots or any(not isinstance(x, str) or not x.strip() for x in shots):
                raise ValueError('缺少作用镜头')
        if not 1 <= counts['image'] <= 9:
            raise ValueError('每段图片必须为1至9张，9是硬上限')
        if counts['video'] or counts['audio']:
            limits = seg.get('verified_input_limits', {})
            required_text(limits, 'source')
            for kind in ('image', 'video', 'audio'):
                limit = limits.get(kind)
                if type(limit) is not int or limit < counts[kind]:
                    raise ValueError('实际入口媒体数量限制未核对或已超限')
            if type(limits.get('total')) is not int or limits['total'] < len(refs):
                raise ValueError('实际入口混合总限制未核对或已超限')
        txt = archive.local(seg["prompt"])
        if txt in prompts:
            raise ValueError("每段须独立提示词文件")
        prompts.add(txt)
        if check_prompts:
            text = archive.text(txt)
            if seg.get("prompt_format") != "readable_v3":
                raise ValueError(f'{seg["id"]}：须声明 prompt_format=readable_v3')
            headings = readable.GRID if strategy == 'grid' else readable.FLEX
            if any(text.count(h) != 1 for h in headings):
                raise ValueError('缺少或重复约定中文段落')
            positions = [text.index(h) for h in headings]
            if positions != sorted(positions):
                raise ValueError('提示词章节顺序错误')
            definitions = text.split(headings[1])[0]
            body = text.split(headings[1], 1)[1]
            if re.search(r'<Subject\s|@Subject\d', text):
                raise ValueError('未声明的主体标签；按本段输入标签引用')
            tokens = {re.sub(r'\s+', '', x) for x in re.findall(LABEL_PATTERN, text)}
            if tokens != labels:
                raise ValueError('提示词引用未声明标签或缺少输入引用')
            for ref in refs:
                label = ref['label']
                if strategy == 'flex':
                    matches = re.findall(r'^' + re.escape(label) + r'：([^\n]*)$', definitions, re.M)
                    if len(matches) != 1 or not matches[0].strip(' ；;。'):
                        raise ValueError('参考标签必须有且只有一个非空定义')
                if not re.search(re.escape(label) + r'(?!\d)', definitions):
                    raise ValueError('参考定义缺少输入标签')
            if strategy == 'grid':
                if len(refs) != 1 or assets[refs[0]['asset_id']][0]['category'] != 'grid':
                    raise ValueError('grid必须仅输入一张九宫格')
                frames = [number(x) for x in seg['keyframes_s']]
                if len(frames) != 9 or frames[0] != 0 or frames[-1] > duration or any(y <= x for x,y in zip(frames,frames[1:])):
                    raise ValueError('grid须有9个合法关键状态')
                _, raw = archive.asset(assets[refs[0]["asset_id"]][0])
                grid_dimensions(raw, data["aspect_ratio"])
            codes = re.findall(r'(?<!\d)(\d{2}):([0-5]\d)\.(\d{3})(?!\d)', text)
            seconds = [int(a)*60+int(b)+int(c)/1000 for a,b,c in codes]
            if not seconds or min(seconds) != 0 or not math.isclose(max(seconds), duration, rel_tol=0, abs_tol=.0005):
                raise ValueError('时间码未覆盖本段或超出段长')
            readable.check_flat_text(text, strategy, round(duration*1000), seg["id"], h3_rules=True)
            readable.check_h3_sound(seg, text)
            readable.sections(text,strategy)
            check_complete_standard(archive, seg, text)
            if seg.get('review_status') != 'passed':
                raise ValueError('本段未审核')
        elapsed += duration
        summary.append(dict(id=ident, counts=counts, duration=duration))
    return elapsed, summary


def review_evidence(archive, data):
    reference = data.get('review_evidence')
    if reference is None:
        raise ValueError('缺少review_evidence审核记录索引')
    if data.get("schema_version") != 2:
        raise ValueError("须使用 schema_version=2")
    assets = dict.fromkeys(a["id"] for a in data["assets"])
    segments = dict.fromkeys(s["id"] for s in data["segments"])
    evidence = json.loads(archive.text(reference))
    if evidence.get('schema_version') != 1 or evidence.get('episode_id') != data['episode_id']:
        raise ValueError('审核证据版本或episode_id不匹配')
    for kind, expected in [('asset_reviews', assets), ('segment_reviews', segments)]:
        records = evidence.get(kind)
        if not isinstance(records, dict) or set(records) != set(expected):
            raise ValueError(f'{kind}缺项或包含未知对象')
        for ident in expected:
            record = records[ident]
            if record.get('status') != 'passed':
                raise ValueError(f'{kind}/{ident}尚未审核通过')
            if not isinstance(record.get('scope'), str) or not record['scope'].strip():
                raise ValueError(f'{kind}/{ident}缺少实际检查范围')
            archive.local(record.get('evidence_path'))
    issues = evidence.get('issues')
    if not isinstance(issues, list):
        raise ValueError('issues须为数组，即使没有问题也须明确为空')
    seen = set()
    user_directed_issues = []
    for issue in issues:
        ident = issue.get('id')
        if not isinstance(ident, str) or not ident.strip() or ident in seen:
            raise ValueError('审核问题ID为空或重复')
        seen.add(ident)
        if issue.get('severity') not in ('critical', 'minor') or issue.get('status') not in ('open', 'resolved', 'accepted'):
            raise ValueError('审核问题的severity/status非法')
        if not isinstance(issue.get('description'), str) or not issue['description'].strip():
            raise ValueError('审核问题缺少具体描述')
        affected_segments, affected_assets = issue.get('segments', []), issue.get('assets', [])
        if (not isinstance(affected_segments, list) or not isinstance(affected_assets, list)
                or not (affected_segments or affected_assets)
                or any(s not in segments for s in affected_segments)
                or any(a not in assets for a in affected_assets)):
            raise ValueError('审核问题缺少有效影响范围')
        archive.local(issue.get('evidence_path'))
        direction = issue.get('user_direction')
        user_directed = False
        if direction is not None:
            if (not isinstance(direction, dict) or direction.get('stage') != 'closing_manual_revision'
                    or not isinstance(direction.get('instruction'), str) or not direction['instruction'].strip()):
                raise ValueError(f'用户手工修订缺少明确指令：{ident}')
            record = archive.local(direction.get('evidence_path'))
            if direction['instruction'] not in archive.text(record):
                raise ValueError(f'用户手工修订指令未记入依据：{ident}')
            user_directed = issue['status'] == 'accepted'
            if user_directed:
                user_directed_issues.append(ident)
        if issue['severity'] == 'critical' and issue['status'] != 'resolved' and not user_directed:
            raise ValueError(f'关键缺陷未解决：{ident}；不得作为小限制接受')
        if issue['severity'] == 'minor' and issue['status'] == 'open':
            raise ValueError(f'轻微问题尚未处置：{ident}；须修复或明确接受并记录理由')
    result = {'status': 'passed', 'assets': len(assets), 'segments': len(segments),
            'scope': '核对检查声明与记录关联'}
    if user_directed_issues:
        result['user_directed_issues'] = user_directed_issues
    return result


def character_contract(archive, data):
    fixed = archive.metadata["fixed"]
    if type(data.get('character_contract_version')) is not int or data['character_contract_version'] != 1:
        raise ValueError('须声明 character_contract_version=1')
    if data.get('schema_version') != 2:
        raise ValueError('角色契约仅支持清单 v2')
    cast = data.get('cast')
    if not isinstance(cast, dict) or not cast:
        raise ValueError('缺少非空 cast 角色登记')
    assets = {a['id']: a for a in data['assets']}
    owners = {}
    for rid, role in cast.items():
        if not re.fullmatch(r'[a-z][a-z0-9-]*', rid) or not isinstance(role, dict):
            raise ValueError('角色 ID 或登记无效')
        if not isinstance(role.get('name'), str) or not role['name'].strip() or type(role.get('identity_required')) is not bool:
            raise ValueError(rid + '缺角色名称或 identity_required 布尔值')
        if rid in fixed and not role['identity_required']:
            raise ValueError(rid + '固定角色不得豁免身份基准')
        if not role['identity_required']:
            if not isinstance(role.get('reason'), str) or not role['reason'].strip():
                raise ValueError(rid + '免独立身份基准须说明理由')
            continue
        aid = role.get('identity_asset_id')
        a = assets.get(aid)
        if not a or a.get('type') != 'image' or a.get('category') != 'character':
            raise ValueError(rid + '缺独立角色基准资产')
        if aid in owners or a.get('sha256') in owners:
            raise ValueError(rid + '与另一角色共用同一身份基准')
        owners[aid] = owners[a.get('sha256')] = rid
        if rid in fixed:
            if a.get('fixed_role') != rid:
                raise ValueError(rid + '固定身份基准错配')
        else:
            if a.get('fixed_role') or a.get('depicts_roles') != [rid] or a.get('depicts_moment', 'missing') is not None:
                raise ValueError(rid + '须使用单角色、无剧情时刻的身份图')
            if not isinstance(role.get('appearance'), str) or not role['appearance'].strip():
                raise ValueError(rid + '缺本集外观短描述')
        raw = archive.identity_bytes(a)
        if hashlib.sha256(raw).hexdigest() != a.get('sha256'):
            raise ValueError(rid + '身份基准指纹不符')
    for a in assets.values():
        if any(r not in cast for r in a.get('depicts_roles', [])):
            raise ValueError(a['id'] + '图片包含未登记角色')
    for s in data['segments']:
        sid = s['id']; roles = s.get('on_screen_roles')
        if not isinstance(roles, list) or any(not isinstance(r, str) or r not in cast for r in roles) or len(set(roles)) != len(roles):
            raise ValueError(sid + '缺实际出镜角色或含未知/重复角色')
        bindings = s.get('identity_bindings')
        expected = {r for r in roles if cast[r]['identity_required']}
        if not isinstance(bindings, dict) or set(bindings) != expected:
            raise ValueError(sid + '身份绑定须覆盖且仅覆盖需要基准的出镜角色')
        refs = {r['asset_id']: r for r in s['references']}
        for aid in refs:
            a = assets.get(aid, {})
            if a.get('type') == 'image' and a.get('depicts_moment') is not None and not set(a.get('depicts_roles', [])).issubset(roles):
                raise ValueError(sid + '实际剧情图片含未声明出镜角色')
        text = archive.text(s['prompt'])
        for rid, aid in bindings.items():
            if aid not in refs or aid not in assets:
                raise ValueError(sid + '/' + rid + '身份绑定未实际上传')
            a = assets[aid]
            if rid not in fixed and cast[rid]['appearance'] not in text:
                raise ValueError(sid + '/' + rid + '提示词缺本集外观短描述')
            if aid == cast[rid]['identity_asset_id']:
                continue
            if (a.get('category') not in ('state', 'grid') or rid not in a.get('depicts_roles', [])
                    or a.get('depicts_moment') != {'segment': sid, 'at': 'start'}):
                raise ValueError(sid + '/' + rid + '身份来源不是本段可用状态图/九宫格')
            review = s.get('identity_reviews', {}).get(rid, {})
            if not isinstance(review, dict) or not isinstance(review.get('observation'), str) or not review['observation'].strip():
                raise ValueError(sid + '/' + rid + '缺与基准的实际视觉对照记录')
            evidence = archive.text(review.get('evidence_path'))
            if review['observation'] not in evidence:
                raise ValueError(sid + '/' + rid + '对照观察未落入证据文件')
    return {'status': 'passed', 'roles': len(cast), 'scope': '结构、字节和实际输入映射；不证明身份视觉一致'}

def archived_sound(archive, data):
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("缺少生成段")
    rows = []
    for segment in segments:
        item = dict(segment=segment.get("id"), nativeBgm=segment.get("native_bgm", "none"),
                    fullSilence=segment.get("full_silence", False), prompt=segment.get("prompt"))
        try:
            raw = archive.read(segment["prompt"])
            item["sha256"] = hashlib.sha256(raw).hexdigest()
            readable.check_h3_sound(segment, raw.decode("utf-8"))
        except (ValueError, KeyError, OSError, TypeError) as exc:
            item.update(state="failed", message=str(exc))
        else:
            item["state"] = "passed"
        rows.append(item)
    failed = sum(item["state"] == "failed" for item in rows)
    marker = data.get("h3_rules") == "v1"
    return dict(state="failed" if failed or not marker else "passed",
                message=("" if marker else "须声明 h3_rules=v1；") + f"{len(rows)} 段声音声明，{failed} 段未通过",
                details=dict(marker=data.get("h3_rules"), segments=rows))


def assess(archive, data):
    """Report each applicable check even when an earlier whole-manifest check failed."""
    checks = {}
    def record(key, operation):
        try:
            result = operation()
        except (ValueError, TypeError, KeyError, OSError, AttributeError, ArithmeticError) as exc:
            checks[key] = dict(state="failed", message=str(exc) or type(exc).__name__)
            return None
        checks[key] = {"state":"passed", **result}
        return result

    if not isinstance(data, dict):
        return {"manifest":dict(state="failed", message="集清单须为 JSON 对象")}
    if data.get("schema_version") == 2:
        from . import archivepage
        if archivepage.PAGE not in archive.files:
            checks["pageContent"] = dict(state="failed",message="缺少图文交付页")
        else:
            record("pageContent", lambda: archivepage.checked_content(archive,data))
    if "imageInventory" in archive.metadata:
        from .imageinventory import inventory
        def image_inventory():
            details = inventory(archive,data)
            message = f"{details['current_images']} 个当前图片版本，排除 {len(details['excluded_projects'])} 个独立子集"
            return dict(message=message,details=details)
        checked = record("imageInventory",image_inventory)
        if checked and checked["details"]["problems"]:
            checks["imageInventory"].update(state="failed",message="；".join(checked["details"]["problems"]))
    if data.get("schema_version") == 2:
        record("h3Sound", lambda: archived_sound(archive, data))
        def aspect():
            details = frame_geometry(data, lambda asset: archive.asset(asset)[1])
            return dict(message=f"本集 {details['target']}，核对 {details['segments']} 段生成设置、"
                                f"{len(details['grids'])} 张九宫格原图", details=details)
        record("aspect", aspect)
    if "assets" in data:
        assets = {}
        def check_assets():
            assets.update(v2_assets(archive, data))
            return dict(message=f"{len(assets)} 个素材的登记、媒体文件头和指纹通过", count=len(assets))
        if record("assets", check_assets) is not None:
            def inputs():
                duration, segments = segment_inputs(archive, data, assets)
                if not math.isclose(duration, number(data["total_duration_s"]), rel_tol=0, abs_tol=1e-6):
                    raise ValueError("段落时长之和不等于总时长")
                return dict(message=f"{len(segments)} 段实际输入、时间与独立 TXT 路径通过", segments=segments)
            record("inputs", inputs)
            def prompts():
                _, checked = segment_inputs(archive,data,assets,check_prompts=True)
                return dict(message=f"{len(checked)} 段提示词的格式、实际引用、时窗和项目标准通过",count=len(checked))
            record("prompts", prompts)
        else:
            checks["inputs"] = dict(state="incomplete", message="素材登记未通过，无法核对分段实际输入")
    else:
        checks["assets"] = dict(state="not_declared",message="历史清单未登记 v2 素材表")
    for name, declared, fn, title in [
        ("appearance", data.get("schema_version") == 2, appearance, "出镜角色与项目外观标准"),
        ("cast", data.get("schema_version") == 2 or "cast" in data or "character_contract_version" in data, character_contract, "角色契约与实际绑定"),
        ("depictions", data.get("schema_version") == 2 or "depicts_version" in data, depictions.check_depictions, "参考图主体、开场时刻与场景锁定图"),
        ("reviews", data.get("schema_version") == 2 or "review_evidence" in data, review_evidence, "审核索引、问题处置及依据"),
        ("promptReview", (data.get("schema_version") == 2 and data.get("workbench",{}).get("flow") != "storyboard-translation-v1") or "prompt_review" in data, lambda files, manifest:promptreceipt.verify(
            manifest, files.read, files.identity_bytes), "提示词独审原件与输入顺序"),
        ("shotReferences", data.get("schema_version") == 2 or shotrefs.enabled(data), lambda files, manifest:shotrefs.check_binding_structure(files.text,manifest), "继承的逐镜角色与素材绑定结构"),
        ("rhythm", data.get("schema_version") == 2 or "shot_rhythm" in data, lambda files, manifest:rhythm.packaged_report(manifest,files.text), "联合表与分段节奏"),
    ]:
        if declared:
            record(name, lambda:dict(message=title+"核对通过", details=fn(archive,data)))
        else:
            checks[name] = dict(state="not_declared",message="历史清单未声明"+title)
    if data.get("schema_version") == 2:
        try:
            details = promptsignals.archived(archive,data)
            state = "not_declared"
            if details["inputs"]:
                state = "advisory" if details["warnings"] or details["skipped"] else "checked"
            checks["promptSignals"] = dict(state=state,details=details,
                message=f"已扫描 {len(details['inputs'])} 段，定位 {len(details['warnings'])} 条线索，"
                        f"{len(details['skipped'])} 段缺少适用材料")
        except (ValueError, TypeError, KeyError, OSError, AttributeError, ArithmeticError) as exc:
            checks["promptSignals"] = dict(state="incomplete",message=f"无法读取提示词或冻结的标准：{exc}")
        if archive.metadata.get("version", 1) < 2:
            checks["sceneAnchors"] = dict(state="incomplete",message="此早期副本未采集场景生成记录，重新导入可核对")
        else:
            rule = archive.metadata.get("sceneBaselineRule")
            tasks = [("sceneAnchors", lambda:sceneprovenance.scene_anchor_warnings(data,archive.scene_bytes))]
            if rule:
                tasks.append(("sceneBaselines", lambda:sceneprovenance.baseline_warnings(data,rule,archive.scene_registry)))
            for key, inspect in tasks:
                try:
                    warnings = inspect()
                except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
                    checks[key] = dict(state="incomplete",message=f"场景来源记录无法核对：{exc}")
                    continue
                checks[key] = dict(state="advisory" if warnings else "checked", warnings=warnings,
                                   message=f"已核对登记材料，{len(warnings)} 条提示")
    return checks
