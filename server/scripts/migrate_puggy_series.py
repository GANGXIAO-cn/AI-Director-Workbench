"""Import the approved local series into one existing PUGGY ROOM project."""
import argparse
import json
from pathlib import Path
import time

from workbench.db import dumps, initialize, new_id, transaction
from workbench.images import ImagesMixin
from workbench.storage import Store, sha256_bytes


class Importer(ImagesMixin):
    clock = staticmethod(time.time)

    def __init__(self, data):
        self.store = Store(data)
        self.db_path = self.store.db_path


def migrate(data, project_id, source):
    wb = Importer(data)
    initialize(wb.db_path)
    source = source.resolve()
    with transaction(wb.db_path) as db:
        project = db.execute("SELECT config_id FROM project WHERE id=?", (project_id,)).fetchone()
        if not project or project[0] != "puggy":
            raise ValueError("目标须为现有 PUGGY ROOM 项目")

        def record(path, section, title=None, content=None):
            file = (source / path).resolve()
            if source not in file.parents:
                raise ValueError(path)
            raw = file.read_bytes() if content is None else content
            digest = wb.store.put_blob(raw)
            if wb.store.read_blob(digest) != raw:
                raise ValueError(path)
            previous = db.execute("SELECT sha256 FROM project_series_file WHERE project_id=? AND path=?",
                                  (project_id, path)).fetchone()
            if previous and previous[0] != digest:
                raise ValueError("已导入的来源发生变化，请按新版本处理：" + path)
            db.execute("INSERT OR IGNORE INTO project_series_file VALUES(?,?,?,?,?)",
                       (project_id, path, digest, section, title or path))
            return raw

        paths = {
            "系列设定.md":"角色与系列", "资产清单.json":"角色与系列", "声音设定.md":"角色与系列",
            "制作基准/角色外观标准.json":"角色与系列", "制作基准/视频提示词标准.json":"制作基准",
            "制作基准/固定生产资产.json":"制作基准", "制作基准/家庭空间约束.md":"制作基准",
            "制作基准/家庭固定资产建设/家庭布局_A.png":"制作基准",
            "制作基准/家庭固定资产建设/检查图/走廊门洞标注_R2.png":"制作基准",
            "制作基准/家庭固定资产建设/用户选择记录.md":"制作基准",
            "制作基准/家庭固定资产建设/故事房屋事实对照.md":"制作基准",
            "复用资产库/index.json":"制作基准", "选题池/candidates.json":"选题与台账",
            "系列台账.json":"选题与台账", "剧情机制库/机制库总览.md":"剧情机制",
            "剧情机制库/mechanisms.json":"剧情机制", "剧情机制库/sources.json":"剧情机制",
        }
        documents = {path:record(path, section) for path, section in paths.items()}
        for pattern, section in [
            ("episodes/*/故事稿.md","历史故事"), ("episodes/*/*版/故事稿.md","历史故事"),
            ("episodes/*/review.md","历史审核"), ("episodes/*/*版/review.md","历史审核"),
            ("episodes/*/episode.json","制作清单"), ("episodes/*/*版/episode.json","制作清单"),
            ("故事组/*/*.md","连续故事组"), ("故事组/*/*.json","连续故事组"), ("故事组/*/共享素材/*.png","连续故事组"),
        ]:
            for path in sorted(source.glob(pattern)):
                record(str(path.relative_to(source)), section)

        registry = json.loads(documents["复用资产库/index.json"])["assets"]
        identities = json.loads(documents["资产清单.json"])["assets"]
        previous_assets = {}
        for item in identities + registry:
            identity = "category" not in item
            path = item["path"] if identity else "复用资产库/" + item["path"]
            existed = db.execute("SELECT 1 FROM project_series_file WHERE project_id=? AND path=?", (project_id,path)).fetchone()
            raw = record(path, "角色原图" if identity else "", item.get("name") or item["description"])
            if sha256_bytes(raw) != item["sha256"]:
                raise ValueError("原图指纹不匹配：" + path)
            if existed:
                continue
            name = item.get("name") or item["description"]
            category = "identity" if identity else {"character":"costume"}.get(item["category"], item["category"])
            prior = previous_assets.get(item["id"], {})
            asset = wb.register_asset(project_id, name, raw, category, roles=[item["id"]] if identity else [],
                                      origin="series_import", asset_id=prior.get("assetId"), source_version_id=prior.get("versionId"),
                                      note=dumps(item), db=db)
            previous_assets[item["id"]] = asset
            usage = item["id"] + "@" + item["version"] + " · " + (item.get("purpose") or "固定角色身份原图")
            limits = "；".join(item.get("conditions", []) + item.get("defects", []))
            if item["id"] == "cream-garage-driveway":
                limits = "旧车库仅作历史追溯，当前住宅前立面采用 milo-home-front@v1。" + limits
            status = "approved" if identity or item["status"] == "可复用" else "limited"
            observation = " · ".join(filter(None, [item.get("source_episode"),item.get("review_scope"),item.get("approval_basis")]))
            db.execute("INSERT INTO library_entry VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (new_id(),project_id,asset["versionId"],usage,None,limits,observation,status,None,time.time()))

        path = "选题池/candidates.json"
        digest = sha256_bytes(documents[path])
        for card in json.loads(documents[path])["candidates"]:
            code = card["id"]
            origin = f"{path}#{code}@{digest}"
            existing = db.execute("SELECT origin FROM topic WHERE project_id=? AND code=?", (project_id,code)).fetchone()
            if existing:
                if existing[0] != origin:
                    raise ValueError("同号选题已有其他来源：" + code)
                continue
            tid = new_id()
            db.execute("INSERT INTO topic VALUES(?,?,?,?,?,?,?,?,?,?)", (tid,project_id,code,card["cn"],
                       "、".join(card.get("mechanisms", [])),card.get("lead",""),card["status"],None,origin,time.time()))
            db.execute("INSERT INTO topic_source VALUES(?,?,?)", (tid,path,digest))

        ledger = json.loads(documents["系列台账.json"])
        index = ["系列台账中的既有故事（按原台账顺序）", ledger["ordering_note"]]
        for ep in ledger["episodes"]:
            index.append(" · ".join(str(ep[k]) for k in ("id","title","comedy_mechanism","ending","remaining_consequence") if ep.get(k)))
        index += ["系列记忆点"] + [r["status"]+" · "+r["text"] for r in ledger["memory_points"]]
        index += ["可选剧情机制"] + [r["id"]+" · "+r["name"]+" · "+r["one_line"]
                                    for r in json.loads(documents["剧情机制库/mechanisms.json"])["mechanisms"]]
        record("故事开发索引.md", "选题与台账", content="\n\n".join(index).encode())

        entries = [dict(r) for r in db.execute(
            "SELECT path,sha256,section,title FROM project_series_file WHERE project_id=? AND section<>'' ORDER BY section,path",
            (project_id,))]
        catalog = dumps(entries).encode()
        record("系列资料目录.json", "", content=catalog)
        if not db.execute("SELECT 1 FROM knowledge WHERE project_id=? AND kind='series_catalog'", (project_id,)).fetchone():
            db.execute("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (new_id(),project_id,"series_catalog","系列创作资料",catalog.decode(),
                        "系列资料目录.json",sha256_bytes(catalog),"indexed",None,time.time()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("project_id")
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    migrate(args.data, args.project_id, args.source)
