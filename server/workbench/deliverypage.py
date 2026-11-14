"""Build an offline delivery page from the files and versions actually packed."""
from html import escape
from pathlib import Path
from urllib.parse import quote

from . import publishing
from .deliverypage_check import TEMPLATES, TEMPLATE, check, reference_shot_label

DOCUMENTS = (
    ("shot-references", "逐镜引用与看图记录", "逐镜参考.html"),
    ("timing-budget", "分段与时长预算", "分段与时长预算.json"),
    ("production", "镜头充分性联合表", "镜头充分性联合表.md"),
    ("rhythm", "镜头节奏核对", "镜头节奏核对.json"),
    ("finecut-handoff", "精剪交接", "03_精剪交接.md"),
    ("manifest", "实际素材清单", "episode.json"),
    ("review", "审核依据", "review.md"),
)
MUSIC_LABELS = {"suno_title":"复制Suno标题", "suno_style":"复制Suno风格提示词", "suno_exclude":"复制排除风格"}


def h(value):
    return escape(str(value), quote=True)


def verbatim(value):
    # A child element preserves an initial newline in <pre>. Encode CR to avoid
    # the browser normalising CRLF before copying the original UTF-8 prompt.
    return "<span>" + h(value).replace("\r", "&#13;") + "</span>"


def link(path):
    return quote(str(path), safe="/")


def media_suffix(kind, name, data):
    if kind == "image":
        return ".jpg" if data.startswith(b"\xff\xd8") else ".png"
    if kind not in ("audio", "video"):
        raise ValueError("不支持的交付媒体类型")
    if data[4:8] == b"ftyp":
        return ".m4a" if kind == "audio" else ".mp4"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav"
    if kind == "video" and data.startswith(b"RIFF") and data[8:12] == b"AVI ":
        return ".avi"
    if kind == "audio" and data.startswith(b"fLaC"):
        return ".flac"
    if data.startswith(b"OggS"):
        return ".ogg" if kind == "audio" else ".ogv"
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    allowed = {"audio": {".wav",".mp3",".ogg",".m4a",".flac",".aac",".webm"}, "video":{".mp4",".webm",".mov",".ogv",".avi",".mkv"}}
    suffix = Path(name).suffix.lower()
    return suffix if suffix in allowed[kind] else ".bin"


def publish_snapshot(publish):
    strict = publish["settings"].get("copy_format") == "instrumental_v1"
    fields = []
    for key, value in publish["fields"].items():
        heading = publish["fieldLabels"][key]
        if strict:
            heading = publishing.COPY_HEADINGS.get(key, heading)
        label = MUSIC_LABELS.get(key, "复制"+heading) if strict else "复制"+heading
        fields.append((key.replace("_","-") if key in MUSIC_LABELS else key, heading, label, value))
    notes = ["本集音乐使用状态：" + publish["music"] + "。"]
    if strict:
        notes.append("Instrumental：开；Lyrics：留空。")
    table = [["剧情段", "全片时间", "音乐处理"]]
    for cue in publish["cues"]:
        table.append([str(cue.get("segment") or cue.get("scene") or "全片"), str(cue.get("at", "")), str(cue.get("action", ""))])
    if len(table) == 1:
        table.append(["全片", "未指定", "未登记剪辑对位"])
    return dict(fields=fields, notes=notes, table=table, strict=strict)


def publish_markdown(title, pack):
    parts = ["# "+title]
    for index, (_, heading, _, value) in enumerate(pack["fields"]):
        if index == 3:
            parts.extend(["## Suno BGM" if pack["strict"] else "## 音乐", *pack["notes"]])
        block = "```text\n"+value+"\n```" if pack["strict"] else value
        parts.append(("## " if index < 3 else "### ")+heading+"\n\n"+block)
    if len(pack["fields"]) <= 3:
        parts.extend(["## 音乐", *pack["notes"]])
    parts.append("### 剪辑对位")
    rows = [pack["table"][0], ["---"]*3, *pack["table"][1:]]
    parts.append("\n".join("|"+"|".join(cell.replace("|","&#124;").replace("\n","<br>") for cell in row)+"|" for row in rows))
    return "\n\n".join(parts)+"\n"


