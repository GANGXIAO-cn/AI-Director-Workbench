"""Read mounted screenplay indexes and director packs. Persist only chosen material."""
import base64
import binascii
from io import BytesIO
import os
import tempfile
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from contextlib import closing, contextmanager
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from zipfile import BadZipFile, ZipFile

from .db import connect, dumps, transaction
from .errors import Conflict, Rejected
from .methodpack import REPO, pack_dir, stage_map
from .palette_inputs import BODY, IMAGE, snapshot_inputs


def inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise Rejected("资料不在挂载目录中，或文件已移动")
    return path


def database(root):
    path = inside(root, "knowledgeIndex.sqlite")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def fulltext_ids(root, query):
    words = re.findall(r"[^\W_]+", query.casefold())
    grams = {f"g{ord(a):06x}{ord(b):06x}" for word in words for a, b in zip(word, word[1:])}
    if not grams:
        return []
    match = " OR ".join(sorted(grams))
    sql = "SELECT rowid FROM search WHERE search MATCH ? ORDER BY rank LIMIT 2000"
    try:
        with closing(database(root)) as db:
            return [r[0] for r in db.execute(sql, (match,))]
    except sqlite3.OperationalError as exc:
        if "contentless_delete" not in str(exc):
            raise
        # Some Python builds link older SQLite than the system CLI. All terms are hex.
        cli = shutil.which("sqlite3")
        if cli:
            result = subprocess.run([cli, "-init", "/dev/null", "-readonly", str(root / "knowledgeIndex.sqlite"),
                                     sql.replace("?", "'" + match + "'")], capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                return [int(n) for n in result.stdout.splitlines()]
        raise Rejected("全文检索需要支持 contentless_delete 的 SQLite 3.43 或更高版本") from exc


def zip_name(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


@contextmanager
def pack_files(path):
    if path.is_dir():
        files = {p.relative_to(path).as_posix(): p for p in path.rglob("*") if p.is_file()}
        def read(name):
            return inside(path, name).read_bytes()
        yield files, read
    else:
        try:
            with ZipFile(path) as archive:
                files = {zip_name(info): info for info in archive.infolist() if not info.is_dir()}
                def read(name):
                    if files[name].file_size > 24 * 1024 * 1024:
                        raise Rejected("单份色卡资料过大")
                    return archive.read(files[name])
                yield files, read
        except (BadZipFile, FileNotFoundError) as exc:
            raise Rejected("无法读取色卡压缩包，请重新挂载") from exc


def read_card(root, palette_bytes, body_bytes, files):
    palette = json.loads(palette_bytes)
    director = palette["director"]
    body = body_bytes.decode("utf-8-sig")
    summary = re.search(r"核心风格一句话[^\n]*\n\s*>\s*([^\n]+)", body)
    covers = ("assets/film_stills/00_frames_contact_sheet.jpg", "assets/color_palettes/frames-contact-sheet.jpg",
              "assets/color_palettes/影调色卡.png")
    return dict(id=director["id"], name=director["name_zh"], english=director["name_en"],
                category=director["category"], palette=palette["palette"], root=root,
                cover=next((cover for cover in covers if root+"/"+cover in files), ""),
                summary=summary[1].strip() if summary else "", body=body)


@lru_cache(maxsize=4)
def palette_index(path, stamp):
    cards = {}
    with pack_files(path) as (files, read):
        suffix = "/assets/color_palettes/palette.json"
        for name in sorted(files):
            if not name.endswith(suffix):
                continue
            root = name[:-len(suffix)]
            card = read_card(root, read(name), read(root + "/" + BODY), files)
            if card["id"] in cards:
                raise Rejected("色卡编号重复：" + card["id"])
            cards[card["id"]] = card
    if not cards:
        raise Rejected("资料包内未找到导演色卡 palette.json")
    return cards


class ResourcesMixin:
    def resource_mounts(self):
        return {kind: str(self._resource_path(kind) or "") for kind in ("knowledge", "palette")}

    def _resource_path(self, kind):
        if kind not in ("knowledge", "palette"):
            raise Rejected("未知资料类型")
        configured = self.get_setting("mount:" + kind)
        if configured:
            return Path(configured)
        # Known companion package installed beside the workbench. No disk-wide discovery.
        base = REPO.parent / "自媒体 2" / "对标分析"
        pattern = "知识库*/*/knowledgeIndex.sqlite" if kind == "knowledge" else "导演风格与色卡*.zip"
        match = next(iter(sorted(base.glob(pattern))), None)
        return match.parent if match and kind == "knowledge" else match

    def mount_resource(self, kind, path):
        root = Path(path).expanduser().resolve()
        if kind == "knowledge":
            with closing(database(root)) as db:
                db.execute("SELECT path,title,category,kind,sha256 FROM documents LIMIT 1")
        elif kind == "palette":
            palette_index.cache_clear()
            self._cards(root)
        else:
            raise Rejected("未知资料类型")
        self.put_setting("mount:" + kind, str(root))
        return self.resource_mounts()

    def _maintenance_root(self, expected_path):
        root = self._resource_path("knowledge")
        if not root or not expected_path or root.resolve() != Path(expected_path).resolve():
            raise Rejected("挂载目录已改变，请重新打开资料位置")
        return root.resolve()

    def add_script_document(self, expected_path, category, title, content):
        from . import knowledge
        root = self._maintenance_root(expected_path)
        try:
            return knowledge.add_document(root, category, title, content)
        except (OSError, sqlite3.Error) as exc:
            raise Rejected("无法完成添加，请检查目录和索引；已保存的正文可再次更新索引：" + str(exc)) from exc

    def refresh_script_index(self, expected_path):
        from . import knowledge
        root = self._maintenance_root(expected_path)
        try:
            with knowledge.exclusive(root):
                return knowledge.update(root)
        except (OSError, sqlite3.Error) as exc:
            raise Rejected("更新未完成，请检查已挂载目录后重试：" + str(exc)) from exc

    def browse_scripts(self, query="", category="", kind="", page=1, recommended=False, episode_id=None, stage=None):
        root = self._resource_path("knowledge")
        if not root:
            raise Rejected("请先挂载剧本知识库")
        query = query.strip()[:160]
        plain_query=query
        if recommended and episode_id:
            with connect(self.db_path) as db:
                kickoff=self._kickoff(db,episode_id)
                source=self._source_text(db,kickoff) or ""
            vocabulary={"story":"故事 因果 动机","script":"剧本 表演 对白","plan":"分镜 摄影 剪辑","assets":"构图 光影 色彩","prompts":"镜头 场景","publish":"标题 文案"}
            query=" ".join(filter(None,[plain_query, source[:60], vocabulary.get(stage,"")]))
        where = ["category NOT LIKE '90-%'"]
        args = []
        for field, value in (("category", category), ("kind", kind)):
            if value:
                where.append(field + "=?")
                args.append(value)
        if recommended:
            where.append("kind IN ('method','reference')")
        with closing(database(root)) as db:
            categories = [r[0] for r in db.execute("SELECT DISTINCT category FROM documents WHERE category NOT LIKE '90-%' ORDER BY category")]
            selects = "SELECT id,path,title,category,kind,sha256,indexed FROM documents WHERE " + " AND ".join(where)
            ids = fulltext_ids(root, query) if query else []
            if query:
                selects += " AND (instr(lower(path),lower(?))>0 OR id IN (SELECT value FROM json_each(?)))"
                args.extend([plain_query or query, json.dumps(ids)])
            rows = [dict(r) for r in db.execute(selects + " ORDER BY path", args)]
            if query:
                ranks = {i: n for n, i in enumerate(ids)}
                rows.sort(key=lambda r: (bool(plain_query) and plain_query.casefold() not in r["title"].casefold(), ranks.get(r["id"], len(ids))))
            start = max(0, int(page)-1)*40
            shown=rows[start:start+40]
            if recommended and query:
                words=re.findall(r"[^\W_]+",query.casefold())
                terms=sorted(set(words),key=len,reverse=True)
                grams=list(dict.fromkeys(w[i:i+2] for w in words for i in range(len(w)-1)))
                for record in shown:
                    try:
                        raw=inside(root,record["path"]).read_bytes()
                        body=raw.decode("utf-8-sig")
                    except (OSError, Rejected, UnicodeError):
                        record["evidence"]="原文不可读，请核对文件或更新索引"
                        continue
                    if record["sha256"] and sha256(raw).hexdigest()!=record["sha256"]:
                        record["evidence"]="原文已变化，需更新索引"
                        continue
                    first_page=re.search(r"<!--\s*(?:第\s*\d+\s*页|page\b)[^>]*-->",body[:12000],re.I)
                    begin=first_page.end() if first_page else 0
                    for word in [*terms,*grams]:
                        hit=re.compile(re.escape(word),re.I).search(body,begin)
                        if hit:
                            at=hit.start()
                            record["evidence"]=body[max(begin,at-28):hit.end()+65]
                            record["matchOffset"]=max(0,at-180)
                            record["match"] = dict(offset=at,text=hit.group())
                            break
            return dict(total=len(rows), rows=shown, categories=categories)

    def search_script_bodies(self, query, category="", kind="", recommended=False, cursor=None, limit=40):
        from .scriptsearch import search
        root = self._resource_path("knowledge")
        if not root:
            raise Rejected("请先挂载剧本知识库")
        return search(root, query, category, kind, recommended, cursor, limit)

    def script_excerpt(self, path, offset=0, query="", expected_sha256=None):
        root = self._resource_path("knowledge")
        if not root:
            raise Rejected("请先挂载剧本知识库")
        with closing(database(root)) as db:
            row = db.execute("SELECT title,kind,sha256 FROM documents WHERE path=? AND category NOT LIKE '90-%'", (path,)).fetchone()
        if not row or Path(path).suffix.lower() != ".md":
            raise Rejected("该资料不在正式目录")
        data = inside(root, path).read_bytes()
        digest = sha256(data).hexdigest()
        if row["sha256"] and row["sha256"] != digest:
            raise Rejected("原文件指纹已变化，请更新资料索引")
        if expected_sha256 and digest != expected_sha256:
            raise Conflict("所选原文已变化，请重新搜索")
        try:
            body = data.decode("utf-8-sig")
        except UnicodeError as exc:
            raise Rejected("原文无法按 UTF-8 读取") from exc
        start = max(0, min(len(body), int(offset)))
        if query.strip() and not offset:
            at = body.find(query.strip())
            if at >= 0:
                start = max(0, at-180)
        header = body[:4000].split("\n---\n",1)[0]
        metadata = "\n".join(line for line in header.splitlines() if re.match(
            r"^\|\s*(源文件|源类型|提取方式|页数)\s*\|", line))
        return dict(title=row["title"], kind=row["kind"], path=path, sha256=digest, offset=start,
                    length=len(body), text=body[start:start+2400], metadata=metadata)

    def _cards(self, root=None):
        path = root or self._resource_path("palette")
        if not path or not path.exists():
            raise Rejected("请先挂载导演色卡目录或 ZIP")
        try:
            return palette_index(path, path.stat().st_mtime_ns)
        except (KeyError, ValueError, UnicodeError, OSError) as exc:
            raise Rejected("色卡格式或编码有误，请检查资料包") from exc

    def browse_palettes(self, query="", category="", page=1):
        external = self._resource_path("palette")
        catalog = self._cards(external).copy() if external and external.exists() else {}
        custom = self.store.root / "director-palettes"
        if custom.is_dir():
            catalog.update(self._cards(custom))
        cards = catalog.values()
        rows = [dict((k,v) for k,v in card.items() if k not in ("body","root")) for card in cards
                if (not category or card["category"] == category) and query.strip().casefold() in
                (card["name"]+card["english"]+card["id"]+card["body"]).casefold()]
        rows.sort(key=lambda c:c["id"])
        start=max(0,int(page)-1)*24
        return dict(total=len(rows), rows=rows[start:start+24], categories=sorted({c["category"] for c in cards}))

    def add_palette(self, name, summary, colors, image_prompt="", video_prompt="", source="", screenshot=""):
        if not all(isinstance(value,str) and len(value)<=16000 for value in (name,summary,image_prompt,video_prompt,source)):
            raise Rejected("色卡说明与提示词须为文本")
        if not name.strip() or not summary.strip() or not isinstance(colors,list) or not 1<=len(colors)<=12:
            raise Rejected("自定义色卡须有名称、风格说明及 1 至 12 个色值")
        if not all(isinstance(c,str) and re.fullmatch("#[A-Fa-f0-9]{6}",c) for c in colors):
            raise Rejected("色值须使用 #RRGGBB 格式")
        picture = None
        if screenshot:
            if not isinstance(screenshot,str) or len(screenshot)>12*1024*1024:
                raise Rejected("影调截图超过大小限制")
            try:
                picture = base64.b64decode(screenshot,validate=True)
                with Image.open(BytesIO(picture)) as im:
                    if im.format not in ("PNG","JPEG") or im.width*im.height>24_000_000:
                        raise Rejected("截图须为 2400 万像素以内的 PNG 或 JPEG")
                    extension = "jpg" if im.format=="JPEG" else "png"
                    im.verify()
            except (UnidentifiedImageError,Image.DecompressionBombError,ValueError,OSError,binascii.Error) as exc:
                raise Rejected("影调截图无法读取") from exc
        cid = "custom-" + uuid4().hex
        parent = self.store.root/"director-palettes"
        parent.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="palette-",dir=self.store.root))
        def save(path, data):
            file=staging/path
            file.parent.mkdir(parents=True,exist_ok=True)
            file.write_bytes(data if isinstance(data,bytes) else data.encode())
        palette = dict(director=dict(id=cid,name_zh=name.strip(),name_en="",category="我的色卡"),
                       palette=dict(primary=[dict(name_zh=c,hex=c,ratio=1) for c in colors],emotional=[]))
        save("assets/color_palettes/palette.json",dumps(palette))
        save("色卡主文档.md","# "+name.strip()+"\n\n## 核心风格一句话\n> "+summary.strip()+"\n")
        save("assets/references/sources.md",source)
        save("prompts/video_prompts.md",video_prompt)
        save(IMAGE,image_prompt)
        if picture:
            save(f"assets/film_stills/reference.{extension}",picture)
            cover = "frames-contact-sheet.jpg" if extension=="jpg" else "影调色卡.png"
            save("assets/color_palettes/"+cover,picture)
        os.rename(staging, parent/cid)
        palette_index.cache_clear()
        return dict(id=cid, name=name.strip())

    def _palette_location(self, cid):
        if re.fullmatch(r"custom-[a-f0-9]{32}",cid) and (self.store.root/"director-palettes"/cid).is_dir():
            return self.store.root/"director-palettes"
        return self._resource_path("palette")

    def palette_detail(self, card_id):
        location = self._palette_location(card_id)
        card = self._cards(location).get(card_id)
        if not card:
            raise Rejected("所选色卡已不在目录")
        prefix = card["root"] + "/"
        with pack_files(location) as (files, read):
            texts = {n[len(prefix):]:read(n) for n in files if n.startswith(prefix+"prompts/") and n.endswith(".md")}
            prompts = {n:data.decode("utf-8-sig") for n,data in texts.items()}
            stills = sorted(n[len(prefix):] for n in files if n.startswith(prefix+"assets/film_stills/") and n.endswith((".jpg",".png")))
            source_files = ("assets/references/sources.md","assets/references/reference_sources.md",
                            "assets/film_stills/_film_stills_source.md")
            for name in ("色卡主文档.md","assets/color_palettes/palette.json",*source_files,"assets/references/collection_guide.md"):
                if prefix+name in files:
                    texts[name] = read(prefix+name)
            sources = "\n\n".join(texts[name].decode("utf-8-sig") for name in source_files if name in texts)
            # Re-read mutable directory cards from the same bytes used for the source digests.
            if BODY not in texts or "assets/color_palettes/palette.json" not in texts:
                raise Rejected("所选色卡的原文件已移动，请更新目录后重新选择")
            card = read_card(card["root"], texts["assets/color_palettes/palette.json"], texts[BODY], files)
            if card["id"] != card_id:
                raise Rejected("色卡编号已变更，请更新目录后重新选择")
            return dict(card,prompts=prompts,stills=stills,sources=sources,sourceBundle=location.name,
                        collectionGuide=texts.get("assets/references/collection_guide.md",b"").decode("utf-8-sig"),
                        fileHashes={n:sha256(data).hexdigest() for n,data in texts.items()})

    def palette_image(self, card_id, filename):
        location = self._palette_location(card_id)
        card=self._cards(location).get(card_id)
        if not card or not re.fullmatch(r"assets/(film_stills|color_palettes)/[^/\\]+\.(jpg|png)",filename):
            raise Rejected("无效的色卡图片")
        with pack_files(location) as (files,read):
            name=card["root"]+"/"+filename
            if name not in files:
                raise Rejected("资料包中没有此图")
            return read(name)

    def project_palettes(self, project_id):
        return self.get_setting("palette-selection:"+project_id, [])

    def save_palettes(self, project_id, card_ids, expected_hashes=None):
        with connect(self.db_path) as db:
            if not db.execute("SELECT 1 FROM project WHERE id=?", (project_id,)).fetchone():
                raise Rejected("项目不存在")
        chosen=self._palette_snapshots(card_ids)
        key = "palette-selection:"+project_id
        with transaction(self.db_path) as db:
            row = db.execute("SELECT value FROM app_setting WHERE key=?", (key,)).fetchone()
            current = json.loads(row["value"]) if row else []
            if expected_hashes is not None and expected_hashes != [card["sha256"] for card in current]:
                raise Conflict("项目色卡已由其他操作更改，请刷新后再选择")
            db.execute("INSERT INTO app_setting VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, dumps(chosen)))
        return chosen

    def _palette_snapshots(self,card_ids):
        if not isinstance(card_ids,list) or not all(isinstance(cid,str) for cid in card_ids) or len(card_ids)>3 or len(set(card_ids))!=len(card_ids):
            raise Rejected("最多选择三份不同的色卡")
        chosen=[]
        for cid in card_ids:
            card=self.palette_detail(cid)
            custom = self._palette_location(cid) == self.store.root/"director-palettes"
            frozen = dict(id=cid, name=card["name"], source=card["root"], sourceBundle=card["sourceBundle"],
                          fileHashes=card["fileHashes"], snapshotVersion=2, **snapshot_inputs(card, custom))
            frozen["sha256"] = sha256(dumps(frozen).encode()).hexdigest()
            chosen.append(frozen)
        return chosen

    def episode_palettes(self, episode_id, stage):
        if stage not in ("assets","prompts"):
            raise Rejected("本阶段不使用视觉色卡")
        with connect(self.db_path) as db:
            ep=self._episode_in(db,episode_id)
        saved=self.get_setting(f"palette:{episode_id}:{stage}",{"mode":"inherit"})
        project_cards=self.project_palettes(ep["project_id"])
        cards=project_cards if saved["mode"]=="inherit" else saved.get("cards",[])
        return dict(mode=saved["mode"], cards=cards, projectCards=project_cards)

    def set_episode_palettes(self, episode_id, stage, mode, card_ids=None):
        self.episode_palettes(episode_id, stage)
        if mode not in ("inherit","off","custom"):
            raise Rejected("无效的单集色卡使用方式")
        cards=self._palette_snapshots(card_ids or []) if mode=="custom" else []
        if mode=="custom" and not cards:
            raise Rejected("指定色卡时须至少选择一份")
        self.put_setting(f"palette:{episode_id}:{stage}",dict(mode=mode,cards=cards))
        return self.episode_palettes(episode_id,stage)

    def enhancement_catalog(self):
        from .enhancements import catalog
        return [{k: item[k] for k in ("id","title","when","where","stages")} for item in catalog()["methods"]]

    def step_form(self, episode_id, stage):
        if stage not in ("story","script","plan","prompts","publish","assets"):
            raise Rejected("本阶段没有可编辑的需求配置")
        with connect(self.db_path) as db:
            pack, _ = self._episode_method(db, episode_id)
        path = pack_dir(pack["id"]) / "workbench-forms.json"
        if not path.exists():
            if pack["id"]=="short-film@v7":
                raise Rejected("当前方法包缺少本步需求表单")
            return dict(pack=pack["id"], fields={"goal":"本步目标","format_note":"形式与规格","keep":"必须保留的内容"})
        definition = json.loads(path.read_text()).get(stage)
        if not isinstance(definition,dict):
            raise Rejected("方法包缺少本阶段的表单定义")
        section = definition["section"]
        fields = definition["fields"]
        if section not in stage_map(pack).get(stage, ()) or section not in pack["files"]:
            raise Rejected("本步需求缺少本阶段的通用方法依据")
        if not fields or set(fields)-{"goal","format_note","keep"} or not all(isinstance(v,str) and v.strip() for v in fields.values()):
            raise Rejected("方法包的本步需求字段无效")
        return dict(pack=pack["id"], fields=fields, section=section, sourceHash=pack["files"][section]["sha256"])

    def step_config(self, episode_id, stage):
        return self.get_setting(f"step:{episode_id}:{stage}", {})

    def step_history(self, episode_id, stage):
        with connect(self.db_path) as db:
            self._episode_in(db,episode_id)
            rows = db.execute("SELECT body FROM step_revision WHERE episode_id=? AND stage=? ORDER BY revision DESC",
                              (episode_id,stage))
            return [json.loads(r[0]) for r in rows]

    def save_step_config(self, episode_id, stage, route="", goal="", keep="", format_note="", mode="off", references=None,
                         expected_revision=None, enhancements=None):
        if stage not in ("story","script","plan","prompts","publish","assets") or mode not in ("off","match","method"):
            raise Rejected("不支持的阶段或资料模式")
        with connect(self.db_path) as db:
            self._episode_in(db,episode_id)
        saved = self.step_config(episode_id, stage)
        retained = {(r["path"], r["offset"], r["sha256"]): r for r in saved.get("references", [])
                    if isinstance(r.get("text"), str)}
        refs=[]
        if mode!="off":
            for ref in references or []:
                if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or type(ref.get("offset",0)) is not int or not isinstance(ref.get("sha256"),str):
                    raise Rejected("引用须包含原文路径、起点和指纹")
                source = retained.get((ref["path"],ref.get("offset",0),ref["sha256"]))
                if source and expected_revision is not None and expected_revision == saved.get("revision"):
                    item = {k:v for k,v in source.items() if k not in ("reason","match")}
                else:
                    item = self.script_excerpt(ref["path"],ref.get("offset",0))
                if mode=="method" and item["kind"]!="method":
                    raise Rejected("只查方法书时请移除其他类型的引用")
                if item["sha256"]!=ref.get("sha256"):
                    raise Rejected("所选原文已变动，请重新选取")
                entry=dict(item, reason=ref.get("reason") or "借鉴写法与结构")
                match=ref.get("match")
                if match is not None:
                    if not isinstance(match,dict) or type(match.get("offset")) is not int or not isinstance(match.get("text"),str):
                        raise Rejected("正文命中位置格式无效")
                    pos=match["offset"]-item["offset"]
                    term=match["text"]
                    if not term or pos<0 or item["text"][pos:pos+len(term)]!=term:
                        raise Rejected("正文命中与选取段落不符，请重新选择")
                    entry["match"]=dict(offset=match["offset"],text=term)
                refs.append(entry)
        config=dict(route=route,goal=goal,keep=keep,format_note=format_note,mode=mode,references=refs,
                    form=self.step_form(episode_id,stage))
        if enhancements is not None:
            from .enhancements import entries
            entries(enhancements)
            config["enhancements"] = enhancements
        elif "enhancements" in saved:
            config["enhancements"] = saved["enhancements"]
        if not all(isinstance(config[k],str) for k in ("route","goal","keep","format_note")):
            raise Rejected("本步需求各项须为文本")
        with transaction(self.db_path) as db:
            return self._write_step_revision(db,episode_id,stage,config,expected_revision)

    def restore_step_revision(self, episode_id, stage, revision, expected_revision):
        with transaction(self.db_path) as db:
            row = db.execute("SELECT body FROM step_revision WHERE episode_id=? AND stage=? AND revision=?",
                             (episode_id,stage,revision)).fetchone()
            if not row:
                raise Rejected("本阶段没有指定的历史需求版本")
            config = json.loads(row[0])
            config.pop("revision",None)
            return self._write_step_revision(db,episode_id,stage,config,expected_revision)

    def _write_step_revision(self, db, episode_id, stage, config, expected_revision):
        key = f"step:{episode_id}:{stage}"
        stored = db.execute("SELECT value FROM app_setting WHERE key=?", (key,)).fetchone()
        old = json.loads(stored[0]) if stored else {}
        revision = old.get("revision",0)
        if old and revision and config == {k:v for k,v in old.items() if k!="revision"}:
            return old
        if expected_revision is not None and expected_revision != revision:
            raise Conflict("本步配置已由其他页面更新，请重新打开后核对")
        if old and not revision:
            db.execute("INSERT OR IGNORE INTO step_revision VALUES(?,?,0,?)",(episode_id,stage,dumps(dict(old,revision=0))))
        config["revision"] = revision+1
        db.execute("INSERT INTO step_revision VALUES(?,?,?,?)",(episode_id,stage,config["revision"],dumps(config)))
        db.execute("INSERT INTO app_setting VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,dumps(config)))
        return config
