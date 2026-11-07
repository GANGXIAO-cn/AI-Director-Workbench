"""SQLite schema and connection helpers (WAL, foreign keys, short transactions)."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS command(
  command_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_hash TEXT NOT NULL,
  result TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
  config_id TEXT NOT NULL, method_pack TEXT NOT NULL, settings_revision INTEGER NOT NULL DEFAULT 0,
  spec_preset TEXT NOT NULL, production TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS setting_entry(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
  setting_revision INTEGER NOT NULL, key TEXT NOT NULL, category TEXT NOT NULL,
  strength TEXT NOT NULL CHECK(strength IN ('required','variable')), content TEXT NOT NULL,
  source_ref TEXT NOT NULL DEFAULT '', user_reason TEXT NOT NULL DEFAULT '',
  retired_revision INTEGER, UNIQUE(project_id, key, setting_revision));
CREATE TABLE IF NOT EXISTS episode(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
  display_code TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  leftover_facts TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'new',
  revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
  UNIQUE(project_id, display_code));
CREATE TABLE IF NOT EXISTS import_record(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
  episode_id TEXT REFERENCES episode(id), original_name TEXT NOT NULL,
  provided_source TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL
    CHECK(purpose IN ('story_source','script_source','storyboard_ref','art_ref')),
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, relpath TEXT NOT NULL,
  parse_status TEXT NOT NULL, parsed_text TEXT, diagnostic TEXT, copied_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kickoff(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL UNIQUE REFERENCES episode(id),
  source_type TEXT NOT NULL CHECK(source_type IN ('import','topic','idea')),
  source_import_id TEXT REFERENCES import_record(id), topic_id TEXT, idea_text TEXT,
  goal TEXT NOT NULL DEFAULT '', relation TEXT NOT NULL CHECK(relation IN ('independent','continue')),
  predecessor_ids TEXT NOT NULL DEFAULT '[]', carried_facts TEXT NOT NULL DEFAULT '[]',
  settings_revision INTEGER NOT NULL, settings_snapshot TEXT NOT NULL, spec TEXT NOT NULL,
  executor_plan TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS artifact(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
  episode_id TEXT NOT NULL REFERENCES episode(id), stage TEXT NOT NULL CHECK(stage IN ('story','script','plan','prompts','publish')),
  adopted_version_id TEXT REFERENCES version(id), revision INTEGER NOT NULL DEFAULT 0,
  UNIQUE(episode_id, stage));
CREATE TABLE IF NOT EXISTS version(
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifact(id), display_no INTEGER NOT NULL,
  base_id TEXT REFERENCES version(id), origin TEXT NOT NULL CHECK(origin IN ('ai','manual','combined')),
  source_run_id TEXT UNIQUE REFERENCES run(id), content TEXT NOT NULL, blob_hash TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, UNIQUE(artifact_id, display_no));
CREATE TABLE IF NOT EXISTS task(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
  episode_id TEXT NOT NULL REFERENCES episode(id), artifact_id TEXT NOT NULL REFERENCES artifact(id),
  stage TEXT NOT NULL, goal TEXT NOT NULL, used_rounds INTEGER NOT NULL DEFAULT 0,
  max_rounds INTEGER NOT NULL DEFAULT 2, auto_revise INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'active', executor_plan TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS thread(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES task(id), scope TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS message(
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES thread(id), seq INTEGER NOT NULL,
  role TEXT NOT NULL, text TEXT NOT NULL, selection TEXT, command_id TEXT UNIQUE,
  run_id TEXT, created_at REAL NOT NULL, kind TEXT NOT NULL DEFAULT 'text', payload TEXT,
  UNIQUE(thread_id, seq));
CREATE TABLE IF NOT EXISTS run(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task(id),
  purpose TEXT NOT NULL CHECK(purpose IN ('create','revise','review','cold_read','comparison','proposal','parallel')),
  counted_round INTEGER NOT NULL DEFAULT 0, provider TEXT NOT NULL,
  requested_model TEXT, requested_effort TEXT, reported_model TEXT, reported_effort TEXT,
  session_id TEXT, session_mode TEXT NOT NULL, isolation_level TEXT,
  base_version_id TEXT REFERENCES version(id), target_version_id TEXT REFERENCES version(id),
  input_hash TEXT NOT NULL, manifest_relpath TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', reason TEXT, diagnostic TEXT, format_attempts INTEGER NOT NULL DEFAULT 0,
  quota_resets_at TEXT, executable TEXT, executor_version TEXT,
  created_at REAL NOT NULL, started_at REAL, ended_at REAL, result_hash TEXT);
CREATE TABLE IF NOT EXISTS event(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES run(id), seq INTEGER NOT NULL,
  payload TEXT NOT NULL, UNIQUE(run_id, seq));
CREATE TABLE IF NOT EXISTS review(
  id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES version(id),
  run_id TEXT NOT NULL UNIQUE REFERENCES run(id), input_hash TEXT NOT NULL, pass_no INTEGER NOT NULL,
  pass TEXT NOT NULL, original_decision TEXT NOT NULL, isolation_level TEXT NOT NULL,
  reviewer TEXT NOT NULL, observations TEXT NOT NULL, created_at REAL NOT NULL,
  scope TEXT NOT NULL DEFAULT '', scope_hash TEXT,
  UNIQUE(version_id, pass, scope, pass_no));
CREATE TABLE IF NOT EXISTS issue(
  id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES review(id), ordinal INTEGER NOT NULL,
  loc TEXT NOT NULL, category TEXT NOT NULL, setting_id TEXT, setting_content TEXT,
  decision TEXT NOT NULL, reason TEXT NOT NULL, blocking INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS waiver(
  id TEXT PRIMARY KEY, issue_id TEXT NOT NULL REFERENCES issue(id), version_id TEXT NOT NULL REFERENCES version(id),
  reason TEXT NOT NULL CHECK(length(trim(reason)) > 0), created_at REAL NOT NULL,
  scope_hash TEXT, UNIQUE(issue_id, version_id));
CREATE TABLE IF NOT EXISTS adoption(
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifact(id),
  version_id TEXT NOT NULL REFERENCES version(id), previous_version_id TEXT,
  review_id TEXT REFERENCES review(id), command_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS preset(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), name TEXT NOT NULL,
  rev INTEGER NOT NULL, "values" TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(project_id, name, rev));
CREATE TABLE IF NOT EXISTS spec_change(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episode(id), key TEXT NOT NULL,
  old_value TEXT, new_value TEXT NOT NULL, impacts TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS episode_settings(
  episode_id TEXT PRIMARY KEY REFERENCES episode(id), settings_revision INTEGER NOT NULL,
  snapshot TEXT NOT NULL, decided_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS impact(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, episode_id TEXT, kind TEXT NOT NULL,
  source_ref TEXT NOT NULL, target_ref TEXT NOT NULL, reason TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ref_source(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), name TEXT NOT NULL,
  sha256 TEXT NOT NULL, relpath TEXT NOT NULL, duration_ms INTEGER, has_audio INTEGER,
  probe TEXT NOT NULL, state TEXT NOT NULL, replaces_id TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ref_pack(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, episode_id TEXT REFERENCES episode(id),
  source_id TEXT NOT NULL REFERENCES ref_source(id), lineage TEXT NOT NULL, version INTEGER NOT NULL,
  range_start_ms INTEGER NOT NULL, range_end_ms INTEGER NOT NULL, request TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(lineage, version));
CREATE TABLE IF NOT EXISTS ref_shot(
  id TEXT PRIMARY KEY, pack_id TEXT NOT NULL REFERENCES ref_pack(id), code TEXT NOT NULL,
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, first_frame TEXT, last_frame TEXT,
  camera TEXT, motion TEXT, sound TEXT, narrative TEXT, unknown_fields TEXT NOT NULL DEFAULT '[]',
  confirmed INTEGER NOT NULL DEFAULT 0, UNIQUE(pack_id, code));
CREATE TABLE IF NOT EXISTS beat(
  id TEXT PRIMARY KEY, pack_id TEXT NOT NULL REFERENCES ref_pack(id), code TEXT NOT NULL,
  shot_codes TEXT NOT NULL, event TEXT NOT NULL, info_gain TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
  UNIQUE(pack_id, code));
CREATE TABLE IF NOT EXISTS shot_bookmark(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episode(id), pack_id TEXT NOT NULL REFERENCES ref_pack(id),
  ref_shot_id TEXT NOT NULL REFERENCES ref_shot(id), reason TEXT NOT NULL, conditions TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS asset(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), episode_id TEXT REFERENCES episode(id),
  name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('image','video','audio')), category TEXT NOT NULL,
  origin TEXT NOT NULL, roles TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS asset_version(
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES asset(id), version_no INTEGER NOT NULL,
  sha256 TEXT NOT NULL, relpath TEXT NOT NULL, bytes INTEGER NOT NULL, width INTEGER, height INTEGER,
  source_version_id TEXT, grid INTEGER NOT NULL DEFAULT 0, cells TEXT, changed_cells TEXT,
  library_entry_id TEXT, note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, UNIQUE(asset_id, version_no));
CREATE TABLE IF NOT EXISTS library_entry(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), asset_version_id TEXT NOT NULL,
  usage TEXT NOT NULL, aspect TEXT, limits TEXT NOT NULL DEFAULT '', observation TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK(status IN ('pending','approved','limited','retired','rejected')),
  source_episode_id TEXT, created_at REAL NOT NULL, UNIQUE(asset_version_id));
CREATE TABLE IF NOT EXISTS image_choice(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, asset_version_id TEXT NOT NULL,
  mark TEXT NOT NULL CHECK(mark IN ('required','recommended','backup','adopted')),
  shot_ids TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL, UNIQUE(episode_id, asset_version_id, mark));
CREATE TABLE IF NOT EXISTS image_batch(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episode(id), input_hash TEXT NOT NULL,
  provider TEXT NOT NULL, model TEXT, operation TEXT NOT NULL, size TEXT NOT NULL, count INTEGER NOT NULL,
  spec TEXT NOT NULL, budget_cents INTEGER, estimate_cents INTEGER, cost_state TEXT NOT NULL,
  summary_hash TEXT NOT NULL, authorization TEXT, state TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(episode_id, input_hash, provider));
CREATE TABLE IF NOT EXISTS image_request(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES image_batch(id), ordinal INTEGER NOT NULL,
  local_request_id TEXT NOT NULL UNIQUE, provider_request_id TEXT, state TEXT NOT NULL,
  asset_version_id TEXT, sha256 TEXT, error TEXT, replaces_id TEXT, submitted_at REAL, ended_at REAL,
  UNIQUE(batch_id, ordinal));
CREATE TABLE IF NOT EXISTS space_item(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episode(id), label TEXT NOT NULL,
  relation TEXT NOT NULL, count INTEGER, source TEXT NOT NULL, lock_asset_version_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('confirmed','pending')), depends TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cast_cell(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episode(id), segment_id TEXT NOT NULL,
  role TEXT NOT NULL, asset_version_id TEXT, cells TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK(status IN ('ok','unclear','absent')), note TEXT NOT NULL DEFAULT '',
  UNIQUE(episode_id, segment_id, role));
CREATE TABLE IF NOT EXISTS delivery(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episode(id), scope TEXT NOT NULL,
  segments TEXT NOT NULL, manifest_hash TEXT NOT NULL, relpath TEXT NOT NULL, state TEXT NOT NULL,
  versions TEXT NOT NULL, command_id TEXT UNIQUE, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS package(
  id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES delivery(id), segments TEXT NOT NULL,
  relpath TEXT NOT NULL, sha256 TEXT NOT NULL, project_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ledger(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, episode_id TEXT NOT NULL REFERENCES episode(id),
  delivery_id TEXT, kind TEXT NOT NULL, content TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '',
  source_ref TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS closure(
  id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episode(id), delivery_id TEXT NOT NULL UNIQUE,
  run_id TEXT, state TEXT NOT NULL, result TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS proposal_item(
  id TEXT PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES closure(id), ordinal INTEGER NOT NULL,
  category TEXT NOT NULL, content TEXT NOT NULL, source_ref TEXT NOT NULL, suggested_strength TEXT,
  rationale TEXT NOT NULL, decision TEXT, strength TEXT, reason TEXT, asset_version_id TEXT,
  UNIQUE(closure_id, ordinal));
CREATE TABLE IF NOT EXISTS knowledge(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), kind TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL, sha256 TEXT NOT NULL,
  status TEXT NOT NULL, episode_id TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS citation(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task(id), knowledge_id TEXT NOT NULL REFERENCES knowledge(id),
  reason TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', version_id TEXT, created_at REAL NOT NULL,
  UNIQUE(task_id, knowledge_id));
CREATE TABLE IF NOT EXISTS topic(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), code TEXT NOT NULL,
  premise TEXT NOT NULL, mechanism TEXT NOT NULL DEFAULT '', motivation TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, episode_id TEXT, origin TEXT NOT NULL DEFAULT 'user', created_at REAL NOT NULL,
  UNIQUE(project_id, code));
CREATE TABLE IF NOT EXISTS story_group(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), name TEXT NOT NULL,
  shared_limits TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS group_member(
  group_id TEXT NOT NULL REFERENCES story_group(id), episode_id TEXT NOT NULL REFERENCES episode(id),
  chapter TEXT NOT NULL DEFAULT '', ordinal INTEGER NOT NULL, ending TEXT NOT NULL DEFAULT '',
  next_hook TEXT NOT NULL DEFAULT '', PRIMARY KEY(group_id, episode_id));
CREATE TABLE IF NOT EXISTS expert_auth(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, episodes TEXT NOT NULL, stop_stage TEXT NOT NULL,
  division TEXT NOT NULL, image_cap INTEGER NOT NULL DEFAULT 0, stops TEXT NOT NULL,
  state TEXT NOT NULL, stop_reason TEXT, log TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pack_adoption(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), ref TEXT NOT NULL,
  regression TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS task_pack(task_id TEXT NOT NULL, pack_id TEXT NOT NULL, PRIMARY KEY(task_id, pack_id));
CREATE TABLE IF NOT EXISTS canvas(episode_id TEXT PRIMARY KEY REFERENCES episode(id), doc TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ref_question(
  id TEXT PRIMARY KEY, pack_id TEXT NOT NULL REFERENCES ref_pack(id), at_ms INTEGER NOT NULL, question TEXT NOT NULL,
  provider TEXT NOT NULL, state TEXT NOT NULL, judgement TEXT, reason TEXT, evidence TEXT, frames TEXT NOT NULL,
  input_hash TEXT NOT NULL, diagnostic TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS backup(
  id TEXT PRIMARY KEY, relpath TEXT NOT NULL, manifest_hash TEXT NOT NULL, files INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
"""


def new_id():
    return str(uuid.uuid4())


def now():
    return time.time()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript(SCHEMA)


@contextmanager
def connect(path: Path):
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        db.close()


@contextmanager
def transaction(path: Path):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
