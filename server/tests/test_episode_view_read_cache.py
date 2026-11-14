"""Read-local deduplication must preserve validation and response semantics."""
from fastapi.testclient import TestClient
from workbench.app import create_app
from test_continuous_finishing import finishing_fixture


def test_view_reuses_identical_segments_and_preserves_all_gates(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    aid=env.art(case['eid'],'prompts')['id']
    for i in range(3):
        vid=wb.save_manual(aid,vid,{'notes':str(i)},'notes-'+str(i))['versionId']
    calls=[];original=wb._cast_prompt_errors
    def count(*args,**kwargs):calls.append(args[2]);return original(*args,**kwargs)
    monkeypatch.setattr(wb,'_cast_prompt_errors',count)
    cached=wb.episode_view(case['eid']);cached_calls=len(calls);calls.clear()
    gate=wb.adoption_gate
    def uncached(version,db=None,**kwargs):return gate(version,db)
    monkeypatch.setattr(wb,'adoption_gate',uncached)
    fresh=wb.episode_view(case['eid'])
    assert cached==fresh
    assert cached_calls<len(calls)


def test_new_read_does_not_reuse_old_validation(tmp_path,monkeypatch):
    env,case,cid,vid=finishing_fixture(tmp_path,True);wb=env.wb
    first=wb.episode_view(case['eid'])
    original=wb._cast_prompt_errors
    monkeypatch.setattr(wb,'_cast_prompt_errors',lambda *a,**k:[*original(*a,**k),'新读取的状态'])
    second=wb.episode_view(case['eid'])
    def errors(view):return next(v for a in view['artifacts'] for v in a['versions'] if v['id']==vid)['gate']['formatErrors']
    assert '新读取的状态' not in errors(first)
    assert '新读取的状态' in errors(second)
    assert '新读取的状态' in wb.adoption_gate(vid)['formatErrors']


def test_query_compression_keeps_response_and_non_query_unchanged(tmp_path):
    env,case,cid,vid=finishing_fixture(tmp_path);client=TestClient(create_app(env.wb,inline_worker=True,token='test'))
    import json
    params={'args':json.dumps({'episode_id':case['eid']})}
    zipped=client.get('/api/q/episode_view',params=params,headers={'accept-encoding':'gzip'})
    plain=client.get('/api/q/episode_view',params=params,headers={'accept-encoding':'identity'})
    assert zipped.status_code==plain.status_code==200
    assert zipped.headers['content-encoding']=='gzip'
    assert zipped.json()==plain.json()
    assert int(zipped.headers['content-length'])<int(plain.headers['content-length'])
    assert 'content-encoding' not in client.get('/api/session',headers={'accept-encoding':'gzip'}).headers
