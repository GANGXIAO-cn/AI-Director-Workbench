"""Pinned authoring references and product art specific to a project profile."""
from io import BytesIO
import json
from pathlib import Path
import re

from PIL import Image

from .db import connect, dumps, transaction
from .errors import Rejected, row
from .methodpack import REPO
from .storage import sha256_bytes

LIBRARIES = REPO / "configs/project-libraries"
WRITING = {"story", "script", "plan", "prompts", "publish"}


def catalog(ref):
    if not ref:
        return None, {"products": [], "sections": []}
    if not re.fullmatch(r"[a-z][a-z0-9_-]*/v[1-9][0-9]*", ref):
        raise Rejected("项目资料版本无效")
    root = LIBRARIES / ref
    info = json.loads((root / "index.json").read_text())
    if info["id"] != ref:
        raise Rejected("项目资料版本不符")
    return root, info


def original(root, name, fingerprint):
    path = (root / name).resolve()
    if root.resolve() not in path.parents:
        raise Rejected("项目资料超出当前版本目录")
    data = path.read_bytes()
    if sha256_bytes(data) != fingerprint:
        raise Rejected("项目资料原件指纹不符")
    return data


def get_item(rows, key, value):
    found = next((item for item in rows if item[key] == value), None)
    if found is None:
        raise Rejected("当前项目资料中没有这条记录")
    return found