def gallery(meta):
    parts = ['<article class="image-selection" id="image-selection"><h2>未采用图片／补选</h2>',
             '<p id="selection-project">'+h(meta["project"])+'</p>']
    if meta.get("requiredImages"):
        parts.append("<h3>必用图落实情况</h3><ul>")
        for rule in meta["requiredImages"]:
            targets = "、".join(rule["requiredHere"]) or "整集至少一段"
            note = rule["reason"] or "旧记录未填写理由"
            limits = " · 用途限制：" + rule["limitations"] if rule["limitations"] else ""
            parts.append("<li>" + h(f"{rule['name']} · {note} · 要求 {targets} · 实际 " +
                                    "、".join(rule["usedHere"]) + limits) + "</li>")
        parts.append("</ul>")
    for used, title in [(True, "已采用图片"), (False, "未采用图片")]:
        rows = [row for row in meta["images"] if bool(row["segments"]) == used]
        parts.append(f'<h3>{title} · {len(rows)}</h3><ol class="cards">')
        for row in rows:
            name = h(row["name"])+" "+h(row["version"])
            url = link(row["path"])
            parts.append(f'<li class="card"><a href="{url}">{name}<img loading="lazy" src="{url}" alt="{name}"></a>')
            if used:
                parts.append('<p>使用段 '+h("、".join(row["segments"]))+'</p>')
            if row.get('provisionalIssues'):
                parts.append('<div class="known-image-issues"><h4>AI 暂用，未解决</h4><ul>'+''.join('<li>'+h(issue['issue_key']+'：'+issue['text'])+'</li>' for issue in row['provisionalIssues'])+'</ul></div>')
            if row.get('knownIssues'):
                parts.append('<div class="known-image-issues"><h4>用户已接受的已知问题</h4><ul>'+''.join('<li>'+h(issue['issue_key']+'：'+issue['text'])+'</li>' for issue in row['knownIssues'])+'</ul></div>')
            if row["marks"]:
                labels = {"required":"必用","backup":"备选","recommended":"推荐","adopted":"采用"}
                parts.append('<p>'+h("、".join(labels.get(m,m) for m in row["marks"]))+'</p>')
            parts.append('<label><input type="checkbox" data-pick="'+h(row["id"])+'">要求补充或保留此图</label></li>')
        parts.append('</ol>')
    parts.extend(['<label for="selection-note">期望段落或用途</label><input id="selection-note">',
                  '<button type="button" id="copy-selection">复制选择消息</button>',
                  '<p id="selection-feedback" role="status"></p>',
                  '<label for="selection-message">选择消息</label><textarea id="selection-message" readonly></textarea></article>'])
    return "\n".join(parts)


def bindings_html(meta):
    rows = meta.get("bindings", [])
    if not rows:
        return ""
    parts = ['<article id="episode-bindings"><h2>分段角色绑定</h2><ol class="cards">']
    labels = {"ok": "身份清楚", "unclear": "看不清", "absent": "未出镜"}
    for row in rows:
        parts.append(f'<li class="card"><h3>{h(row["segment"])} · {h(row["role"])}</h3><p>{h(labels.get(row["status"],row["status"]))}</p>')
        if row.get("path"):
            url = link(row["path"])
            parts.append(f'<a href="{url}">{h(row["name"])} {h(row["version"])}<img src="{url}" alt="{h(row["role"])}"></a>')
            parts.append('<p>'+('列入本段上传素材' if row["uploaded"] else '供角色对照')+'</p>')
        if row.get("cells"):
            parts.append('<p>对应格位 '+h("、".join(map(str,row["cells"])))+'</p>')
        if row.get("depictsMoment"):
            parts.append('<p>已核对的开场 '+h(row["depictsMoment"]["segment"])+'</p>')
        if row.get("note"):
            parts.append('<p>'+h(row["note"])+'</p>')
        parts.append('</li>')
    return "\n".join(parts+['</ol></article>'])


