"""Content-addressed blobs, import copies and frozen run input manifests."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "workbench.sqlite"

    # -- paths are always derived from IDs on the server side
    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("path escapes data directory")
        return resolved

    def blob_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid digest")
        return self.root / "blobs" / "sha256" / digest[:2] / digest

    def put_blob(self, data: bytes) -> str:
        digest = sha256_bytes(data)
        path = self.blob_path(digest)
        if not path.exists():
            write_atomic(path, data)
        return digest

    def read_blob(self, digest: str) -> bytes:
        data = self.blob_path(digest).read_bytes()
        if sha256_bytes(data) != digest:
            raise ValueError("blob fingerprint mismatch: " + digest)
        return data

    def import_copy(self, project_id: str, import_id: str, name: str, data: bytes) -> str:
        safe = Path(name).name
        if not safe or safe in (".", ".."):
            raise ValueError("invalid file name")
        rel = Path("projects") / project_id / "imports" / import_id / safe
        target = self._inside(self.root / rel)
        write_atomic(target, data)
        if sha256_bytes(target.read_bytes()) != sha256_bytes(data):
            raise ValueError("copy verification failed")
        return rel.as_posix()

    def run_dir(self, run_id: str) -> Path:
        return self._inside(self.root / "runs" / run_id)

    def freeze(self, run_id: str, header: dict, inputs: list[dict]) -> tuple[str, str]:
        """inputs: [{role, name, text}] -> blobs + manifest. Returns (fingerprint, relpath)."""
        entries = []
        for item in inputs:
            data = item["data"] if "data" in item else item["text"].encode("utf-8")
            entry = dict(role=item["role"], name=item["name"], sha256=self.put_blob(data), bytes=len(data))
            if "data" in item:
                entry["binary"] = True
            entries.append(entry)
        manifest = dict(header, runId=run_id, inputs=entries)
        body = canonical(manifest)
        fingerprint = sha256_bytes(body)
        rel = Path("runs") / run_id / "input-manifest.json"
        write_atomic(self.root / rel, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        return fingerprint, rel.as_posix()

    def load_manifest(self, relpath: str) -> tuple[dict, str]:
        manifest = json.loads((self._inside(self.root / relpath)).read_text())
        return manifest, sha256_bytes(canonical(manifest))

    def manifest_texts(self, manifest: dict) -> list[dict]:
        return [dict(e, text=None if e.get("binary") else self.read_blob(e["sha256"]).decode("utf-8"))
                for e in manifest["inputs"]]


def write_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def publish_dir_atomic(staging: Path, final: Path):
    """Delivery guard: write into a staging dir on the same filesystem, then rename."""
    if final.exists():
        raise FileExistsError(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.rename(staging, final)


def copy_tree_to_staging(src: Path, parent: Path) -> Path:
    staging = Path(tempfile.mkdtemp(dir=parent, prefix=".staging-"))
    shutil.copytree(src, staging, dirs_exist_ok=True)
    return staging
