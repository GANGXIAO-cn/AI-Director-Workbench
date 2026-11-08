"""Read explicit review fields from Markdown; never infer semantic approval."""
import re

ALIASES = {
    '覆盖段': 'scope', '段号': 'scope', 'scope': 'scope',
    '阶段': 'stage', 'stage': 'stage',
    '冷读观察': 'cold', 'cold': 'cold',
    '对照结论': 'comparison', 'comparison': 'comparison',
    '观察': 'observation', 'observation': 'observation',
    '结论': 'verdict', 'verdict': 'verdict',
    '问题处理': 'issues', 'issues': 'issues',
    '审核者': 'reviewer', 'reviewer': 'reviewer',
    'TXT哈希': 'txt_sha256', 'txt_sha256': 'txt_sha256',
    '输入图片': 'images', 'images': 'images',
}


def clean(value):
    return value.strip().strip('`').strip('*').strip()


def key(value):
    return ALIASES.get(clean(value).replace(' ', ''))


def records(text):
    result, current, header = [], {}, None
    boundary, last = False, None
    def flush():
        nonlocal current, last, boundary
        if 'scope' in current or 'stage' in current:
            result.append(current)
        elif current:
            raise ValueError('审核记录缺少覆盖段或阶段，可能存在孤立字段或结论冲突')
        current = {}
        last, boundary = None, False
    for line in text.splitlines() + ['']:
        line = line.strip()
        if not line:
            boundary = True; header = None
            continue
        if line.startswith('|'):
            cells = [clean(x) for x in line.strip('|').split('|')]
            if all(re.fullmatch(r':?-{3,}:?', x) for x in cells):
                continue
            names = [key(x) for x in cells]
            if any(x in ('scope', 'stage') for x in names) and any(x == 'verdict' for x in names):
                known = [name for name in names if name]
                if len(known) != len(set(known)):
                    raise ValueError('审核表格字段重复或结论冲突')
                flush(); header = names
            elif header:
                if len(cells) != len(header):
                    raise ValueError('审核表格列数不一致')
                row = {k: v for k, v in zip(header, cells) if k}
                if 'scope' in row or 'stage' in row:
                    result.append(row)
            elif len(cells) == 2 and names[0]:
                if names[0] in current:
                    if boundary:flush()
                    else:raise ValueError('审核记录字段重复或结论冲突：' + names[0])
                current[names[0]] = cells[1]
                boundary, last = False, names[0]
            continue
        header = None
        # Heading depth and unrelated prose have no significance.
        match = re.match(r'^(?:#+\s*|[-*]\s+)?([^:：]+)[:：]\s*(.*)$', line)
        if match and key(match[1]):
            name, value = key(match[1]), clean(match[2])
            if name in current:
                if boundary:flush()
                else:raise ValueError('审核记录字段重复或结论冲突：' + name)
            current[name] = value
            boundary, last = False, name
        elif not line.startswith('#') and last in ('cold','comparison','observation'):
            current[last] += '\n' + line
    flush()
    return result


def require_record(row, fields):
    for name in fields:
        if not isinstance(row.get(name), str) or not row[name].strip():
            raise ValueError('审核记录缺少字段：' + name)
    if row.get('verdict') not in ('通过', '退回', 'PASS', 'RETURN'):
        raise ValueError('审核结论须为通过或退回')
    if row['verdict'] in ('退回', 'RETURN'):
        raise ValueError('审核退回，须处理问题并重新审核')
    # Explicit dispositions, not interpretation of the accompanying prose.
    disposition = re.split(r'[:：；;\n]', row.get('issues', ''), 1)[0].strip()
    if disposition not in ('无', '已解决', '已接受', 'none', 'resolved', 'accepted'):
        raise ValueError('审核问题须标明无、已解决或已接受，并附实际处理说明')