class ProjectLibraryMixin:
    def _project_resources(self, db, project_id, episode_id=None):
        from .sourcefiles import production
        if episode_id:
            self._episode_in(db, episode_id, project_id)
            ref = db.execute("SELECT library_ref FROM episode_resource_version WHERE episode_id=?", (episode_id,)).fetchone()
            if ref:
                return catalog(ref[0])
            # Episodes created before this catalog existed use their profile’s first catalog.
            config = row(db, "SELECT config_id FROM project WHERE id=?", project_id)
            return catalog("puggy/v1" if config["config_id"] == "puggy" else None)
        return catalog(production(db, project_id).get("projectLibrary"))

    def project_resources(self, project_id, episode_id=None):
        with connect(self.db_path) as db:
            _, items = self._project_resources(db, project_id, episode_id)
            return items

    def project_product_image(self, project_id, code, episode_id=None):
        with connect(self.db_path) as db:
            root, items = self._project_resources(db, project_id, episode_id)
        item = get_item(items["products"], "code", code)
        return original(root, item["path"], item["sha256"])

    def _project_section(self, root, section):
        data = original(root, section["path"], section["sourceSha256"])
        text = "".join(data.decode().splitlines(keepends=True)[section["start"]-1:section["end"]])
        if sha256_bytes(text.encode()) != section["sha256"]:
            raise Rejected("项目资料小节指纹不符")
        return dict(section, text=text)

    def project_section(self, project_id, section_id, episode_id=None):
        with connect(self.db_path) as db:
            root, items = self._project_resources(db, project_id, episode_id)
        return self._project_section(root, get_item(items["sections"], "id", section_id))

    def episode_project_sections(self, episode_id, stage):
        with connect(self.db_path) as db:
            record = db.execute("SELECT refs FROM stage_project_reference WHERE episode_id=? AND stage=?",
                                (episode_id, stage)).fetchone()
            return json.loads(record[0]) if record else []

    def set_project_sections(self, episode_id, stage, section_ids):
        if stage not in WRITING or not isinstance(section_ids, list) or not all(isinstance(k, str) for k in section_ids):
            raise Rejected("项目小节须指定创作阶段和小节编号")
        with transaction(self.db_path) as db:
            ep = self._episode_in(db, episode_id)
            root, items = self._project_resources(db, ep["project_id"], episode_id)
            selected = [self._project_section(root, get_item(items["sections"], "id", key))
                        for key in dict.fromkeys(section_ids)]
            db.execute("INSERT INTO stage_project_reference VALUES(?,?,?) ON CONFLICT(episode_id,stage) DO UPDATE SET refs=excluded.refs",
                       (episode_id, stage, dumps(selected)))
        return selected

    def use_project_product(self, episode_id, code, command_id):
        with transaction(self.db_path) as db:
            def run():
                ep = self._episode_in(db, episode_id)
                root, items = self._project_resources(db, ep["project_id"], episode_id)
                item = get_item(items["products"], "code", code)
                prior = db.execute("SELECT version_id FROM episode_product WHERE episode_id=? AND code=?", (episode_id, code)).fetchone()
                if prior:
                    return dict(versionId=prior[0], originalSha256=item["sha256"])
                source = original(root, item["path"], item["sha256"])
                # Keep the exact WebP. Register a lossless PNG for image generation and delivery.
                with Image.open(BytesIO(source)) as img:
                    png = BytesIO()
                    img.save(png, format="PNG")
                ref = dict(item, originalSha256=self.store.put_blob(source))
                asset = self.register_asset(ep["project_id"], item["name"], png.getvalue(), "product_reference",
                                            episode_id=episode_id, origin="project_library", note=dumps(ref), db=db)
                db.execute("INSERT INTO episode_product VALUES(?,?,?,?)", (episode_id, code, asset["versionId"], dumps(ref)))
                return dict(versionId=asset["versionId"], originalSha256=ref["originalSha256"])
            return self._command(db, command_id, "use_project_product", dict(episode=episode_id, code=code), run)

    def save_product_placement(self, episode_id, code, shot_id, intent, scene_version=None, observation="", command_id=None):
        from .storage import canonical
        with transaction(self.db_path) as db:
            def run():
                product = row(db, "SELECT source,version_id FROM episode_product WHERE episode_id=? AND code=?", episode_id, code,
                              missing="请先登记本集所选商品主图")
                plan = self._adopted_content(db, episode_id, "plan") or {}
                shot = get_item(plan.get("shots", []), "id", shot_id)
                if not isinstance(intent, str) or not intent.strip():
                    raise Rejected("请填写使用理由、位置与朝向、需保留的识别特征")
                if scene_version:
                    asset = self.asset_version(scene_version, db)
                    if asset["episode_id"] != episode_id or asset["kind"] != "image" or asset["category"] not in ("state","scene","grid"):
                        raise Rejected("入景证据须选本集的场面图、场景图或九宫格")
                    if not isinstance(observation, str) or not observation.strip():
                        raise Rejected("请记录实际看图所见的款式、自然性及接续")
                    # A re-upload of the catalog image cannot establish placement in a scene.
                    ref = self.asset_version(product["version_id"], db)
                    if asset["sha256"] in (ref["sha256"], json.loads(product["source"])["sha256"]):
                        raise Rejected("单独的商品主图不能作为场面证据")
                db.execute("INSERT INTO product_placement VALUES(?,?,?,?,?,?,?) ON CONFLICT(episode_id,code,shot_id) "
                           "DO UPDATE SET shot_hash=excluded.shot_hash,intent=excluded.intent,scene_version=excluded.scene_version,observation=excluded.observation",
                           (episode_id, code, shot_id, sha256_bytes(canonical(shot)), intent.strip(), scene_version,
                            observation.strip() if scene_version else ""))
                return {"ok": True}
            return self._command(db, command_id, "save_product_placement",
                                 [episode_id, code, shot_id, intent, scene_version, observation], run)

    def product_placements(self, episode_id):
        from .sourcefiles import production
        from .storage import canonical
        with connect(self.db_path) as db:
            ep = self._episode_in(db, episode_id)
            rule = db.execute("SELECT minimum FROM episode_product_rule WHERE episode_id=?", (episode_id,)).fetchone()
            minimum = rule[0] if rule else production(db, ep["project_id"]).get("minimumProductPlacements", 0)
            plan = self._adopted_content(db, episode_id, "plan") or {}
            prompts = self._adopted_content(db, episode_id, "prompts") or {}
            products = {p["code"]:json.loads(p["source"]) for p in db.execute("SELECT * FROM episode_product WHERE episode_id=?", (episode_id,))}
            required = {r[0] for r in db.execute("SELECT asset_version_id FROM image_choice WHERE episode_id=? AND mark='required'", (episode_id,))}
            records = [dict(r) for r in db.execute("SELECT * FROM product_placement WHERE episode_id=? ORDER BY code,shot_id", (episode_id,))]
        shots = {shot["id"]:shot for shot in plan.get("shots", [])}
        for item in records:
            shot = shots.get(item["shot_id"])
            sid = shot.get("segment") if shot else None
            version = item["scene_version"]
            refs = prompts.get("segments", {}).get(sid, {}).get("refs", [])
            issue = ""
            if not shot or sha256_bytes(canonical(shot)) != item["shot_hash"]:
                issue = "关联镜头已变动，须复核入景位置"
            elif not version:
                issue = "待登记实际场面与看图观察"
            elif version not in required:
                issue = "场面图未标必用"
            elif not any(r.get("assetVersionId") == version for r in refs):
                issue = "采用提示词的对应段未引用该场面图"
            item.update(segment=sid, issue=issue, product=products[item["code"]], dynamic="not_tested")
        return dict(minimum=minimum, products=list(products.values()), records=records,
                    verified=sum(not item["issue"] for item in records))
