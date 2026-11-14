"""Offline HTML checks ported from milo-leo-short-video scripts/check_delivery.py
at ab0fa75e3b2254a9341a8501a48be7577ab023eb. Callers supply frozen episode-specific inputs.
"""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

TEMPLATES = Path(__file__).with_name("delivery_templates")
TEMPLATE = "puggy-light-v1"


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections, self.links, self.scripts, self.media = [], [], [], []
        self.current, self.asset = None, None
        self.pre, self.script, self.title, self.anchor = False, False, False, False
        self.heading = ''
        self.reading_shots = False
        self.reading_grid_size = False
        self.template, self.styles, self.style = None, [], False
        self.documents, self.navigation, self.ids = [], [], []
        self.cast_roles, self.cast_assets, self.cast_versions = [], [], []
        self.main_count = 0
        self.publish_count, self.in_publish = 0, False
        self.publish_fields, self.publish_notes, self.publish_rows = [], [], []
        self.publish_navigation, self.publish_sources = [], []
        self.publish_pre = self.publish_note = self.publish_cell = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-character-contract' in attrs: self.cast_versions.append(attrs['data-character-contract'])
        if 'data-cast-role' in attrs: self.cast_roles.append(attrs['data-cast-role'])
        if 'data-cast-asset' in attrs: self.cast_assets.append((attrs['data-cast-asset'], attrs.get('href')))
        if tag == 'article' and 'data-publish-pack' in attrs:
            self.publish_count += 1
            self.in_publish = True
            self.publish_dom_id = attrs.get('id')
        if 'data-publish-link' in attrs:
            self.publish_navigation.append(attrs.get('href'))
        if self.in_publish:
            if 'data-publish-source' in attrs:
                self.publish_sources.append(attrs.get('href'))
            if tag == 'pre':
                self.publish_pre = True
                self.publish_fields.append({'key': attrs.get('data-publish-field'),
                                            'label': attrs.get('data-copy-label'), 'text': ''})
            if tag == 'p' and 'data-publish-note' in attrs:
                self.publish_note = True
                self.publish_notes.append('')
            if tag == 'tr': self.publish_rows.append([])
            if tag in ('td', 'th'):
                if not self.publish_rows:
                    raise ValueError('发布包表格单元格缺少表行')
                self.publish_cell = True
                self.publish_rows[-1].append('')
        if tag == 'html': self.template = attrs.get('data-delivery-template')
        if tag == 'style': self.style = True; self.styles.append('')
        if tag == 'main': self.main_count += 1
        if 'id' in attrs: self.ids.append(attrs['id'])
        if tag == 'section':
            self.current = {'id': attrs.get('data-segment'), 'text': '', 'assets': [],
                            'pre_count': 0, 'prompt_links': [], 'dom_id': attrs.get('id'), 'card_grids': 0}
            self.sections.append(self.current)
        if tag == 'p' and 'data-grid-size' in attrs and self.current is not None:
            self.reading_grid_size = True
            self.current.setdefault("grid_sizes", []).append("")
        if tag == 'ol' and self.current is not None and 'cards' in attrs.get('class', '').split():
            self.current['card_grids'] += 1
        if tag == 'pre' and self.current is not None:
            self.pre = True
            self.current['pre_count'] += 1
        if tag == 'h1': self.title = True
        if tag == 'li' and self.current is not None and attrs.get('data-asset'):
            self.asset = {'id': attrs['data-asset'], 'links': [], 'media': [], 'label': '',
                          'is_card': 'card' in attrs.get('class', '').split()}
            self.current['assets'].append(self.asset)
        if tag == 'p' and self.asset is not None and 'data-reference-shots' in attrs:
            self.reading_shots = True
            self.asset.setdefault('shot_labels', []).append('')
        if tag == 'a':
            self.anchor = True
            if self.current is None:
                if 'data-document' in attrs:
                    self.documents.append((attrs['data-document'], attrs.get('href')))
                if 'data-segment-link' in attrs:
                    self.navigation.append((attrs['data-segment-link'], attrs.get('href')))
            if self.current is not None:
                if self.asset is not None:
                    self.asset['links'].append(attrs.get('href'))
                else:
                    self.current['prompt_links'].append(attrs.get('href'))
        if tag in ('img', 'video', 'audio'):
            item = (tag, attrs.get('src'))
            self.media.append(item)
            if self.asset is not None: self.asset['media'].append(item)
        for key in ('src', 'href'):
            if key in attrs: self.links.append(attrs[key])
        if tag == 'script':
            self.script = True
            self.scripts.append('')

    def handle_endtag(self, tag):
        if tag == 'article': self.in_publish = False
        if tag == 'pre': self.publish_pre = False
        if tag == 'p':
            self.reading_shots = self.reading_grid_size = self.publish_note = False
        if tag in ('td', 'th'): self.publish_cell = False
        if tag == 'section': self.current, self.asset = None, None
        if tag == 'li': self.asset = None
        if tag == 'a': self.anchor = False
        if tag == 'pre': self.pre = False
        if tag == 'script': self.script = False
        if tag == 'h1': self.title = False
        if tag == 'style': self.style = False

    def handle_data(self, data):
        if self.reading_grid_size and self.current is not None:
            self.current["grid_sizes"][-1] += data
        if self.reading_shots and self.asset is not None:
            self.asset['shot_labels'][-1] += data
        if self.in_publish:
            if self.publish_pre: self.publish_fields[-1]['text'] += data
            if self.publish_note: self.publish_notes[-1] += data
            if self.publish_cell: self.publish_rows[-1][-1] += data
        if self.pre and self.current is not None: self.current['text'] += data
        if self.script: self.scripts[-1] += data
        if self.title: self.heading += data
        if self.anchor and self.asset is not None: self.asset['label'] += data
        if self.style: self.styles[-1] += data


