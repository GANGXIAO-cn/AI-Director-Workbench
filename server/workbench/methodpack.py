"""Versioned, read-only method packs and project configs."""
from difflib import unified_diff
import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
METHODS = REPO / "methods"
CONFIGS = REPO / "configs" / "projects"
CONTRACT = REPO / "contracts" / "model-result.v1.schema.json"

PACK_FILES = {
    "story_standard": "story/story-standard.md",
    "story_draft": "story/story-draft.md",
    "story_review": "review/story-review.md",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pack_dir(ref: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]*@v[1-9][0-9]*", ref):
        raise ValueError("invalid method pack reference")
    name, version = ref.split("@")
    path = (METHODS / name / version).resolve()
    if METHODS.resolve() not in path.parents:
        raise ValueError("invalid method pack reference")
    return path


def build_manifest(ref: str) -> dict:
    root = pack_dir(ref)
    return dict(id=ref, files={k: dict(path=v, sha256=_digest(root / v)) for k, v in PACK_FILES.items()})


def write_manifest(ref: str):
    (pack_dir(ref) / "manifest.json").write_text(
        json.dumps(build_manifest(ref), ensure_ascii=False, indent=2) + "\n")


def load_pack(ref: str) -> dict:
    """Load and verify a pack against its committed manifest."""
    root = pack_dir(ref)
    manifest = json.loads((root / "manifest.json").read_text())
    if not isinstance(manifest,dict) or manifest.get("id") != ref:
        raise ValueError("method pack reference does not match manifest")
    if not isinstance(manifest.get("files"),dict) or not isinstance(manifest.get("projectFiles",{}),dict) or not isinstance(stage_map(manifest),dict):
        raise ValueError("方法包文件或阶段目录格式无效")
    project_texts = {}
    names = set(manifest["files"])
    def read_entries(catalog):
        result = {}
        for key, entry in catalog.items():
            if not isinstance(entry,dict) or not isinstance(entry.get("path"),str) or not isinstance(entry.get("sha256"),str):
                raise ValueError(f"方法文件缺少路径或指纹：{key}")
            path = (root / entry["path"]).resolve()
            if root not in path.parents:
                raise ValueError("method pack file outside pack directory")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"method pack file changed without new version: {entry['path']}")
            result[key] = data.decode("utf-8")
        return result
    texts = read_entries(manifest["files"])
    for profile, annex in manifest.get("projectFiles", {}).items():
        if not isinstance(annex,dict):
            raise ValueError(f"项目小节目录格式无效：{profile}")
        if manifest["files"].keys() & annex.keys():
            raise ValueError("通用小节与项目专属小节重名")
        names.update(annex)
        project_texts[profile] = read_entries(annex)
    for stage, keys in stage_map(manifest).items():
        if not isinstance(keys,list) or any(not isinstance(key,str) for key in keys) or len(set(keys))!=len(keys) or set(keys)-names:
            raise ValueError(f"阶段装配包含缺失或重复的小节：{stage}")
    return dict(manifest, texts=texts, projectTexts=project_texts)


def section_text(pack, key, profile):
    return pack["texts"][key] if key in pack["files"] else pack["projectTexts"][profile][key]


def stage_map(pack):
    return pack.get("stages", dict(story=["story_standard", "story_draft"], story_review=["story_review", "story_standard"]))


def method_entries(pack, stage, profile=None):
    sections = stage_map(pack).get(stage, [])
    annex = pack.get("projectFiles", {}).get(profile, {})
    items = []
    for key in sections:
        entry = pack["files"].get(key) or annex.get(key)
        if entry:
            items.append(dict(role="method" if key in pack["files"] else "project_method",
                              name=Path(entry["path"]).name, text=section_text(pack,key,profile), methodSource=entry))
    return items


def pack_changes(original, candidate, profile=None):
    def catalog(pack):
        return {**pack["files"], **pack.get("projectFiles", {}).get(profile, {})}

    before, after = catalog(original), catalog(candidate)
    old_map, new_map = stage_map(original), stage_map(candidate)
    stages = {}
    for stage in sorted(old_map.keys() | new_map.keys()):
        old = [k for k in old_map.get(stage, []) if k in before]
        new = [k for k in new_map.get(stage, []) if k in after]
        if old != new or any(before[k]["sha256"] != after[k]["sha256"] for k in set(old) & set(new)):
            stages[stage] = dict(before=old, after=new)

    sections = []
    for key in sorted(before.keys() | after.keys()):
        a, b = before.get(key), after.get(key)
        source_a, source_b = (e.get("source", {}) if e else {} for e in (a,b))
        old_stages = [s for s, keys in old_map.items() if key in keys]
        new_stages = [s for s, keys in new_map.items() if key in keys]
        if not a:
            kind = "added"
        elif not b:
            kind = "removed"
        elif a["sha256"] != b["sha256"]:
            kind = "edited"
        elif set(old_stages) != set(new_stages):
            kind = "stages"
        elif (a["path"], source_a.get("path"), source_a.get("lines")) != (b["path"], source_b.get("path"), source_b.get("lines")):
            kind = "location"
        else:
            continue
        old_text = section_text(original,key,profile) if a else ""
        new_text = section_text(candidate,key,profile) if b else ""
        patch = "\n".join(unified_diff(old_text.splitlines(), new_text.splitlines(),
                                       fromfile=original["id"], tofile=candidate["id"], lineterm=""))
        sections.append(dict(key=key, kind=kind, scope="shared" if key in original["files"] or key in candidate["files"] else profile,
                             before=a, after=b, oldStages=old_stages, newStages=new_stages, patch=patch))
    return dict(original=original["id"], candidate=candidate["id"], stages=stages, sections=sections,
                beforeSources=original.get("source",{}), afterSources=candidate.get("source",{}))


def available_packs():
    refs = []
    for manifest in METHODS.glob("*/*/manifest.json"):
        family, version = manifest.relative_to(METHODS).parts[:2]
        ref = family+"@"+version
        if re.fullmatch(r"[a-z][a-z0-9-]*@v[1-9][0-9]*",ref):
            refs.append(ref)
    return sorted(refs,key=lambda ref:(ref.split("@")[0], int(ref.split("@v")[1])))


def load_config(config_id: str) -> dict:
    if "/" in config_id or config_id.startswith(".."):
        raise ValueError("invalid config id")
    return json.loads((CONFIGS / f"{config_id}.json").read_text())


def list_configs() -> list[dict]:
    return [dict(configId=p.stem, name=json.loads(p.read_text()).get("name") or "（空白新项目）")
            for p in sorted(CONFIGS.glob("*.json"))]


if __name__ == "__main__":
    write_manifest("short-film@v1")
    print(json.dumps(build_manifest("short-film@v1"), ensure_ascii=False, indent=2))
