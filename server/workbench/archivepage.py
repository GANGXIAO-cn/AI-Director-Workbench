"""Check the content shown by a historical page using only bytes in its receipt."""
from html.parser import HTMLParser
import posixpath
import re
from urllib.parse import unquote, urlsplit

from .deliverypage_check import Page, check_uploads
from .storage import sha256_bytes

PAGE = "正式交付/00_图文对照.html"


class HistoricalPage(Page):
    def __init__(self):
        super().__init__()
        self.paragraph = None
        self.heading_text = None
        self.legacy = None
        self.container = ""
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag,attrs)
        attrs = dict(attrs)
        if self.legacy is not None and tag == self.container:
            self.depth += 1
        elif self.current is not None and self.asset is None and self.legacy is None and (
                tag == "figure" or tag in ("div","article") and set(attrs.get("class","").split()) & {"input","ref","card"}):
            self.container, self.depth = tag, 1
            self.legacy = dict(links=[], media=[], text=[])
            self.current.setdefault("legacy",[]).append(self.legacy)
        if self.legacy is not None:
            if tag=="a":
                self.legacy["links"].append(attrs.get("href"))
                if self.asset is None and self.current is not None and self.current["prompt_links"]:
                    self.current["prompt_links"].pop()
            if tag in ("img","video","audio"):
                self.legacy["media"].append((tag,attrs.get("src")))
            if tag in ("p","br","strong","figcaption","small","a"):
                self.legacy["text"].append("\n")
        if tag == "p" and self.asset is not None:
            self.paragraph = []
        if tag == "h2" and self.current is not None:
            self.heading_text = []

    def handle_data(self, text):
        super().handle_data(text)
        if self.legacy is not None:
            self.legacy["text"].append(text)
        if self.paragraph is not None:
            self.paragraph.append(text)
        if self.heading_text is not None:
            self.heading_text.append(text)

    def handle_endtag(self, tag):
        if self.legacy is not None and tag == self.container:
            self.depth -= 1
            if self.depth == 0:
                self.legacy = None
        if tag == "section":
            self.legacy = None
            self.depth = 0
        if tag == "p" and self.paragraph is not None:
            value = "".join(self.paragraph).strip()
            if self.asset is not None and value.startswith("作用镜头："):
                self.asset.setdefault("visible_shots",[]).append(value)
            self.paragraph = None
        if tag == "h2" and self.heading_text is not None:
            if self.current is not None:
                self.current["heading"] = "".join(self.heading_text).strip()
            self.heading_text = None
        super().handle_endtag(tag)


def legacy_inputs(actual, segment, assets, matches):
    refs = sorted(segment["references"],key=lambda r:r["upload_order"])
    if len(actual["legacy"])!=len(refs):
        raise ValueError("页面实际上传数量不符")
    for card, ref in zip(actual["legacy"],refs):
        asset = assets[ref["asset_id"]]
        text = [line.strip() for line in "".join(card["text"]).splitlines() if line.strip()]
        line = next((ln for ln in text if re.match(r"^(?:上传|\d|图片|视频|音频|@)",ln)),"")
        label = re.match(r"^(?:(?:上传)?(\d+)\s*[./·]\s*)?(图片\d+|视频\d+|音频\d+|@(?:Image|Video|Audio)\d+)(?:$|\s*[·/])",line)
        if not label or label[1] and int(label[1])!=ref["upload_order"] or label[2]!=ref["label"]:
            raise ValueError("页面上传序号或标签不符："+ref["asset_id"])
        visible_id = line[label.end():].strip().split(" @ ",1)[0]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*",visible_id) and visible_id!=asset["id"]:
            raise ValueError("页面素材编号不符："+asset["id"])
        if not card["links"] or not all(matches(link,asset["path"]) for link in card["links"]):
            raise ValueError("打开素材的链接不符："+asset["id"])
        media = card["media"]
        expected = "img" if asset["type"]=="image" else asset["type"]
        if len(media)!=1 or media[0][0]!=expected or not matches(media[0][1],asset["path"]):
            raise ValueError("素材预览错误："+asset["id"])
        if ref.get("shots"):
            shown = []
            for line in text:
                marked = re.search(r"(?:^|[；;])作用(?:镜头)?[：:]?\s*(S[^；;]*)$",line)
                compact = re.search(r" / (S[^·]*)(?:\s*· 不沿用：.*)?$",line)
                if marked or compact:
                    shown.append([shot.strip() for shot in re.split("[、,，/]",(marked or compact)[1])])
            if shown != [ref["shots"]]:
                visible = "、".join("、".join(s) for s in shown) or "未列出"
                raise ValueError(f"页面作用镜头不符：{asset['id']}，页面{visible}，清单{'、'.join(ref['shots'])}")
    if len(actual["prompt_links"])!=1 or not matches(actual["prompt_links"][0],segment["prompt"]):
        raise ValueError("TXT下载链接不符："+segment["id"])


