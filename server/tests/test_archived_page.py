from html import escape
import json

from workbench import archivepage
from workbench.archivefiles import ArchiveFiles
from workbench.storage import Store


def test_archived_page_maps_relative_inputs_and_keeps_independent_txt_results(tmp_path):
    store = Store(tmp_path/"files")
    source = "截图&道具.png"
    face = str(tmp_path/"人物.png")
    text = "第一行\n<&红伞>。\r\n"
    raw = b"test image"
    digest = store.put_blob(raw)
    data = dict(schema_version=2,assets=[dict(id="umbrella",type="image",path=source,sha256=digest),
            dict(id="face",type="image",fixed_role="clerk",path=face,sha256=digest)],
            segments=[dict(id="01",prompt="一.txt",references=[dict(upload_order=1,label="图片1",asset_id="umbrella",shots=["S22a"])]),
                      dict(id="02",prompt="二.txt",references=[dict(upload_order=1,label="图片1",asset_id="face",shots=["S23"])])])
    def section(sid,path,asset,shot,txt):
        return (f'<section id="seg-{sid}"><h2>镜头</h2><figure><a href="{path}"><img src="{path}"></a>'
                f'<figcaption>上传1 · 图片1 / {asset}<br>作用镜头：{shot}</figcaption></figure>'
                f'<a href="../{txt}.txt">原文</a><pre>{escape(text)}</pre></section>')
    html = section("01","../%E6%88%AA%E5%9B%BE%26%E9%81%93%E5%85%B7.png","umbrella","S22a","一")+section("02","../../../人物.png","face","S23","二")
    files={archivepage.PAGE:store.put_blob(html.encode()),"episode.json":store.put_blob(json.dumps(data).encode()),
           "一.txt":store.put_blob(text.encode()),"二.txt":store.put_blob(text.encode()),source:digest}
    meta=dict(aliases={},fixedRefs={face:dict(path=face,sha256=digest)},pageExternalPaths={face:"../../../人物.png"})
    def check(page,metadata=meta):
        return archivepage.checked_content(ArchiveFiles(dict(files,**{archivepage.PAGE:store.put_blob(page.encode())}),metadata,store),data)
    passed=check(html)
    assert passed["state"]=="passed" and len(passed["details"]["segments"])==2
    changed=check(html.replace("红伞","蓝伞",1))
    bad, other=changed["details"]["segments"]
    assert bad["textState"]=="failed" and bad["mappingState"]==other["state"]=="passed"
    assert bad["difference"]==dict(offset=text.index("红"),line=2,expected=text,page=text.replace("红伞","蓝伞"))
    for mutation in [html.replace(" / umbrella"," / stranger"),html.replace("S22a","S22"),html.replace("../%E6","https://example.invalid/%E6")]:
        bad,other=check(mutation)["details"]["segments"]
        assert bad["mappingState"]=="failed" and bad["textState"]==other["state"]=="passed"
    old=check(html,{k:v for k,v in meta.items() if k!="pageExternalPaths"})
    assert old["state"]=="incomplete" and old["details"]["segments"][0]["state"]=="passed"


def test_malformed_historical_publish_table_is_reported(tmp_path):
    from workbench.db import transaction
    from helpers import Env
    env=Env(tmp_path/"service")
    pid=env.wb.create_project("_template","旧目录")["projectId"]
    project=tmp_path/"project"
    root=project/"episodes"/"sample"
    (root/"正式交付").mkdir(parents=True)
    (root/"episode.json").write_text(json.dumps(dict(schema_version=2,segments=[])))
    (root/archivepage.PAGE).write_text('<article data-publish-pack="1"><td>没有行</td></article>')
    with transaction(env.wb.db_path) as db:
        db.execute("UPDATE project SET production=? WHERE id=?",(json.dumps({"assetRoot":str(project)}),pid))
    response=env.wb.import_historical_episode(pid,"sample")
    assert response["materialChecks"]["pageContent"]==dict(state="failed",message="发布包表格单元格缺少表行")
