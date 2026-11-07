"""Versioned, read-only method packs and project configs."""
import hashlib
import json
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
    texts = {}
    for key, entry in manifest["files"].items():
        path = root / entry["path"]
        if _digest(path) != entry["sha256"]:
            raise ValueError(f"method pack file changed without new version: {entry['path']}")
        texts[key] = path.read_text()
    return dict(id=manifest["id"], files=manifest["files"], texts=texts)


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
