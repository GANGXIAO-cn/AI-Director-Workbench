"""Immutable, project-scoped file inputs used to recheck an imported episode."""
import json
import os
from pathlib import Path

from .errors import Rejected
from . import imageinventory, referencereports, sceneprovenance
from .storage import sha256_bytes


def records(value):
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def evidence_paths(value):
    if isinstance(value, list):
        for item in value:
            yield from evidence_paths(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_path" and isinstance(item, str):
                yield item
            elif isinstance(item, (dict, list)):
                yield from evidence_paths(item)


def capture(root, originals, store, project_roles, project_root, production, *, include_image_inventory=False):
    files = {key:store.put_blob(value) for key,value in originals.items()}
    aliases = {}
    issues = {}
    fixed = {}
    root = root.resolve()
    project_root = project_root.resolve()

    def copy(name, *, expected=None):
        if not isinstance(name, str) or not name:
            return None
        cached = files.get(aliases.get(name,name))
        if cached is not None:
            return cached if expected is None or cached==expected else None
        if name in issues:
            return None
        try:
            if Path(name).is_absolute():
                raise ValueError("不是本集相对路径")
            resolved = (root/name).resolve()
            if root not in resolved.parents:
                raise ValueError("路径越出本集")
            if not resolved.is_file():
                raise ValueError("文件缺失")
            rel = resolved.relative_to(root).as_posix()
            if rel not in files:
                data = resolved.read_bytes()
                if expected is not None and sha256_bytes(data)!=expected:
                    return None
                files[rel] = store.put_blob(data)
            if expected is not None and files[rel]!=expected:
                return None
            if rel != name:
                aliases[name] = rel
            return files[rel]
        except (OSError, ValueError, RuntimeError) as exc:
            issues[name] = str(exc)
            return None

    for role in project_roles:
        file = (project_root / role["path"]).resolve()
        if project_root not in file.parents:
            raise Rejected("固定角色登记路径超出项目")
        digest = store.put_blob(file.read_bytes())
        if digest != role["sha256"]:
            raise Rejected("固定角色原图在导入时已变更")
        fixed[role["id"]] = dict(path=str(file), sha256=digest, version=role["version"])
    standards = {}
    for key in ("promptStandard", "characterStandard", "sceneBaselines", "assetLibraryIndex"):
        rel = production.get(key)
        if isinstance(rel,str) and not Path(rel).is_absolute():
            path = (project_root/rel).resolve()
            if project_root in path.parents and path.is_file():
                standards[key] = dict(path=rel,sha256=store.put_blob(path.read_bytes()))
    try:
        manifest = json.loads(originals["episode.json"])
    except (ValueError, UnicodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    for name in ("review.md","故事稿.md","剧本与创作说明.md","场面与制作方案.md","过程稿/图片选择.json","正式交付/00_使用说明.txt"):
        if (root/name).is_file():
            copy(name)
    story = manifest.get("story_source")
    if isinstance(story,dict):
        for key in ("path","snapshot","review"):
            copy(story.get(key))
    anchors = manifest.get("scene_anchors")
    if isinstance(anchors,dict):
        for anchor in anchors.values():
            if isinstance(anchor,dict):
                copy(anchor.get("path"))
    fixed_refs = {}
    for asset in records(manifest.get("assets")):
        name = asset.get("path")
        if not asset.get("fixed_role"):
            copy(name)
        elif isinstance(asset["fixed_role"],str) and isinstance(name,str) and Path(name).is_absolute():
            # A historical file can be an episode-local identity copy. Its bytes
            # can establish identity even when it fails the canonical registry path rule.
            try:
                resolved = Path(name).resolve()
            except (ValueError,OSError,RuntimeError) as exc:
                issues[name] = str(exc)
                continue
            registered = fixed.get(asset["fixed_role"],{})
            if str(resolved) == registered.get("path"):
                fixed_refs[name] = dict(path=str(resolved),sha256=registered["sha256"])
            elif root in resolved.parents:
                digest = copy(resolved.relative_to(root).as_posix())
                if digest:
                    fixed_refs[name] = dict(path=str(resolved),sha256=digest)
            else:
                issues[name] = "固定角色路径未登记，且位于本集目录外"
    for seg in records(manifest.get("segments")):
        copy(seg.get("prompt"))
        copy(seg.get("storyboard"))
    for key in ("asset_snapshot","video_prompt_standard","video_prompt_rules"):
        copy(manifest.get(key))
    for key in ("prompt_review","shot_rhythm"):
        block = manifest.get(key)
        if isinstance(block, dict):
            for field in ("plan","story","requirements"):
                copy(block.get(field))
    if isinstance(manifest.get("shot_rhythm"),dict) and "plan" not in manifest["shot_rhythm"]:
        copy("场面与制作方案.md")
    for name in evidence_paths(manifest):
        copy(name)
    ref = manifest.get("review_evidence")
    digest = copy(ref)
    if digest:
        try:
            index = json.loads(store.read_blob(digest))
        except (ValueError, UnicodeError):
            index = None
        for name in evidence_paths(index):
            copy(name)
    # Preserve only evidence named by an adopted scene image; never scan tool outputs.
    scene_refs = {}
    fixed_by_path = {item["path"]:item["sha256"] for item in fixed.values()}
    output_dir = production.get("imageOutputRoot")
    allowed_outputs = Path(output_dir).resolve() if output_dir and Path(output_dir).is_absolute() else None

    def scene_record(record, *, episode_only=False):
        if not isinstance(record, dict):
            return
        name = record.get("path")
        if not isinstance(name, str) or not name or name in files or name in scene_refs:
            return
        if not Path(name).is_absolute():
            copy(name)
            return
        try:
            path = Path(name).resolve()
            if root in path.parents:
                digest = copy(path.relative_to(root).as_posix())
            elif episode_only:
                return
            elif str(path) in fixed_by_path:
                digest = fixed_by_path[str(path)]
            elif allowed_outputs and allowed_outputs in path.parents and path.suffix.lower() in {".png",".jpg",".jpeg",".webp"}:
                raw = path.read_bytes()
                if sha256_bytes(raw) != record.get("sha256"):
                    raise ValueError("工具输出与记录指纹不符")
                digest = store.put_blob(raw)
            else:
                raise ValueError("图片路径未登记或超出允许范围")
            if digest:
                scene_refs[name] = digest
                issues.pop(name, None)
        except (OSError, ValueError, RuntimeError) as exc:
            issues[name] = str(exc)

    # Absolute episode-local paths in older manifests remain bound to this receipt.
    for asset in records(manifest.get("assets")):
        name = asset.get("path")
        if isinstance(name,str) and Path(name).is_absolute():
            scene_record(asset, episode_only=True)
    if isinstance(anchors, dict):
        for anchor in anchors.values():
            scene_record(anchor)
    if (root/sceneprovenance.REQUEST_LOG).is_file():
        log = copy(sceneprovenance.REQUEST_LOG)
        try:
            requests = json.loads(store.read_blob(log)) if log else {}
        except (ValueError, UnicodeError):
            requests = {}
        if isinstance(requests, dict):
            for asset in sceneprovenance.used_scene_images(manifest):
                scene_record(asset)
                request_id = asset.get("generation_request")
                req = requests.get(request_id) if isinstance(request_id, str) else None
                if isinstance(req, dict):
                    scene_record(req.get("output"))
                    for ref in records(req.get("input_versions")):
                        scene_record(ref)

    metadata = dict(version=2, contactRules=dict(roles=production.get("roleAssets",{}).get("roles",[]),
                       outfits=production.get("contactOutfits",{})), sceneRefs=scene_refs, sceneBaselineRule=production.get("sceneBaselineRule"), aliases=aliases, unavailable=issues, fixed=fixed, fixedRefs=fixed_refs, standards=standards,
                       silentRoles=production.get("silentRoles", []))
    metadata["pageOriginalRoot"] = str(root)
    metadata["pageExternalPaths"] = {name:os.path.relpath(name,root/"正式交付") for name in fixed_refs}
    if include_image_inventory:
        metadata["imageInventory"] = imageinventory.capture(root,manifest,copy,store.read_blob)
        metadata["referenceReports"] = referencereports.capture(root,project_root,store,files)
    return files, metadata


class ArchiveFiles:
    def __init__(self, files, metadata, store):
        self.files = files
        self.metadata = metadata
        self.store = store

    def local(self, name):
        rel = self.metadata["aliases"].get(name,name) if isinstance(name,str) else None
        if not rel or Path(rel).is_absolute() or rel not in self.files:
            raise ValueError(f"文件缺失或超出本集目录：{name}")
        return rel

    def read(self, name):
        return self.store.read_blob(self.files[self.local(name)])

    def scene_bytes(self, name):
        digest = self.metadata.get("sceneRefs", {}).get(name)
        if digest:
            return self.store.read_blob(digest)
        return self.read(name)

    def scene_registry(self, key):
        standard = self.metadata["standards"].get(key)
        if not standard:
            raise ValueError("导入时缺少项目登记资料：" + key)
        return json.loads(self.store.read_blob(standard["sha256"]))

    def text(self, name):
        return self.read(name).decode("utf-8")

    def asset(self, asset):
        role = asset.get("fixed_role")
        if role:
            item = self.metadata["fixed"].get(role)
            ref = self.metadata.get("fixedRefs",{}).get(asset.get("path"), {})
            if not item or ref.get("path") != item["path"] or asset.get("sha256") != item["sha256"]:
                raise ValueError("固定角色不匹配现行角色基准")
            if asset.get("type") != "image" or asset.get("category") != "character":
                raise ValueError("固定角色文件不可用")
            return item["path"], self.store.read_blob(item["sha256"])
        name = asset["path"]
        if Path(name).is_absolute():
            raise ValueError("非固定角色资产必须使用本集相对路径")
        return self.local(name), self.read(name)


    def identity_bytes(self, asset):
        if not asset.get("fixed_role"):
            return self.read(asset["path"])
        ref = self.metadata.get("fixedRefs",{}).get(asset.get("path"))
        if not ref:
            raise ValueError("固定角色身份文件未在允许范围内登记")
        return self.store.read_blob(ref["sha256"])