def roster_html(meta):
    roster = meta.get("roster")
    if not roster:
        return ""
    parts = ['<article id="episode-cast"><h2>本集角色设定</h2><ul class="cards">']
    for role, row in roster.items():
        parts.append('<li class="card"><h3>'+h(role)+'</h3>')
        if row.get("description"):
            parts.append('<p>'+h(row["description"])+'</p>')
        if row.get("path"):
            url = link(row["path"])
            parts.append(f'<a href="{url}"><img src="{url}" alt="{h(role)}">打开独立身份基准</a><p>{h(row["appearance"])}</p>')
        elif row.get("identityRequired") is not None:
            parts.append('<p>'+("需要独立身份基准，尚未登记" if row["identityRequired"] else "故事稿标记为无需独立身份基准")+'</p>')
        if row["segments"]:
            parts.append('<p>已登记出镜段 '+h("、".join(row["segments"]))+'</p>')
        if row["unconfirmed"]:
            parts.append('<p>待确认段 '+h("、".join(row["unconfirmed"]))+'</p>')
        parts.append('</li>')
    return "\n".join(parts+['</ul></article>'])


def publish_html(pack):
    parts = ['<article id="publish-pack" class="publish-pack" data-publish-pack="v1"><h2>发布包</h2>',
             '<a data-publish-source href="'+link("01_发布包.md")+'">发布包源文件</a>']
    for key, heading, label, text in pack["fields"]:
        parts.append(f'<div class="card publish-card"><h3>{h(heading)}</h3><pre data-publish-field="{h(key)}" data-copy-label="{h(label)}">{verbatim(text)}</pre></div>')
    parts.extend('<p class="status" data-publish-note>'+h(note)+'</p>' for note in pack["notes"])
    parts.append('<h3>剪辑对位</h3><div class="publish-table-scroll"><table>')
    for index, row in enumerate(pack["table"]):
        tag = "td" if index else "th"
        parts.append('<tr>'+''.join(f'<{tag}>{h(cell)}</{tag}>' for cell in row)+'</tr>')
    parts.append('</table></div></article>')
    return "\n".join(parts)


