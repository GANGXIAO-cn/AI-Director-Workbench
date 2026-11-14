"""A completed judgment is not necessarily a cleared material use."""
from .materialcontext import RULE

INSTRUCTION=RULE+'''用途接续协议 v2。返回对象：
{"action":"continue|revise_prompt|conflict","conflicts":[],"reason":"具体依据","instruction":"局部修订要求","quote":"当前正文相关原句"}。
continue 表示当前用途已落实，conflicts 只能列已接受/暂用问题对象 {text,acceptedIssue} 或 {text,provisionalIssue}，没有则 []。
revise_prompt 仅用于正文遗漏明确用途限制且可以不改变采用方案、图片或引用集合地修正；填写具体 instruction、reason 和正文相关 quote。
conflict 表示真实新增的错图、越界用途或不能仅靠措辞解决的问题；填写 reason 和 quote，不能假称已解决。
不得把真实新冲突塞入 continue.conflicts；不要为了通过而返回空冲突。'''


def normalize(answer,accepted,provisional):
    known={a['text']:dict(text=a['text'],provisionalIssue=a['issue_key']) for a in provisional}
    known.update({a['text']:dict(text=a['text'],acceptedIssue=a['issue_key']) for a in accepted})
    # Read compatible no-conflict/known-issue replies, but never reinterpret an
    # unknown string as clearance or as a user acceptance.
    if isinstance(answer,list):answer=dict(action='continue',conflicts=answer)
    if not isinstance(answer,dict):return answer
    result=dict(answer)
    if isinstance(result.get('conflicts'),list):
        result['conflicts']=[known.get(c,c) if isinstance(c,str) else c for c in result['conflicts']]
    return result


def validate(answer,accepted,provisional,text):
    value=normalize(answer,accepted,provisional)
    if not isinstance(value,dict) or value.get('action') not in ('continue','revise_prompt','conflict'):
        return ['须按用途接续协议返回 action 对象']
    conflicts=value.get('conflicts',[])
    if not isinstance(conflicts,list):return ['conflicts 须为数组']
    for item in conflicts:
        if not isinstance(item,dict) or not isinstance(item.get('text'),str) or not item['text'].strip():
            return ['未知冲突不能作为通过记录：请用 revise_prompt 或 conflict 并说明处理依据']
        if not (item.get('acceptedIssue') in {a['issue_key'] for a in accepted} or item.get('provisionalIssue') in {a['issue_key'] for a in provisional}):
            return ['conflicts 只能引用已有接受或暂用问题']
    if value['action']!='continue':
        if conflicts:return ['未解决问题写 reason，不混入通过记录 conflicts']
        if not isinstance(value.get('reason'),str) or not value['reason'].strip():return ['缺少新冲突的具体依据']
        quote=value.get('quote')
        if not isinstance(quote,str) or not quote.strip() or quote not in text:return ['quote 须是当前正文相关原句']
        if value['action']=='revise_prompt' and (not isinstance(value.get('instruction'),str) or not value['instruction'].strip()):return ['缺少局部修订要求']
    return []
