"""Scene comparison pages from adopted images, explicit cast declarations and verified originals."""
import json
from html import escape
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .sceneprovenance import REF, used_scene_images
from .storage import canonical, sha256_bytes


def scene_references(data, assets, fixed, rules):
    by_id = {asset["id"]:asset for asset in data["assets"]}
    ids = {a["id"] for a in assets}
    cast = data.get("cast", {})
    roles, outfits, warnings = set(), {}, []
    related = []
    for segment in data.get("segments", []):
        refs = [r["asset_id"] for r in segment.get("references", []) if r.get("asset_id") in by_id]
        if not ids.intersection(refs):
            continue
        related.append(str(segment.get("id", "?")))
        declared = segment.get("on_screen_roles")
        if declared is None:
            raise ValueError(f"段{segment.get('id','?')}缺出镜声明 on_screen_roles")
        if not isinstance(declared, list) or any(not isinstance(r,str) or
                r not in cast and r not in rules["roles"] and r not in fixed for r in declared):
            raise ValueError("on_screen_roles须为已登记角色ID数组")
        roles.update(declared)
        for aid in refs:
            asset = by_id[aid]
            assigned = rules.get("outfitVersions",{}).get(aid)
            if assigned in declared and asset.get("category")=="costume":
                outfits[(assigned,aid)] = dict(asset,reference_kind="outfit",role=assigned,reference_label=asset["name"])
            if asset.get("type") != "image" or asset.get("category") != "character" or asset.get("fixed_role"):
                continue
            for role in declared:
                matches = [f"{key}@{ver}" for key,ver in REF.findall(asset.get("source") or "")
                           if key in rules["outfits"].get(role, [])]
                if matches:
                    outfits[(role,aid)] = dict(asset,reference_kind="outfit",role=role,reference_label=", ".join(matches))
    identities = []
    for role in sorted(roles):
        member = cast.get(role,{})
        if role in fixed:
            asset = dict(fixed[role], id=role)
        else:
            asset = by_id.get(member.get("identity_asset_id"))
        if asset:
            identities.append(dict(asset, reference_kind="identity", role=role,
                                   reference_label=role if role in fixed else (member.get("name") or role)))
        elif member.get("identity_required") is not False:
            warnings.append(role+"缺角色原图，服装图不能代替身份原图。")
        if rules["outfits"].get(role) and not any(key[0]==role for key in outfits):
            warnings.append(role+"未登记本场景服装参考。")
    return related, identities+list(outfits.values()), warnings


