"""Segment review inherits upstream decisions and validates its actual response scope."""
import json
from types import SimpleNamespace
from workbench.service import Workbench
from workbench.prompts import segment_review_trailer


def check(result, purpose='comparison'):
    manifest=dict(purpose=purpose,scope='seg-02',inputs=[dict(role='production_supplements',sha256='supp')])
    wb=SimpleNamespace(store=SimpleNamespace(load_manifest=lambda p:(manifest,None),
        read_blob=lambda h:json.dumps([dict(code='S01')]).encode()),
        _review_settings=lambda t,m:[dict(key='cat-no-dialogue',strength='required')],
        _review_decisions=lambda m:{})
    run=dict(id='run',input_hash='a'*64,target_version_id='version',purpose=purpose,manifest_relpath='unused')
    return Workbench._precheck(wb,run,{'stage':'story'},'review',result)


def result():
    return dict(schemaVersion=1,runId='run',inputFingerprint='a'*64,kind='review',reviewedVersionId='version',
                **{'pass':'comparison'},decision='pass',issues=[],observations=[dict(objectId='seg-02',versionId='version',unit='segment',anchor='0-3秒',evidence='忠实翻译采用方案')])


def test_segment_does_not_require_global_setting_checklist():
    assert check(result())==[]


def test_frozen_supplement_and_setting_observations_are_allowed():
    value=result()
    for code in ('S01','cat-no-dialogue'):
        value['observations'].append(dict(value['observations'][0],objectId=code))
    assert check(value)==[]


def test_unknown_id_and_wrong_version_still_fail_with_actionable_error():
    for field,bad in [('objectId','S99'),('versionId','wrong-version')]:
        value=result();value['observations'][0][field]=bad
        errors=check(value)
        assert errors and 'objectId 允许' in errors[0] and 'S01' in errors[0]


def test_story_still_requires_global_setting_review():
    value=result();value['pass']='story'
    assert any('未逐条' in e for e in check(value,'review'))


def test_trailer_does_not_require_unproduced_video_to_pass():
    text=segment_review_trailer('comparison','seg-02',['cat-no-dialogue'])
    assert '不要求每段把全部设定各填一条' in text
    assert '只在 observations 说明未测' in text
    assert '缺少图像或视频证据，记 continuity、uncertain' not in text


def test_review_receipt_gets_version_and_segment_from_request():
    from workbench import contracts
    from workbench.replyformat import bind_review_reply
    schema = contracts.review_content_schema("cold_read")
    content = dict(decision="pass", issues=[], observations=[dict(unit="segment", anchor="00:02", evidence="猫推开门")])
    contracts.Draft202012Validator(schema).validate(content)
    run = dict(id="run",input_hash="a"*64,target_version_id="version")
    result = bind_review_reply(run, content, "cold_read", "seg-02")
    assert not contracts.validate(result)
    assert result["observations"][0]["objectId"] == "seg-02"
    assert result["observations"][0]["versionId"] == "version"
