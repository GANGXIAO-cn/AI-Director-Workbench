"""Assemble model-authored sections with frozen, program-owned fixed text."""
from copy import deepcopy
from . import readable


def assemble(entry, scaffold):
    if scaffold.get("prompt_format") == "h3_official":
        return dict(entry, prompt_format="h3_official", refs=deepcopy(scaffold["refs"]),
                    materialUses=deepcopy(scaffold.get("materialUses", [])))
    if 'authoredBlocks' not in entry:return entry
    if 'txt' in entry:raise ValueError('authoredBlocks 与 txt 不可同时提交')
    fixed=scaffold.get('fixedSections')
    if not isinstance(fixed,dict):raise ValueError('本轮没有冻结固定栏目，须提交完整 txt')
    free=entry['authoredBlocks']
    headings=readable.GRID if set(scaffold['blocks'])==set(readable.GRID) else readable.FLEX
    if not isinstance(free,dict) or set(free)!=set(headings) or any(not isinstance(v,str) for v in free.values()):
        raise ValueError('authoredBlocks 须逐栏提供文字，栏目与冻结骨架一致')
    refs=deepcopy(entry.get('refs',scaffold['refs']))
    if not isinstance(refs,list) or any(not isinstance(r,dict) for r in refs):
        raise ValueError('refs 须为引用对象数组')
    original={r['assetVersionId']:r for r in scaffold['refs']}
    if len(refs)!=len(original) or {r.get('assetVersionId') for r in refs}!=set(original):
        raise ValueError('引用集合发生变化，请明确提交完整 txt 和 refs 以核验新引用')
    # Labels are deterministic; scope and role are retained, not copied over.
    for r in refs:
        if r.get('label',original[r['assetVersionId']]['label'])!=original[r['assetVersionId']]['label']:
            raise ValueError('引用标签须沿用本轮冻结标签')
        r['label']=original[r['assetVersionId']]['label']
    parts={}
    for title in headings:
        phrases=fixed.get(title,[])
        body=free[title]
        for phrase in phrases:
            if phrase in body:raise ValueError('authoredBlocks 不应重复程序固定文字：'+title)
        parts[title]='\n'.join([*phrases,body]).strip()
    result={k:deepcopy(v) for k,v in entry.items() if k!='authoredBlocks'}
    from .materialcontext import binding_context
    result.update(materialUses=[binding_context(ref) for ref in refs],refs=refs,txt=readable.render(parts),blocks=parts,prompt_format='readable_v3')
    return result
