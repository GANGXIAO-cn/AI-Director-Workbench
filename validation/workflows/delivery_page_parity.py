"""Check the same mutated offline pages using the source CLI and workbench HTML gate."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote
from unittest.mock import patch

from historical_baseline import ROOT, PIN, SKILL

sys.path[:0] = [str(ROOT/"server"),str(SKILL/"tests")]
from workbench import deliverypage_check as native


def compare():
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","templates","tests"],check=True)
    for name in ("delivery.css","prompt-copy.js","image-selection.js"):
        assert (native.TEMPLATES/name).read_bytes() == subprocess.check_output(["git","-C",SKILL,"show",f"{PIN}:templates/{name}"])
    from test_character_integration import CharacterIntegration
    fixture = CharacterIntegration()
    fixture.setUp()
    fixture.three()
    import check_delivery as source
    from image_selection import gallery_html
    from publish_delivery import load_publish_pack
    from render_delivery import render, render_cast, document_links
    root, data, page, manifest = fixture.root, fixture.data, fixture.page, fixture.manifest
    text = page.read_text()
    docs = document_links(manifest,data,inline_selection=True)
    pack = load_publish_pack(root,required=True)
    gallery = gallery_html(root,manifest,output=page)
    cast = render_cast(data,lambda p:quote(os.path.relpath(root/p,page.parent),safe="/"))
    scope = "full"
    def workbench():
        return native.check(root,page,data,scope,docs,pack,gallery,cast)
    def original():
        return source.check(manifest,page,scope=scope)
    assert original() == workbench()
    def replace(source_text, before, after):
        assert before in source_text, before
        return source_text.replace(before,after,1)
    def sub(expr,repl,subject):
        result,n = re.subn(expr,repl,subject,count=1)
        assert n and result != subject, expr
        return result
    seg = data["segments"][0]
    sid = seg["id"]
    block = re.search(r'<section id="segment-[\s\S]+?</section>',text).group()
    bad = {
        "scope":text.replace('data-delivery-scope="full"','data-delivery-scope="trial"'),
        "gallery":text.replace(gallery,""), "gallery_navigation":text.replace('data-image-selection-link="true"','data-unused-nav'),
        "cast_body":text.replace(cast,cast.replace("女孩","不相干的人")),
        "cast_roles":text.replace("<main>",'<main><span data-cast-role="stranger"></span>'),
        "cast_asset_order":text.replace("<main>",'<main><a data-cast-asset="extra" href="../episode.json"></a>'),
        "cast_link":text.replace(cast,"<!--"+cast+"-->"+sub(r'(data-cast-asset="[^"]+" href=")[^"]*',r'\g<1>../episode.json',cast)),
        "id_collision":text.replace('<main>','<main id="image-selection">'),
        "heading":sub(r'<h1>[^<]*</h1>','<h1>Other Title</h1>',text),
        "section_count":text.replace(block,""),"duplicate_id":text.replace('<main>','<main id="segment-'+sid+'">'),
        "template":text.replace('data-delivery-template="puggy-light-v1"','data-delivery-template="missing"'),
        "publish_nav":text.replace('data-publish-link="true"','data-other-link'),
        "publish_fields":text.replace('data-copy-label="复制标题"','data-copy-label="复制旧标题"'),
        "publish_note":text.replace('data-publish-note="true"','data-irrelevant-note'),
        "publish_source":sub(r'(data-publish-source="true" href=")[^"]*',r'\g<1>../episode.json',text),
        "no_doc":text.replace('data-document=','data-unrelated='),
        "wrong_doc":sub(r'(data-document="[^"]+" href=")[^"]*',r'\g<1>../episode.json',text),
        "no_segment_nav":text.replace('data-segment-link=','data-no-segment='),
        "wrong_anchor":text.replace('href="#segment-'+sid+'"','href="#other"'),
        "cast_navigation":text.replace('href="#episode-cast"','href="#other"'),
        "extra_link":text.replace('<main>','<main><a href="../episode.json">多余入口</a>'),
        "extra_script":text+'<script>void 0</script>',
    }
    changes = {
        "pre_count":block.replace('<pre>','<pre></pre><pre>'),
        "no_grid":block.replace('<ol class="cards">','<ol>'),
        "txt_mismatch":block.replace('<pre>','<pre>遗漏第一句。'),
        "ref_order":sub(r'data-asset="[^"]+"','data-asset="unbound"',block),
        "shot_label":block.replace('作用镜头：','镜号：'),
        "asset_label":block.replace('图片1 · ','图一 · '),
        "media":sub(r'(<img src=")[^"]*',r'\g<1>../episode.json',block),
        "txt_link":sub(r'href="[^"]+">独立提示词TXT','href="../episode.json">独立提示词TXT',block),
        "not_local":sub(r'(<a href=")[^"]*',r'\g<1>https://invalid.example/',block),
        "missing_file":sub(r'(<a href=")[^"]*',r'\g<1>missing-file',block),
        "missing_url":sub(r'<a href="[^"]*">','<a>',block),
    }
    bad.update({k:replace(text,block,v) for k,v in changes.items()})
    cases = {}
    def run_case(name, changed):
        page.write_text(changed)
        errors = []
        for fn in (original,workbench):
            try:
                fn()
            except ValueError as exc:
                errors.append(str(exc))
        assert len(errors)==2 and errors[0]==errors[1], (name,errors)
        cases[name] = dict(matchedRule=True,sourceOK=False,workbenchOK=False,issue=errors[0])
    for name, changed in bad.items():
        assert changed != text, name
        run_case(name, changed)
    page.write_text(text)
    positive = workbench()
    # Both checkers regenerate the gallery first. Inject a faulty fragment from
    # the renderer to exercise the defensive duplicate-ID check independently.
    good_gallery = gallery
    gallery = gallery.replace('</article>', '<p id="image-selection"></p></article>')
    with patch("image_selection.gallery_html", return_value=gallery):
        run_case("gallery_duplicate_id", text.replace(good_gallery,gallery))
    cases["gallery_duplicate_id"]["injection"] = "补选区渲染函数返回重复 ID 片段"
    pack["path"].rename(root/"试验夹具发布包备份.md")
    scope, pack = "trial", None
    render(manifest,page,scope=scope)
    gallery = gallery_html(root,manifest,output=page)
    docs = document_links(manifest,data,inline_selection=True)
    assert original() == workbench()
    trial_text = page.read_text()
    run_case("orphan_publish",trial_text.replace('<main>','<main><a data-publish-link href="#publish-pack">发布包</a>'))
    return dict(sourceCommit=PIN, positive=positive, testedTemplateFiles=3, cases=cases)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("output",type=Path)
    result=compare()
    parser.parse_args().output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(len(result["cases"]),"matching fault diagnostics",result["positive"])