class CastCards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.versions = []
        self.cards = []
        self.in_cast = False
        self.card = None
        self.field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and attrs.get("id") == "episode-cast":
            self.in_cast = True
            self.versions.append(attrs.get("data-character-contract"))
        if not self.in_cast:
            return
        if tag == "li":
            self.card = dict(role=attrs.get("data-cast-role"), h3=[], p=[], links=[], images=[])
            self.cards.append(self.card)
        if self.card is None:
            return
        if tag in ("h3","p"):
            self.field = tag
            self.card[tag].append("")
        elif tag == "a":
            self.card["links"].append((attrs.get("data-cast-asset"),attrs.get("href")))
        elif tag == "img":
            self.card["images"].append((attrs.get("alt"),attrs.get("src")))

    def handle_endtag(self, tag):
        if tag == self.field:
            self.field = None
        if tag == "li":
            self.card = None
            self.field = None
        if tag == "article":
            self.in_cast = False

    def handle_data(self, value):
        if self.card is not None and self.field is not None:
            self.card[self.field][-1] += value


class MissingPageBasis(ValueError):
    """A previous receipt did not freeze the mapping for an external identity."""


def checked_content(archive, data):
    html = archive.text(PAGE)
    page = HistoricalPage()
    page.feed(html)
    page.close()
    if not isinstance(data.get("segments"),list) or not data["segments"]:
        raise ValueError("清单没有可核对的分段")
    assets = {a["id"]:a for a in data["assets"]}
    versions = {}
    def matches(url,name):
        if not isinstance(url,str):
            return False
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        if parsed.netloc or parsed.query or parsed.fragment or not path:
            return False
        if parsed.scheme and not (parsed.scheme=="file" and posixpath.isabs(path) and data.get("shot_reference_version")!=1):
            return False
        basis = archive.metadata
        if posixpath.isabs(name):
            if posixpath.isabs(path):
                return posixpath.normpath(path) == name
            if "pageExternalPaths" not in basis:
                raise MissingPageBasis("旧回执未保存外部身份图的原页面位置")
            return posixpath.normpath(path) == basis["pageExternalPaths"].get(name)
        if posixpath.isabs(path):
            if not basis.get("pageOriginalRoot"):
                raise MissingPageBasis("旧回执未保存页面的原集目录")
            path = posixpath.relpath(path,posixpath.join(basis["pageOriginalRoot"],posixpath.dirname(PAGE)))
        target = posixpath.normpath(posixpath.join(posixpath.dirname(PAGE),path))
        return archive.metadata["aliases"].get(target,target) == archive.local(name)

    def original(aid):
        if aid not in versions:
            asset = assets[aid]
            raw = archive.identity_bytes(asset)
            digest = sha256_bytes(raw)
            if digest != asset["sha256"]:
                raise ValueError("页面引用素材与登记指纹不符："+aid)
            versions[aid] = digest
        return versions[aid]

    def check_roles():
        role_details = []
        if data.get("character_contract_version")==1:
            cards = CastCards()
            cards.feed(html)
            cards.close()
            roles = data["cast"]
            if cards.versions != ["1"] or [c["role"] for c in cards.cards] != list(roles) or page.links.count("#episode-cast") != 1:
                raise ValueError("页面缺少完整本集角色设定区或导航")
            for card in cards.cards:
                role = card["role"]
                spec = roles[role]
                segments = [s["id"] for s in data["segments"] if role in s["on_screen_roles"]]
                paras = ["出镜段："+"、".join(segments)]
                aid = None
                if spec["identity_required"]:
                    aid = spec["identity_asset_id"]
                    path = assets[aid]["path"]
                    if not (len(card["links"])==len(card["images"])==1 and card["links"][0][0]==aid
                            and card["images"][0][0]==spec["name"] and matches(card["links"][0][1],path)
                            and matches(card["images"][0][1],path)):
                        raise ValueError("页面角色设定身份图或打开原图的链接不符："+role)
                    original(aid)
                    paras.append(spec.get("appearance","沿用系列固定身份与服装标准"))
                else:
                    if card["links"] or card["images"]:
                        raise ValueError("页面角色设定混入未登记身份图："+role)
                    paras.append("无需独立基准："+spec["reason"])
                if card["h3"] != [spec["name"]] or card["p"] != paras:
                    raise ValueError("页面角色设定姓名、外观或适用段落不符："+role)
                role_details.append(dict(role=role,name=spec["name"],segments=segments,assetId=aid))
        return role_details
    issues = []
    def issue(key, exc):
        issues.append(dict(location=key, state="incomplete" if isinstance(exc,MissingPageBasis) else "failed", message=str(exc)))
    role_details = []
    role_state = "passed" if data.get("character_contract_version")==1 else "not_declared"
    try:
        role_details = check_roles()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        issue("角色设定",exc)
        role_state = issues[-1]["state"]
    rows = []
    sections = [s for s in page.sections if s["id"] is not None or s["pre_count"]]
    if len(sections) != len(data["segments"]):
        issue("分段导航",ValueError("页面段数不符"))
    for index, segment in enumerate(data["segments"]):
        sid = segment["id"]
        result = dict(segment=sid,textState="failed",mappingState="failed",state="failed",inputs=[])
        rows.append(result)
        actual = sections[index] if index<len(sections) else None
        try:
            raw = archive.read(segment["prompt"])
            result["promptSha256"] = sha256_bytes(raw)
            if actual is None:
                raise ValueError("页面缺少此段")
            if actual["id"] is None and data.get("shot_reference_version") != 1:
                if actual.get("dom_id") in ("seg-"+sid,"segment-"+sid) or actual.get("heading","").split(" · ",1)[0]==sid:
                    actual["id"] = sid
            if actual["id"] != sid or actual["pre_count"] != 1:
                raise ValueError("页面段号或提示词复制区与清单不符")
            expected = raw.decode("utf-8")
            shown = actual["text"]
            if expected != shown:
                offset = next((i for i,(l,r) in enumerate(zip(expected,shown)) if l!=r),min(len(expected),len(shown)))
                start = max(0,offset-20)
                result["difference"] = dict(offset=offset,line=expected.count("\n",0,offset)+1,
                                            expected=expected[start:offset+60],page=shown[start:offset+60])
                raise ValueError("页面复制区与独立 TXT 原文不符")
            result["textState"] = "passed"
        except (KeyError,ValueError,TypeError,OSError) as exc:
            issue(sid,exc)
        try:
            if actual is None or actual["id"]!=sid:
                raise ValueError("未能定位本段素材区")
            if data.get("shot_reference_version") != 1:
                for asset in actual["assets"]:
                    if "shot_labels" not in asset and asset.get("visible_shots"):
                        asset["shot_labels"] = asset["visible_shots"]
            if actual.get("legacy") and not actual["assets"] and data.get("shot_reference_version")!=1:
                legacy_inputs(actual,segment,assets,matches)
            else:
                check_uploads(actual,segment,assets,data,matches)
            refs = sorted(segment["references"],key=lambda r:r["upload_order"])
            result["inputs"] = [dict(label=r["label"],assetId=r["asset_id"],sha256=original(r["asset_id"]),
                                     shots=r.get("shots",[])) for r in refs]
            result["mappingState"] = "passed"
        except (KeyError,ValueError,TypeError,OSError) as exc:
            issue(sid,exc)
            result["mappingState"] = issues[-1]["state"]
            if actual and actual["id"]==sid and actual["assets"]:
                discrepancies = []
                refs = sorted(segment["references"],key=lambda r:r["upload_order"])
                for ref, card in zip(refs,actual["assets"]):
                    asset = assets.get(ref["asset_id"])
                    if not asset or not isinstance(asset.get("path"),str):
                        continue
                    urls = card["links"] + [src for _,src in card["media"]]
                    try:
                        wrong = [str(url) for url in dict.fromkeys(urls) if not matches(url,asset["path"])]
                    except (MissingPageBasis,ValueError):
                        continue
                    if wrong:
                        discrepancies.append(dict(label=ref["label"],assetId=asset["id"],expectedPath=asset["path"],
                                                  pageLinks=[unquote(url) for url in wrong]))
                if discrepancies:
                    result["linkDifferences"] = discrepancies
        result["state"] = result["mappingState"] if result["textState"]=="passed" else "failed"
        result["message"] = "；".join(i["message"] for i in issues if i["location"]==sid)
    states = {i["state"] for i in issues}
    state = "failed" if "failed" in states else "incomplete" if issues else "passed"
    texts = sum(r["textState"]=="passed" for r in rows)
    inputs = sum(r["mappingState"]=="passed" for r in rows)
    role_summary = {"not_declared":"未声明页面角色契约","incomplete":"页面角色设定待核对","failed":"页面角色设定不符",
                    "passed":f"{len(role_details)} 个角色设定通过"}[role_state]
    return dict(state=state, message=f"{role_summary}；{texts}/{len(rows)} 段 TXT、{inputs}/{len(rows)} 段上传映射核对通过"
                + ("；"+issues[0]["location"]+" · "+issues[0]["message"] if issues else ""),
                details=dict(pageSha256=sha256_bytes(archive.read(PAGE)),roleState=role_state,roles=role_details,segments=rows,issues=issues))