def check_segments(parsed, data, read_text, matches):
    """Check the visible segment cards against their independently supplied originals."""
    if len(parsed.sections) != len(data["segments"]):
        raise ValueError("页面段数不符")
    assets = {a["id"]:a for a in data["assets"]}
    return sum(check_segment(actual,segment,assets,data,read_text,matches)
               for actual,segment in zip(parsed.sections,data["segments"]))


def check_segment(actual, segment, assets, data, read_text, matches):
    if actual['pre_count'] != 1:
        raise ValueError(f'每段须有唯一完整pre，避免复制半段：{segment["id"]}')
    prompt_path = segment["prompt"]
    if actual['id'] != segment['id'] or actual['text'] != read_text(prompt_path):
        raise ValueError(f'TXT与页面原文不符：{segment["id"]}')
    return check_uploads(actual,segment,assets,data,matches)


def reference_shot_label(ref):
    numbers = ref.get("windows") or ref.get("shots")
    return "作用镜头：" + "、".join(numbers) if numbers else None


def check_uploads(actual, segment, assets, data, matches):
    prompt_path = segment["prompt"]
    if (actual['card_grids'] != 1 or not all(x['is_card'] for x in actual['assets'])):
        raise ValueError('素材卡片布局缺失')
    geometry = data.get("workbench", {}).get("frameGeometry")
    if geometry is not None:
        grid = geometry["grids"].get(segment["id"])
        expected_sizes = [f"九宫格原图 {grid['width']} × {grid['height']} 像素"] if grid else []
        if actual.get("grid_sizes", []) != expected_sizes:
            raise ValueError(f"页面九宫格原图尺寸不符：{segment['id']}")
    refs = sorted(segment['references'], key=lambda r: r['upload_order'])
    if [a['id'] for a in actual['assets']] != [r['asset_id'] for r in refs]:
        raise ValueError(f'页面实际上传顺序不符：{segment["id"]}')
    for entry, ref in zip(actual['assets'], refs):
        label = reference_shot_label(ref)
        if (label or data.get("shot_reference_version") == 1) and entry.get("shot_labels", []) != ([label] if label else []):
            raise ValueError(f'页面作用镜头与实际清单不符：{segment["id"]}/{ref["asset_id"]}')
        asset = assets[ref['asset_id']]
        expected = asset['path']
        expected_tag = 'img' if asset['type'] == 'image' else asset['type']
        if (len(entry['links']) != 1 or not matches(entry['links'][0],expected)
                or entry['label'] != ref['label'] + ' · ' + asset['id']):
            raise ValueError(f'素材链接或标签映射错误：{asset["id"]}')
        if (len(entry['media']) != 1 or entry['media'][0][0] != expected_tag
                or not matches(entry['media'][0][1],expected)):
            raise ValueError(f'素材预览映射错误：{asset["id"]}')
    if len(actual['prompt_links']) != 1 or not matches(actual['prompt_links'][0],prompt_path):
        raise ValueError(f'TXT下载链接映射错误：{segment["id"]}')
    return len(refs)