def render_page(images, references, read):
    height = max(1440, 300*len(references))
    sheet = Image.new("RGB",(1600 if references else 1200,height),"#eeeeee")
    draw = ImageDraw.Draw(sheet)

    def tile(item, left, top, cell_height, label):
        with Image.open(BytesIO(read(item["sha256"]))) as source:
            picture = ImageOps.exif_transpose(source).convert("RGB")
            picture.thumbnail((390,cell_height-40))
            sheet.paste(picture,(left+(400-picture.width)//2,top+30))
        draw.text((left+10,top+8),label,fill="black")

    for i,item in enumerate(images):
        tile(item,(i%3)*400,(i//3)*720,720,str(i+1)+(" ANCHOR" if item.get("anchor") else ""))
    for i,item in enumerate(references):
        cell = height//len(references)
        tile(item,1200,i*cell,cell,f"R{i+1} {item['reference_kind'].upper()}")
    stream = BytesIO()
    sheet.save(stream,format="JPEG",quality=92)
    return stream.getvalue()


def build(data, files, rules):
    if data.get("schema_version") != 2:
        raise ValueError("须声明 schema_version=2")
    groups, unmapped = {}, []
    for asset in used_scene_images(data):
        scenes = asset.get("scene_ids")
        if not isinstance(scenes,list) or not scenes or not all(isinstance(s,str) and s.strip() for s in scenes):
            unmapped.append(asset["id"])
            continue
        for scene in dict.fromkeys(scenes):
            groups.setdefault(scene,[]).append(asset)
    if not groups:
        raise ValueError("无明确scene_ids；请先登记本集采用图所属的场景")
    anchors = data.get("scene_anchors",{})
    if not isinstance(anchors,dict):
        raise ValueError("scene_anchors须为对象")
    verified = set()
    def verify(item, trusted_fixed=False, anchor=False):
        path = item["path"]
        expected = item.get("sha256")
        if item.get("reference_kind") and not expected:
            raise ValueError("角色／服装参考缺SHA，无法核对原图版本")
        raw = files.store.read_blob(expected) if trusted_fixed else files.scene_bytes(path)
        digest = sha256_bytes(raw)
        if expected and digest != expected:
            raise ValueError(f"源图指纹不符：{path}")
        if digest not in verified:
            with Image.open(BytesIO(raw)) as image:
                image.verify()
            verified.add(digest)
        return dict({k:item[k] for k in ("id","name","path","reference_kind","role","reference_label") if k in item},
                    sha256=digest, **({"anchor":True} if anchor else {}))

    result = dict(review_status="not_tested",unmapped_asset_ids=unmapped,scenes=[])
    for scene, assets in groups.items():
        segments, refs, warnings = scene_references(data,assets,files.metadata["fixed"],rules)
        references = [verify(r,r.get("reference_kind")=="identity" and r.get("role") in files.metadata["fixed"]) for r in refs]
        anchor = anchors.get(scene)
        start = [verify(dict(anchor,id="ANCHOR"),anchor=True)] if isinstance(anchor,dict) else []
        states = [verify(a) for a in assets]
        result["scenes"].append(dict(scene_id=scene,segments=segments,sources=start+states,role_references=references,
                                     reference_warnings=warnings,sheets=[],pages=[]))
    for number,scene in enumerate(result["scenes"],1):
        sources = scene["sources"]
        n_anchor = int(bool(sources[0].get("anchor")))
        per_page = 6-n_anchor
        for page, start in enumerate(range(n_anchor,len(sources),per_page),1):
            indices = list(range(n_anchor))+list(range(start,min(start+per_page,len(sources))))
            images = [sources[i] for i in indices]
            jpeg = render_page(images,scene["role_references"],files.store.read_blob)
            filename = f"scene-{number:02d}-{page:02d}.jpg"
            scene["sheets"].append(filename)
            scene["pages"].append(dict(filename=filename,sha256=files.store.put_blob(jpeg),sourceIndexes=indices))
    result["fingerprint"] = sha256_bytes(canonical(result))
    return result


def export(result, store, directory):
    """Write a self-contained contact index and full-resolution originals into a new directory."""
    requested = Path(directory)
    if not requested.is_absolute():
        raise ValueError("请选择新目录的绝对路径")
    out = requested.resolve()
    try:
        out.mkdir(parents=True,exist_ok=False)
    except FileExistsError as exc:
        raise ValueError("对照目录已存在，不覆盖") from exc
    originals = out/"originals"
    originals.mkdir()
    seen = {}
    detail_template = (Path(__file__).with_name("delivery_templates") / "contact-detail.html").read_text(encoding="utf-8")
    def link(item):
        digest = item["sha256"]
        if digest not in seen:
            raw = store.read_blob(digest)
            with Image.open(BytesIO(raw)) as im:
                ext = (im.format or "bin").lower()
            rel = f"originals/{digest}.{ext}"
            (out/rel).write_bytes(raw)
            (out/f"detail-{digest}.html").write_text(detail_template.replace("__ORIGINAL__", rel), encoding="utf-8")
            seen[digest] = rel
        label = escape(str(item.get("reference_label") or item.get("name") or item["id"]))
        return f'<a href="{seen[digest]}">{label}</a> · <a href="detail-{digest}.html" aria-label="放大细节 · {label}">放大细节</a>'

    html = ['<!doctype html><html lang="zh"><meta charset="utf-8"><title>场景对照</title>',
            '<style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:24px}img{max-width:100%;height:auto}li{margin:6px}</style>',
            '<h1>场景对照</h1><p>视觉审核待进行。点击素材名称可打开原图。</p>']
    if result["unmapped_asset_ids"]:
        html.append('<p>未登记场景：'+escape('、'.join(result["unmapped_asset_ids"]))+'</p>')
    for scene in result["scenes"]:
        html.append('<h2>'+escape(scene["scene_id"])+'</h2><p>相关段：'+escape('、'.join(scene["segments"]))+'</p>')
        if not any(s.get("anchor") for s in scene["sources"]):
            html.append('<p>未登记锁定图</p>')
        html.extend('<p>'+escape(w)+'</p>' for w in scene["reference_warnings"])
        for page in scene["pages"]:
            (out/page["filename"]).write_bytes(store.read_blob(page["sha256"]))
            html.append('<img alt="场景与角色对照" src="'+page["filename"]+'">')
            html.append('<ol>'+''.join('<li>'+link(scene["sources"][i])+'</li>' for i in page["sourceIndexes"])+'</ol>')
            if scene["role_references"]:
                html.append('<ol>'+''.join('<li>R'+str(i)+' · '+('身份原图' if r["reference_kind"]=="identity" else '服装参考')+
                            ' · '+link(r)+'</li>' for i,r in enumerate(scene["role_references"],1))+'</ol>')
    (out/"index.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (out/"index.html").write_text('\n'.join(html)+"</html>\n",encoding="utf-8")
    return dict(index=str(out/"index.html"),originals=len(seen),pages=sum(len(s["pages"]) for s in result["scenes"]))
