import concurrent.futures
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from jsonschema import ValidationError
from executors import CHECK, SAMPLE, SCHEMA, decode
from storage import adopt, connect, digest, freeze, initialize, receive


class WireTests(unittest.TestCase):
    def test_four_schema_branches(self):
        CHECK.check_schema(SCHEMA)
        loc = dict(objectId="scene02", versionId="R03", unit="scene", anchor="02", evidence="推门")
        common = {k: SAMPLE[k] for k in ("schemaVersion", "runId", "inputFingerprint")}
        results = [SAMPLE, dict(common, kind="review", reviewedVersionId="R03", **{"pass": "story"},
                                decision="pass", observations=[loc], issues=[]),
                   dict(common, kind="proposal", items=[dict(category="rule", content="门向外开",
                        source=loc, suggestedStrength="variable", rationale="作为空间参考")]),
                   dict(common, kind="image_assessment", assetVersionId="I01", intendedShotIds=["S03"],
                        visibleValue="门轴可见", useDefects=[], evidence=[loc], recommendation="candidate")]
        for item in results:
            with self.subTest(kind=item["kind"]):
                CHECK.validate(item)
                for invalid in (dict(item, schemaVersion=2), dict(item, adopt=True),
                                {k: v for k, v in item.items() if k != "runId"}):
                    with self.assertRaises(ValidationError):
                        CHECK.validate(invalid)

    def test_executor_receipts(self):
        start = {"type": "thread.started", "thread_id": "synthetic"}
        msg = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(SAMPLE)}}
        done = {"type": "turn.completed"}
        def lines(events):
            return "\n".join(map(json.dumps, events))
        self.assertEqual(decode("codex", lines([start, msg, done])), SAMPLE)
        for events in ([start, msg], [start, msg, {"type": "turn.failed"}, done], [msg, done]):
            with self.assertRaises(ValueError):
                decode("codex", lines(events))
        envelope = dict(subtype="success", is_error=False, structured_output=SAMPLE)
        self.assertEqual(decode("claude", json.dumps(envelope)), SAMPLE)
        with self.assertRaises(ValueError):
            decode("claude", json.dumps(dict(envelope, is_error=True)))
        agy = dict(status="SUCCESS", structured_output=SAMPLE)
        self.assertEqual(decode("antigravity", json.dumps(agy)), SAMPLE)
        with self.assertRaises(ValueError):
            decode("antigravity", json.dumps(dict(agy, status="ERROR")))
        with self.assertRaises(KeyError):
            decode("antigravity", json.dumps({"status": "SUCCESS", "response": "ok"}))


class StorageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="r7-store-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        initialize(self.root)
        self.source = self.root / "source.txt"
        self.source.write_text("合成场次。")
        self.fp = freeze(self.root, "run1", self.source)
        self.result = dict(SAMPLE, runId="run1", inputFingerprint=self.fp)

    def rows(self, query):
        with connect(self.root) as db:
            return db.execute(query).fetchall()

    def test_source_edits_do_not_change_frozen_input(self):
        self.source.write_text("手工改稿。")
        self.assertNotEqual(self.fp, freeze(self.root, "run2", self.source))
        raw, = self.rows("SELECT manifest FROM snapshot JOIN run ON run.input=hash WHERE id='run1'")[0]
        self.assertEqual(digest(raw), self.fp)
        blob = json.loads(raw)["inputs"][0]["sha256"]
        self.assertEqual((self.root / "blobs" / blob).read_text(), "合成场次。")
        receive(self.root, "run1", self.result)
        self.assertEqual(self.rows("SELECT revision,draft_id FROM head"), [(0, None)])

    def test_scripted_worker_to_candidate_to_adoption(self):
        # A separate Python process supplies Codex-shaped events, with no provider access.
        worker = """import json,sys
result=json.load(sys.stdin)
events=[
 {"type":"thread.started","thread_id":"fake-1"},
 {"type":"item.completed","item":{"type":"agent_message","text":json.dumps(result)}},
 {"type":"turn.completed"}
]
for event in events:
 print(json.dumps(event))
"""
        output = subprocess.run([sys.executable, "-c", worker], input=json.dumps(self.result),
                                capture_output=True, text=True, timeout=5, check=True)
        receive(self.root, "run1", decode("codex", output.stdout))
        self.assertEqual(self.rows("SELECT draft_id FROM head"), [(None,)])
        adopt(self.root, "accept-draft", "run1", 0)
        contents, = self.rows("SELECT contents FROM draft JOIN head ON head.draft_id=draft.id")[0]
        self.assertEqual(json.loads(contents)["changes"], self.result["changes"])

    def test_reject_modified_input_bytes(self):
        blob = next((self.root / "blobs").iterdir())
        blob.write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            receive(self.root, "run1", self.result)
        self.assertEqual(self.rows("SELECT id FROM draft"), [])

    def test_reject_cross_run_wrong_base_or_scope(self):
        for patch in ({"runId": "run2"}, {"inputFingerprint": "f" * 64}, {"baseVersionId": "R02"},
                      {"changes": [dict(targetId="scene01", field="action", value="越界")]},
                      {"changes": [dict(targetId="scene02", field="ending", value="越界")]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                receive(self.root, "run1", dict(self.result, **patch))
        self.assertEqual(self.rows("SELECT id FROM draft"), [])

    def test_duplicate_receipt(self):
        for _ in range(2):
            receive(self.root, "run1", self.result)
        fork = copy.deepcopy(self.result)
        fork["changes"][0]["value"] = "另一个结果。"
        with self.assertRaisesRegex(ValueError, "conflicting receipt"):
            receive(self.root, "run1", fork)
        self.assertEqual(self.rows("SELECT id FROM draft"), [("run1",)])

    def race(self, commands):
        barrier = threading.Barrier(2)
        def submit(command):
            barrier.wait(timeout=5)
            try:
                return adopt(self.root, *command)
            except ValueError as exc:
                return str(exc)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(submit, commands))

    def test_two_clients_same_command(self):
        receive(self.root, "run1", self.result)
        self.assertEqual(self.race([("c1", "run1", 0)] * 2), [1, 1])
        self.assertEqual(self.rows("SELECT revision FROM head"), [(1,)])
        self.assertEqual(self.rows("SELECT count(*) FROM receipt"), [(1,)])
        with self.assertRaisesRegex(ValueError, "command id reused"):
            adopt(self.root, "c1", "run1", 1)

    def test_two_clients_conflicting_adoptions(self):
        freeze(self.root, "run2", self.source)
        for run in ("run1", "run2"):
            receive(self.root, run, dict(self.result, runId=run))
        results = self.race([("c1", "run1", 0), ("c2", "run2", 0)])
        self.assertCountEqual(results, [1, "stale revision"])
        self.assertEqual(self.rows("SELECT draft_id FROM head"), [(f"run{results.index(1) + 1}",)])
        self.assertEqual(self.rows("SELECT count(*) FROM draft"), [(2,)])

    def test_crash_and_reopen(self):
        receive(self.root, "run1", self.result)
        # Exit without closing the transaction. SQLite must undo the half-written adoption.
        crash = """import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute("BEGIN IMMEDIATE")
db.execute("UPDATE head SET revision=1,draft_id='run1'")
os._exit(73)
"""
        child = subprocess.run([sys.executable, "-c", crash, str(self.root / "workbench.sqlite")],
                               timeout=10)
        self.assertEqual(child.returncode, 73)
        self.assertEqual(self.rows("SELECT revision,draft_id FROM head"), [(0, None)])
        self.assertEqual(self.rows("SELECT command_id FROM receipt"), [])
        self.assertEqual(adopt(self.root, "c1", "run1", 0), 1)
        self.assertEqual(adopt(self.root, "c1", "run1", 0), 1)
        self.assertEqual(self.rows("PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self.rows("PRAGMA foreign_key_check"), [])
        with self.assertRaises(sqlite3.IntegrityError):
            adopt(self.root, "c2", "missing-draft", 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
