"""Assemble scene comparison inputs from the adopted native shot plan and prompt references."""
import json
from . import contactsheet, sceneprovenance
from .archivefiles import ArchiveFiles
from .db import connect, dumps, transaction
from .errors import Conflict, Rejected
from .storage import canonical, sha256_bytes


def read_inputs(wb, db, eid):
    ep = wb._episode_in(db,eid)
    plan = wb._adopted_content(db,eid,"plan") or {}
    prompts = wb._adopted_content(db,eid,"prompts")
    matrix = wb._cast_matrix(db,eid)
    rows = {r["segment"]:r["cells"] for r in matrix["rows"]}
    assets, segments, scene_ids = {}, [], []
    def image(vid):
        if vid not in assets:
            v = wb.asset_version(vid,db)
            if v["project_id"] != ep["project_id"] or v["episode_id"] not in (None,eid) or v["kind"] != "image":
                raise Rejected("参考图不属于本集或本项目")
            assets[vid] = dict(id=vid,type="image",category="grid" if v["grid"] else v["category"],path=vid,
                               name=f'{v["name"]} v{v["version_no"]}',sha256=v["sha256"],scene_ids=[])
        return assets[vid]

    cast = {}
    for role, r in matrix["roster"].items():
        cast[role] = dict(name=role,identity_required=r.get("identityRequired",True))
        if r.get("assetVersionId"):
            cast[role]["identity_asset_id"] = image(r["assetVersionId"])["id"]
    for item in plan.get("segments",[]):
        sid = item["id"]
        shots = [s for s in plan.get("shots",[]) if s["segment"]==sid]
        origins = {s["id"]:s.get("origin",{}).get("scene") for s in shots}
        for scene in origins.values():
            if scene and scene not in scene_ids:
                scene_ids.append(scene)
        refs = {}
        if prompts is not None:
            for r in prompts.get("segments",{}).get(sid,{}).get("refs",[]):
                if r.get("kind")=="image":
                    refs.setdefault(r["assetVersionId"],[]).extend(r.get("shots") or [])
        else:
            for s in shots:
                for key in ("startAssetId","endAssetId"):
                    if s.get(key):
                        refs.setdefault(s[key],[]).append(s["id"])
        for vid, shot_ids in refs.items():
            a = image(vid)
            for shot in shot_ids:
                scene = origins.get(shot)
                if scene and scene not in a["scene_ids"]:
                    a["scene_ids"].append(scene)
        segment = dict(id=sid,references=[dict(asset_id=v) for v in refs])
        if shots and all(isinstance(s.get("visibleRoles"),list) for s in shots):
            segment["on_screen_roles"] = sorted({r for s in shots for r in s["visibleRoles"]})
        elif cast and all(rows.get(sid,{}).get(r,{}).get("status") in ("ok","absent") for r in cast):
            segment["on_screen_roles"] = [r for r in cast if rows[sid][r]["status"]=="ok"]
        segments.append(segment)
    data = dict(schema_version=2,assets=list(assets.values()),segments=segments,scene_anchors={},cast=cast)
    locks = wb._space_locks(db,eid)
    lookup = {s["id"]:s for s in locks if s["status"]=="confirmed" and s["sha256"]}
    used = sceneprovenance.used_scene_images(data)
    referenced = {r["asset_id"] for s in segments for r in s["references"]}
    outfits = [a for a in assets.values() if a["category"]=="costume" and a["id"] in referenced]
    return data,dict(scenes=scene_ids,images=used,outfits=outfits,roles=list(cast),locks=lookup,
                     source="采用提示词的有序参考图" if prompts is not None else "采用方案的起幅与落幅图")


def overrides(db,eid):
    row = db.execute("SELECT value FROM app_setting WHERE key=?",("contact:"+eid,)).fetchone()
    return json.loads(row[0]) if row else dict(images={},locks={},outfits={})


def active_options(options,catalog):
    scenes = catalog["scenes"]
    ids = {a["id"] for a in catalog["images"]}
    outfits = {a["id"] for a in catalog["outfits"]}
    return dict(images={i:[s for s in values if s in scenes] for i,values in options["images"].items() if i in ids},
                locks={s:v for s,v in options["locks"].items() if s in scenes and isinstance(v,str) and v in catalog["locks"]},
                outfits={i:r for i,r in options["outfits"].items() if i in outfits and r in catalog["roles"]})


def receipt(data,catalog,options):
    return sha256_bytes(canonical([data,catalog,options]))


class EpisodeContactMixin:
    def episode_contact_options(self, episode_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            data, catalog = read_inputs(self,db,episode_id)
            saved = overrides(db,episode_id)
        return dict(**catalog,options=active_options(saved,catalog),fingerprint=receipt(data,catalog,saved))

    def save_contact_options(self, episode_id, options, expected):
        with transaction(self.db_path) as db:
            data,catalog = read_inputs(self,db,episode_id)
            saved = overrides(db,episode_id)
            if receipt(data,catalog,saved) != expected:
                raise Conflict("采用稿、锁定图或对照设置已变化，请刷新后核对")
            if not isinstance(options,dict) or set(options)!={"images","locks","outfits"} or not all(
                    isinstance(v,dict) for v in options.values()):
                raise Rejected("场景对照设置格式无效")
            if not all(isinstance(s,list) and all(isinstance(v,str) for v in s) and len(s)==len(set(s))
                       for s in options["images"].values()) or active_options(options,catalog)!=options:
                raise Rejected("请选择当前采用的素材、场景、锁定图与本集角色")
            db.execute("INSERT INTO app_setting VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       ("contact:"+episode_id,dumps(options)))
        return self.episode_contact_options(episode_id)

    def episode_contact_sheet(self, episode_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            data,catalog=read_inputs(self,db,episode_id)
            saved = overrides(db,episode_id)
        options=active_options(saved,catalog)
        for a in catalog["images"]:
            if a["id"] in options["images"]:
                a["scene_ids"]=options["images"][a["id"]]
        hashes={a["path"]:a["sha256"] for a in data["assets"]}
        for scene, ident in options["locks"].items():
            lock=catalog["locks"][ident]
            data["scene_anchors"][scene]=dict(path=lock["versionId"],sha256=lock["sha256"])
            hashes[lock["versionId"]]=lock["sha256"]
        files=ArchiveFiles(hashes,dict(fixed={},aliases={}),self.store)
        try:
            return contactsheet.build(data,files,dict(roles=catalog["roles"],outfits={},outfitVersions=options["outfits"]))
        except (KeyError,TypeError,ValueError,OSError) as exc:
            raise Rejected(str(exc)) from exc

    def export_episode_contact_sheet(self, episode_id, directory, expected):
        report=self.episode_contact_sheet(episode_id)
        if report["fingerprint"]!=expected:
            raise Conflict("对照内容已变化，请重新打开后导出")
        try:
            return contactsheet.export(report,self.store,directory)
        except (ValueError,OSError) as exc:
            raise Rejected(str(exc)) from exc
