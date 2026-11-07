"""Phase 0 only. A single synthetic episode and artifact in a disposable SQLite database."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from executors import CHECK


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def connect(root):
    db = sqlite3.connect(root / "workbench.sqlite", timeout=5)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(root):
    (root / "blobs").mkdir()
    with connect(root) as db:
        mode, = db.execute("PRAGMA journal_mode=WAL").fetchone()
        if mode != "wal":
            raise RuntimeError("SQLite WAL unavailable")
        db.executescript("""
          CREATE TABLE snapshot(hash TEXT PRIMARY KEY, manifest BLOB NOT NULL);
          CREATE TABLE run(id TEXT PRIMARY KEY, input TEXT NOT NULL REFERENCES snapshot(hash));
          CREATE TABLE draft(id TEXT PRIMARY KEY REFERENCES run(id), contents BLOB NOT NULL);
          CREATE TABLE head(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                            draft_id TEXT REFERENCES draft(id));
          INSERT INTO head VALUES (1, 0, NULL);
          CREATE TABLE receipt(command_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, revision INTEGER NOT NULL);
        """)


def freeze(root, run_id, source: Path):
    data = source.read_bytes()
    blob = digest(data)
    manifest = {"project": "probe", "episode": "N01", "base": "R03",
                "inputs": [{"sha256": blob}], "scope": [["scene02", "action"]]}
    raw = encode(manifest)
    fingerprint = digest(raw)
    path = root / "blobs" / blob
    # Publish complete bytes before publishing a manifest; concurrent freezes use the same content.
    with tempfile.NamedTemporaryFile(dir=path.parent) as temp:
        temp.write(data)
        temp.flush()
        os.fsync(temp.fileno())
        try:
            os.link(temp.name, path)
        except FileExistsError:
            pass
    if digest(path.read_bytes()) != blob:
        raise ValueError("corrupt blob")
    with connect(root) as db:
        db.execute("INSERT OR IGNORE INTO snapshot VALUES (?,?)", (fingerprint, raw))
        db.execute("INSERT INTO run VALUES (?,?)", (run_id, fingerprint))
    return fingerprint


def receive(root, run_id, result):
    CHECK.validate(result)
    with connect(root) as db:
        db.execute("BEGIN IMMEDIATE")
        fingerprint, raw = db.execute(
            "SELECT input,manifest FROM run JOIN snapshot ON hash=input WHERE id=?", (run_id,)
        ).fetchone()
        manifest = json.loads(raw)
        if (digest(raw) != fingerprint or result["runId"] != run_id
                or result["inputFingerprint"] != fingerprint or result["kind"] != "draft"
                or result["baseVersionId"] != manifest["base"]):
            raise ValueError("receipt context mismatch")
        for item in manifest["inputs"]:
            blob = item["sha256"]
            if digest((root / "blobs" / blob).read_bytes()) != blob:
                raise ValueError("corrupt input")
        if any([c["targetId"], c["field"]] not in manifest["scope"] for c in result["changes"]):
            raise ValueError("out of scope")
        value = encode(result)
        old = db.execute("SELECT contents FROM draft WHERE id=?", (run_id,)).fetchone()
        if old and old[0] != value:
            raise ValueError("conflicting receipt")
        if not old:
            db.execute("INSERT INTO draft VALUES (?,?)", (run_id, value))


def adopt(root, command_id, draft, expected):
    payload = digest(encode(["adopt", draft, expected]))
    with connect(root) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT payload_hash,revision FROM receipt WHERE command_id=?",
                         (command_id,)).fetchone()
        if old:
            if old[0] != payload:
                raise ValueError("command id reused")
            return old[1]
        updated = db.execute("UPDATE head SET draft_id=?, revision=revision+1 WHERE id=1 AND revision=?",
                             (draft, expected))
        if updated.rowcount != 1:
            raise ValueError("stale revision")
        db.execute("INSERT INTO receipt VALUES (?,?,?)", (command_id, payload, expected + 1))
        return expected + 1
