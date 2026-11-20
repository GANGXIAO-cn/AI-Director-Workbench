"""Count successful video rounds and hold capacity for in-flight submissions across keys, deliveries and restarts of the same episode."""
import json
import sqlite3
from contextlib import closing

MAX_REWORKS_PER_SEGMENT = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS video_round_state(delivery TEXT,segment TEXT,round INTEGER,state TEXT NOT NULL,PRIMARY KEY(delivery,segment,round));
CREATE TABLE IF NOT EXISTS video_scope(delivery TEXT PRIMARY KEY,episode TEXT NOT NULL,segments TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS video_round(delivery TEXT,segment TEXT,round INTEGER,PRIMARY KEY(delivery,segment,round));
"""


def register(queue, db, delivery, segments):
    if db.execute("SELECT 1 FROM video_scope WHERE delivery=?", (delivery,)).fetchone():
        return
    episode = delivery
    main = queue.data / "workbench.sqlite"
    if main.is_file():
        with closing(sqlite3.connect(main.as_uri() + "?mode=ro", uri=True)) as source:
            (episode,) = source.execute(
                "SELECT episode_id FROM delivery WHERE id=?", (delivery,)
            ).fetchone()
            # Trial deliveries contain just a subset. Use the episode's actual segment list.
            row = source.execute(
                "SELECT v.content FROM artifact a JOIN version v ON a.adopted_version_id=v.id "
                "WHERE a.episode_id=? AND a.stage='plan'",
                (episode,),
            ).fetchone()
            if row:
                planned = [s["id"] for s in json.loads(row[0]).get("segments", [])]
                if planned and set(segments).issubset(planned):
                    segments = planned
    db.execute(
        "INSERT INTO video_scope VALUES(?,?,?)",
        (delivery, episode, json.dumps(list(segments))),
    )
    for job in db.execute("SELECT * FROM job WHERE delivery=?", (delivery,)):
        current = job["round"]
        reserved = bool(job["account"] or job["adopted"] or job["state"] != "排队中")
        db.executemany(
            "INSERT OR IGNORE INTO video_round VALUES(?,?,?)",
            [(delivery, job["segment"], n) for n in range(current + int(reserved))],
        )


def limits(db, delivery):
    row = db.execute(
        "SELECT episode,segments FROM video_scope WHERE delivery=?", (delivery,)
    ).fetchone()
    if not row:
        return None
    segments = json.loads(row["segments"])
    rows = db.execute(
        "SELECT r.segment,coalesce(o.state,'reserved') FROM video_round r "
        "JOIN video_scope s USING(delivery) LEFT JOIN video_round_state o USING(delivery,segment,round) "
        "WHERE s.episode=?", (row["episode"],)
    )
    counts, held = {}, {}
    for segment, state in rows:
        if state == "failed":
            continue
        bucket = counts if state == "success" else held
        bucket[segment] = bucket.get(segment, 0) + 1
    rework_limit = len(segments) * MAX_REWORKS_PER_SEGMENT
    return dict(
        used=sum(counts.values()), reserved=sum(held.values()), limit=len(segments) + rework_limit,
        reworkLimit=rework_limit, reworkUsed=sum(n - 1 for n in counts.values()),
        firstRemaining=sum(s not in counts and s not in held for s in segments),
        counts=counts, held=held,
    )


def mark(db, delivery, segment, number, state):
    db.execute("INSERT INTO video_round_state VALUES(?,?,?,?) ON CONFLICT(delivery,segment,round) "
               "DO UPDATE SET state=excluded.state WHERE video_round_state.state<>'success'",
               (delivery, segment, number, state))


def reconcile(queue, db):
    """Classify pre-upgrade reservations by their durable download receipts, once."""
    for d,s,n,adopted,current in db.execute(
        "SELECT r.delivery,r.segment,r.round,j.adopted,j.round FROM video_round r JOIN job j USING(delivery,segment) "
        "LEFT JOIN video_round_state o USING(delivery,segment,round) WHERE o.state IS NULL"
    ).fetchall():
        folder = queue.directory(d,s,n)
        if adopted and n == current and not (folder/"trial.json").is_file():
            folder = queue.data/adopted
        path = folder/"trial.json"
        receipt = json.loads(path.read_bytes()) if path.is_file() else {}
        state = receipt.get("status")
        result = "reserved"
        if state == "downloaded" and (folder/"result.mp4").is_file():
            result = "success"
        elif state in ("rejected","cloud_failed","capacity"):
            result = "failed"
        mark(db,d,s,n,result)


def reserve(db, delivery, segment, round_no):
    existing = db.execute(
        "SELECT coalesce(o.state,'reserved') FROM video_round r LEFT JOIN video_round_state o USING(delivery,segment,round) "
        "WHERE r.delivery=? AND r.segment=? AND r.round=?", (delivery, segment, round_no)
    ).fetchone()
    if existing and existing[0] != "failed":
        return ""
    budget = limits(db, delivery)
    count = budget["counts"].get(segment, 0) + budget["held"].get(segment, 0)
    if round_no > MAX_REWORKS_PER_SEGMENT or count >= 1 + MAX_REWORKS_PER_SEGMENT:
        return "本段已用完首发和三次返工名额"
    if budget["used"] + budget["reserved"] + 1 + budget["firstRemaining"] - int(count == 0) > budget["limit"]:
        return "本集返工名额已用完，保留其余分段的首发名额"
    db.execute("INSERT OR IGNORE INTO video_round VALUES(?,?,?)", (delivery, segment, round_no))
    mark(db,delivery,segment,round_no,"reserved")
    return ""
