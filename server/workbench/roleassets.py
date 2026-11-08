"""Named, project-configured character baselines. File checks make no visual quality claim."""
from pathlib import Path
import json

from .db import connect, transaction, dumps
from .errors import Rejected
from .storage import sha256_bytes


def check_assets(asset_root, settings):
    root = Path(asset_root).resolve()
    def file(rel, error):
        if not isinstance(rel, str) or Path(rel).is_absolute():
            raise Rejected(error)
        path = (root/rel).resolve()
        if root not in path.parents or not path.is_file():
            raise Rejected(error)
        return path
    path = file(settings["manifest"], "缺少固定角色清单")
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise Rejected("固定角色清单不可读") from exc
    file(settings["series"], "缺少系列设定")
    records = manifest.get("assets") if isinstance(manifest, dict) else None
    expected = settings["roles"]
    if not isinstance(records, list) or len(records) != len(expected) or not all(isinstance(r,dict) for r in records) or {r.get("id") for r in records} != set(expected):
        raise Rejected("固定角色清单须恰好包含：" + "、".join(expected))
    voice = file(settings["voice"], "缺少固定声音设定")
    result = []
    for r in records:
        role = r["id"]
        image = file(r.get("path"), f"{role} 角色原图缺失或超出项目目录")
        if sha256_bytes(image.read_bytes()) != r.get("sha256"):
            raise Rejected(f"{role} 角色原图指纹不匹配")
        if not r.get("version"):
            raise Rejected(f"{role} 缺少版本")
        result.append({k:r.get(k) for k in ("id", "name", "path", "version", "sha256")})
    return dict(roles=result, registrySha256=sha256_bytes(raw), voiceSha256=sha256_bytes(voice.read_bytes()))


class RoleAssetMixin:
    def project_role_assets(self, project_id):
        from .sourcefiles import production
        with connect(self.db_path) as db:
            prod = production(db, project_id)
        return check_assets(prod["assetRoot"], prod["roleAssets"]) if prod.get("roleAssets") else {"roles":[]}

    def use_fixed_role(self, episode_id, role, command_id):
        from .sourcefiles import production
        with transaction(self.db_path) as db:
            def run():
                ep = self._episode_in(db, episode_id)
                prod = production(db, ep["project_id"])
                if not prod.get("roleAssets"):
                    raise Rejected("本项目没有登记固定身份图")
                catalog = check_assets(prod["assetRoot"], prod["roleAssets"])
                item = next((r for r in catalog["roles"] if r["id"] == role), None)
                if not item:
                    raise Rejected("角色不在本项目固定身份清单内")
                old = db.execute("SELECT asset_version_id FROM fixed_role_asset WHERE episode_id=? AND role=? AND sha256=?",
                                 (episode_id,role,item["sha256"])).fetchone()
                if old:
                    return dict(versionId=old[0],sha256=item["sha256"])
                image = (Path(prod["assetRoot"])/item["path"]).read_bytes()
                if sha256_bytes(image) != item["sha256"]:
                    raise Rejected("登记时原图已变化，请重新检测")
                asset = self.register_asset(ep["project_id"], item.get("name") or role, image, "identity", episode_id=episode_id,
                                            roles=[role], origin="fixed_role", note=dumps(dict(item,registrySha256=catalog["registrySha256"])), db=db)
                db.execute("INSERT INTO fixed_role_asset VALUES(?,?,?,?)", (episode_id,role,item["sha256"],asset["versionId"]))
                return dict(versionId=asset["versionId"],sha256=item["sha256"])
            return self._command(db,command_id,"use_fixed_role",dict(episode=episode_id,role=role),run)
