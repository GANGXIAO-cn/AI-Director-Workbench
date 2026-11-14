"""Retired material judgments remain evidence, not prompt prerequisites."""
import pytest
from workbench import materialuse
from test_image_assess import reply
from test_material_conclusion_continuity import limited_fixture
from test_continuous_finishing import finishing_fixture


@pytest.mark.parametrize('kind,result',[
    ('素材用途接续',{'action':'conflict'}),
    ('素材用途接续',{'action':'revise_prompt','instruction':'要求参考图换衣服'}),
    ('引用用途核对',{'mappings':[{'source':'necklace','mode':'unresolved','target':''}]}),
    ('引用用途核对',{'mappings':[{'source':'washers','mode':'state','target':''}]}),
])
def test_retired_receipts_do_not_reassess_or_change_candidate(tmp_path,kind,result):
    env,case,cid,vid,image=limited_fixture(tmp_path);wb=env.wb
    before=len(env.codex.calls)
    receipts={kind+':G01:'+image:{'result':result}}
    wb._continuous_cursor(cid,judgmentReceipts=receipts)
    original=wb._continuous_version(vid)
    assert wb._continuous_shot_observations(cid,vid,{'G01'})==vid
    assert len(env.codex.calls)==before and wb._continuous_version(vid)==original
    assert wb._continuous_record(cid)['cursor']['judgmentReceipts']==receipts


def test_invalid_cache_result_uses_remaining_retry_not_permanent_success(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path);wb=env.wb
    env.codex.script=[reply(['旧字符串']),reply(dict(action='continue',conflicts=[]))]
    arguments=(cid,'测试缓存','a','固定指令',{},lambda a:[])
    wb._continuous_bounded_json(*arguments,stage='prompts')
    result,_=wb._continuous_bounded_json(*arguments[:-1],lambda a:materialuse.validate(a,[],[],'正文'),stage='prompts')
    assert result['action']=='continue'