def check(root, page, data, scope, expected_docs, publish_pack, gallery_fragment, cast_fragment="", allowed_files=None, bindings_fragment="", roster_fragment=""):
    parsed = Page()
    content = page.read_bytes().decode("utf-8")
    if content.count('data-delivery-scope="' + scope + '"') != 1:
        raise ValueError('页面交付范围与检查范围不符')
    gallery = None
    fragment = gallery_fragment
    if content.count(fragment) != 1:
        raise ValueError('正式页面缺少当前完整同页补选区，或内容已失效')
    nav = '<a data-image-selection-link="true" href="#image-selection">未采用图片／补选</a>'
    if content.count(nav) != 1 or content.index(nav) > content.index(fragment):
        raise ValueError('正式页面缺少顶部未采用图片／补选同页入口')
    gallery = Page()
    gallery.feed(fragment)
    if roster_fragment:
        roster_link = '<a href="#episode-cast">本集角色设定</a>'
        if content.count(roster_fragment) != 1 or content.count(roster_link) != 1:
            raise ValueError("本集角色设定或入口与交付版本不同")
        gallery.feed(roster_fragment)
        content = content.replace(roster_fragment, "").replace(roster_link, "")
    if bindings_fragment:
        bindings_link = '<a href="#episode-bindings">分段角色绑定</a>'
        if content.count(bindings_fragment) != 1 or content.count(bindings_link) != 1:
            raise ValueError("角色绑定表或入口与交付版本不同")
        gallery.feed(bindings_fragment)
        content = content.replace(bindings_fragment, "").replace(bindings_link, "")
    # Validate the independent shared fragment, then keep segment parsing unchanged.
    content = content.replace(fragment, '').replace(nav, '')
    if data.get('character_contract_version') == 1:
        fragment = cast_fragment
        if content.count(fragment) != 1:
            raise ValueError('页面角色设定内容、图片或适用段落缺失/失效')
    parsed.feed(content)
    if data.get('character_contract_version') == 1:
        if parsed.cast_versions != ['1'] or parsed.cast_roles != list(data['cast']):
            raise ValueError('页面缺少完整本集角色设定区')
        assets = {a['id']: a for a in data['assets']}
        expected = [r['identity_asset_id'] for r in data['cast'].values() if r['identity_required']]
        if [a for a, _ in parsed.cast_assets] != expected:
            raise ValueError('页面角色基准缺项或错序')
        for aid, href in parsed.cast_assets:
            if not href or (page.parent / unquote(href)).resolve() != (root / assets[aid]['path']).resolve():
                raise ValueError('页面角色基准链接错配')

    if gallery and set(parsed.ids) & set(gallery.ids):
        raise ValueError('补选区与正文ID冲突')
    if gallery and len(set(gallery.ids)) != len(gallery.ids):
        raise ValueError('补选区ID重复')
    if parsed.heading != data['english_title']:
        raise ValueError('页面英文标题不符')
    if len(parsed.sections) != len(data['segments']):
        raise ValueError('页面段数不符')
    if len(set(parsed.ids)) != len(parsed.ids):
        raise ValueError('页面锚点ID重复')
    stylesheet = TEMPLATES / 'delivery.css'
    if parsed.template != TEMPLATE or parsed.styles != [stylesheet.read_text()] or parsed.main_count != 1:
        raise ValueError('交付模板或样式不符，须使用当前共享模板重建')
    assets = {a['id']: a for a in data['assets']}
    def target(url):
        if not isinstance(url, str): raise ValueError('页面链接缺失')
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            raise ValueError(f'链接不是纯本地文件：{url}')
        path = (page.parent / unquote(parts.path)).resolve()
        if allowed_files is not None and (not path.is_relative_to(root) or path.relative_to(root).as_posix() not in allowed_files):
            raise ValueError(f'链接不在本次交付文件内：{url}')
        if not path.is_file(): raise ValueError(f'链接不存在：{url}')
        return path
    if publish_pack:
        if (parsed.publish_count != 1 or parsed.publish_dom_id != 'publish-pack'
                or parsed.publish_navigation != ['#publish-pack']):
            raise ValueError('网页缺少发布包正文或导航锚点')
        expected_fields = [{'key': key, 'label': label, 'text': value}
                           for key, _, label, value in publish_pack['fields']]
        if parsed.publish_fields != expected_fields:
            raise ValueError('发布包复制字段缺失、顺序错误或与MD原文不一致')
        if parsed.publish_notes != publish_pack['notes'] or parsed.publish_rows != publish_pack['table']:
            raise ValueError('发布包音乐使用状态或剪辑对位表与MD不一致')
        if len(parsed.publish_sources) != 1 or target(parsed.publish_sources[0]) != publish_pack['path'].resolve():
            raise ValueError('发布包源文件链接缺失或映射错误')
    elif parsed.publish_count or parsed.publish_navigation or parsed.publish_sources:
        raise ValueError('网页含有无源文件的发布包')
    if [key for key, _ in parsed.documents] != [key for key, _, _ in expected_docs]:
        raise ValueError('本集文档入口缺失或顺序不符')
    for (_, url), (_, _, expected) in zip(parsed.documents, expected_docs):
        if target(url) != expected.resolve():
            raise ValueError('本集文档入口映射错误')
    if [key for key, _ in parsed.navigation] != [s['id'] for s in data['segments']]:
        raise ValueError('分段导航缺失或顺序不符')
    for (ident, url), section in zip(parsed.navigation, parsed.sections):
        parts = urlsplit(url or '')
        expected_id = 'segment-' + ident
        if (parts.scheme or parts.netloc or parts.path or parts.query
                or unquote(parts.fragment) != expected_id or section['dom_id'] != expected_id):
            raise ValueError('分段导航锚点映射错误')
    input_count = check_segments(parsed,data,lambda name:(root/name).read_bytes().decode("utf-8"),
                                 lambda url,name:target(url)==(root/name).resolve())
    cast_count = len(parsed.cast_assets)
    cast_nav = 1 if data.get('character_contract_version') == 1 else 0
    if cast_nav and parsed.links.count('#episode-cast') != 1:
        raise ValueError('缺本集角色设定导航')
    if len(parsed.media) != input_count + cast_count or len(parsed.links) != (input_count + cast_count) * 2 + cast_nav + len(data['segments']) + len(parsed.documents) + len(parsed.navigation) + (2 if publish_pack else 0):
        raise ValueError('页面存在额外或缺少的预览/链接')
    for url in parsed.links:
        if url not in [u for _, u in parsed.navigation] + parsed.publish_navigation + (['#episode-cast'] if cast_nav else []): target(url)
    script = (TEMPLATES / 'prompt-copy.js').read_text()
    expected_scripts = [script]
    if gallery:
        expected_scripts.insert(0, (TEMPLATES / 'image-selection.js').read_text())
        for url in gallery.links: target(url)
    if parsed.scripts != expected_scripts:
        raise ValueError('复制脚本不符或存在其他脚本')
    return {'ok': True, 'segments': len(parsed.sections), 'local_links': len(parsed.links),
            'layout_check': TEMPLATE,
            'document_links': len(parsed.documents), 'segment_links': len(parsed.navigation),
            'publish_pack': 'passed' if publish_pack else 'not_provided',
            'inline_image_selection': 'passed' if gallery else 'not_provided',
            'publish_copy_fields': len(parsed.publish_fields),
            'scope': '静态解析检查；图片完整解码、浏览器布局、真实剪贴板与三分支反馈须另外实测'}


