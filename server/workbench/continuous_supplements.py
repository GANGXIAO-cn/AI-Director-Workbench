"""Continuous-only supplement decisions; manual adoption remains unchanged."""
import json
from .db import connect, transaction, dumps
from .errors import row
from . import supplements


class ContinuousSupplements:
    def _supplement_judgment(self,cid,stage,existing,new,content):
        context=dict(existing=existing,new=new,candidate=content)
        instruction=('判断两条制作补充是否真的冲突；若冲突，继承已登记结论，不能用下游新补充替换旧事实。'
                     '结合各自场次和时间；不同场次或时间可以同时成立。reply 只返回 JSON：'
                     '{"conflict":true|false,"keep":"existing|new|both","reason":"一句话"}。'
                     '不冲突时 keep=both，冲突时只能 existing。\n'+dumps(context))
        for attempt in range(2):
            try:
                result=json.loads(self._continuous_discuss(cid,stage,instruction))
                if (type(result.get('conflict')) is bool and isinstance(result.get('reason'),str) and result['reason'].strip()
                    and result.get('keep') in (('existing',) if result['conflict'] else ('both',))):
                    return result
            except (ValueError,AttributeError,TypeError):
                pass
            instruction+='\n上次回复格式不合格，请按契约重回。'
        self._continuous_stop(cid,'blocked','制作补充判断回复不合格：'+existing['statement']+'；'+new['statement'])

    def _continuous_prepare_supplements(self,cid,stage,vid):
        if stage not in supplements.STAGES:return vid
        eid=self._continuous_record(cid)['episode_id']
        for revision in range(2):
            with connect(self.db_path) as db:
                content=json.loads(row(db,'SELECT content FROM version WHERE id=?',vid)['content'])
                records=supplements.active(db,eid)
                existing={r['subject']:r for r in records if r['origin_version_id']!=vid}
                existing.update({r['subject']:r for r in records if r['state']=='confirmed'})
            rewritten=False
            for item in content.get('supplements',[]):
                old=existing.get(item.get('subject')) if isinstance(item,dict) else None
                if not old or old['statement']==item.get('statement'):continue
                decision=self._supplement_judgment(cid,stage,old,item,content)
                self._continuous_log(cid,stage,'confirm_supplement',old['id'],decision['reason'],dict(existing=old,new=item,judgment=decision))
                if revision:
                    self._continuous_stop(cid,'blocked','制作补充修订后仍与登记说法不同：'+old['statement']+'；'+item['statement'])
                if decision['keep']=='existing':
                    with transaction(self.db_path) as db:
                        self._insert_supplement(db,eid,item,stage,vid,state='rejected')
                    instruction='保留已登记的制作补充，修订候选正文并移除新补充登记：'+dumps(dict(existing=old,rejected=item))
                else:
                    instruction='两条补充在各自场次成立；保留正文事实，将新补充 subject 改为更具体的对象.属性（区分场次），避免与旧补充同名：'+dumps(dict(existing=old,new=item))
                self._continuous_create(cid,stage,instruction,base=vid)
                vid=self._continuous_art(eid,stage)['versions'][-1]['id'];rewritten=True
                break
            if not rewritten:return vid
        return vid

    def _continuous_confirm_supplements(self,cid,stage):
        if stage not in supplements.STAGES:return
        eid=self._continuous_record(cid)['episode_id']
        for item in self.production_supplements(eid):
            if item['state']!='proposed':continue
            self.decide_supplement(item['id'],'confirm',command_id=f'{cid}:supplement-confirm:{item["id"]}')
            self._continuous_log(cid,stage,'confirm_supplement',item['id'],'补充与已采用内容无冲突，按稿件确认',dict(supplement=item))