def render(root, data, files):
    meta = data["workbench"]["page"]
    assets = {a["id"]:a for a in data["assets"]}
    segments = data["segments"]
    title = h(data["english_title"])
    parts = [f'<!doctype html><html lang="zh-CN" data-delivery-template="{TEMPLATE}"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             f'<title>{title}</title><style>'+(TEMPLATES/"delivery.css").read_text()+'</style></head><body><main>',
             f'<h1>{title}</h1><p class="meta">{h(data["aspect_ratio"])} · {len(segments)} 段 · 计划 {sum(s["duration_s"] for s in segments):g} 秒</p>',
             f'<p data-delivery-scope="{data["scope"]}">'+("整集完整交付" if data["scope"]=="full" else "所选段试验交付")+'</p>',
             '<nav aria-label="本集文档">']
    for key, label, name in DOCUMENTS:
        if name in files:
            parts.append(f'<a data-document="{key}" href="{link(name)}">{label}</a>')
    bindings = bindings_html(meta)
    roster = roster_html(meta)
    if roster:
        parts.append('<a href="#episode-cast">本集角色设定</a>')
    if bindings:
        parts.append('<a href="#episode-bindings">分段角色绑定</a>')
    parts.append('<a data-image-selection-link="true" href="#image-selection">未采用图片／补选</a></nav>')
    parts.append('<nav class="segments-nav" aria-label="分段导航">')
    for s in segments:
        parts.append(f'<a data-segment-link="{h(s["id"])}" href="#{link("segment-"+s["id"])}">{h(s["id"])}</a>')
    if meta["publish"]:
        parts.append('<a data-publish-link href="#publish-pack">发布包</a>')
    parts.append('</nav><p class="status">按段内顺序将素材和完整提示词送入 H3。实际生成后再核对动作、时长和声音。</p>')
    if data['workbench'].get('changes'):
        parts.append('<aside class="delivery-changes"><h2>本次修改</h2><ul>')
        for change in data['workbench']['changes']:
            scope='、'.join([*change['segments'],*change['scenes'],*change['needKeys']])
            parts.append('<li>'+h(scope+'：'+change['reason'])+'<p>'+h(change['instruction'])+'</p></li>')
        parts.append('</ul></aside>')
    for s in segments:
        sid = h(s["id"])
        parts.append(f'<section id="segment-{sid}" data-segment="{sid}"><h2>{sid}</h2><p class="meta">{h(s["strategy"])} · {h(s["start"])} · {h(s["duration_s"])} 秒</p>')
        grid = data["workbench"].get("frameGeometry", {}).get("grids", {}).get(s["id"])
        if grid:
            parts.append(f'<p class="meta" data-grid-size>九宫格原图 {h(grid["width"])} × {h(grid["height"])} 像素</p>')
        text = (root/s["prompt"]).read_bytes().decode("utf-8")
        parts.append(f'<p><a href="{link(s["prompt"])}">独立提示词 TXT</a></p><pre>{verbatim(text)}</pre>')
        parts.append('<ol class="cards">')
        for ref in sorted(s["references"], key=lambda r:r["upload_order"]):
            asset = assets[ref["asset_id"]]
            url = link(asset["path"])
            aid, label, kind = h(asset["id"]), h(ref["label"]), asset["type"]
            parts.append(f'<li class="card" data-asset="{aid}"><p class="meta">上传 {ref["upload_order"]} · {h(asset["version"])}</p><h3>{h(asset.get("name") or label)}</h3>')
            media = f'<img src="{url}" alt="{label}">' if kind=="image" else f'<{kind} controls preload="metadata" src="{url}"></{kind}>'
            parts.append(f'<a href="{url}">{label} · {aid}</a>{media}<p>{h(ref.get("role",""))}</p>')
            shot_label = reference_shot_label(ref)
            if shot_label:
                parts.append('<p data-reference-shots>'+h(shot_label)+'</p>')
            parts.append('</li>')
        parts.append('</ol></section>')
    if bindings:
        parts.append(bindings)
    if roster:
        parts.append(roster)
    if meta["publish"]:
        parts.append(publish_html(meta["publish"]))
    parts.append(gallery(meta))
    for name in ("image-selection.js", "prompt-copy.js"):
        parts.append('<script>'+(TEMPLATES/name).read_text()+'</script>')
    return "\n".join(parts+['</main></body></html>'])


def verify(root, data, files):
    if data["scope"] == "full":
        path = root / "03_精剪交接.md"
        if path.name not in files or not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise ValueError("完整交付缺少03_精剪交接.md")
    handoff = data["workbench"].get("finecut")
    if handoff is not None:
        from . import finecut
        for stage, source in handoff.get("sourceFiles", {}).items():
            if source["sha256"] != files.get(source["path"]) or source["version"] != handoff["adopted"][stage]:
                raise ValueError("精剪交接来源文件与冻结稿不符")
        if (handoff["adopted"] != data["workbench"]["adopted"] or handoff["revision"] != data["workbench"]["deliveryId"]
                or (root/"03_精剪交接.md").read_bytes() not in (finecut.render(handoff).encode(), finecut.render(handoff, legacy_labels=True).encode())):
            raise ValueError("精剪交接与本次来源版本或冻结正文不符")
    meta = data["workbench"]["page"]
    pack = meta["publish"]
    if pack:
        path = root/"01_发布包.md"
        if path.read_bytes().decode("utf-8") != publish_markdown(data["english_title"],pack):
            raise ValueError("发布包 Markdown 与采用字段、音乐状态或剪辑对位不同")
        pack = dict(pack, path=path)
    docs = [(key,label,root/name) for key,label,name in DOCUMENTS if name in files]
    return check(root, root/"00_图文对照.html", data, data["scope"], docs, pack, gallery(meta), allowed_files=files,
                 bindings_fragment=bindings_html(meta), roster_fragment=roster_html(meta))
