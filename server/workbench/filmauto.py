"""Start a film once, after the episode's publication copy has been adopted."""
import logging
import sqlite3
import time

from .cloudtask import PollInterrupted
from .db import connect, transaction
from . import videoplan


def register(db, episode, publish):
    db.execute(
        "INSERT INTO film_start(episode_id,publish,state) VALUES(?,?,'待开工') "
        "ON CONFLICT(episode_id) DO UPDATE SET publish=excluded.publish,state='待开工',delivery=NULL,error='' "
        "WHERE film_start.state IN ('待开工','开工受阻','等待自动重试') AND film_start.publish<>excluded.publish",
        (episode, publish),
    )


def resume_delivery(wb, delivery):
    """Entering production resumes only the current adopted full delivery."""
    with transaction(wb.db_path) as db:
        row=db.execute("SELECT d.episode_id,a.adopted_version_id FROM delivery d JOIN artifact a "
                       "ON a.episode_id=d.episode_id AND a.stage='publish' "
                       "WHERE d.id=? AND d.scope='full' AND d.state='deliverable' "
                       "AND json_extract(d.versions,'$.publish')=a.adopted_version_id "
                       "AND d.id=(SELECT id FROM delivery WHERE episode_id=d.episode_id AND scope='full' "
                       "AND state='deliverable' AND json_extract(versions,'$.publish')=a.adopted_version_id "
                       "ORDER BY rowid DESC LIMIT 1)",(delivery,)).fetchone()
        if not row:
            return {"state":"历史交付"}
        register(db,*row)
        return dict(db.execute("SELECT state,error FROM film_start WHERE episode_id=?",(row[0],)).fetchone())


def recover_existing(wb):
    # Recover interrupted local runs that explicitly started this workflow.
    with transaction(wb.db_path) as db:
        db.execute("""INSERT INTO film_start(episode_id,publish,state)
            SELECT a.episode_id,a.adopted_version_id,'待开工' FROM artifact a
            JOIN episode_cooperation c ON c.episode_id=a.episode_id AND c.mode='expert'
            WHERE a.stage='publish' AND a.adopted_version_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM film_start f WHERE f.episode_id=a.episode_id)
            AND EXISTS (SELECT 1 FROM delivery d WHERE d.episode_id=a.episode_id AND d.scope='full'
                        AND d.state='deliverable' AND json_extract(d.versions,'$.publish')=a.adopted_version_id)
            AND EXISTS (SELECT 1 FROM continuous_run r WHERE r.episode_id=a.episode_id
                        AND r.target_stage='delivery' AND r.state='done' AND json_extract(r.cursor,'$.roleProtocol')=1)""")


# Recheck eligibility after planning too: planning may outlast a pause or a new adoption.
_CANDIDATES = """
    SELECT f.*,r.attempts,r.retry_at,r.delivery AS previous_delivery,c.mode,
           (SELECT d.id FROM delivery d WHERE d.episode_id=f.episode_id
            AND d.scope='full' AND d.state='deliverable' AND json_extract(d.versions,'$.publish')=f.publish
            ORDER BY d.rowid DESC LIMIT 1) AS current_delivery
    FROM film_start f JOIN artifact a ON a.episode_id=f.episode_id AND a.stage='publish'
        AND a.adopted_version_id=f.publish
    LEFT JOIN film_start_recovery r ON r.episode_id=f.episode_id AND r.publish=f.publish
    LEFT JOIN episode_cooperation c ON c.episode_id=f.episode_id
    WHERE (f.state='待开工' OR (c.mode='expert' AND f.state IN ('开工受阻','等待自动重试')
           AND (r.attempts IS NULL OR r.retry_at<=? OR current_delivery IS NOT r.delivery)))
      AND NOT EXISTS (SELECT 1 FROM continuous_run run WHERE run.episode_id=f.episode_id
                      AND run.state IN ('running','waiting_external','paused'))
      AND (c.mode IS NOT 'expert' OR COALESCE((SELECT state FROM continuous_run run WHERE run.episode_id=f.episode_id
                    ORDER BY created_at DESC,rowid DESC LIMIT 1),'')<>'cancelled')
      AND NOT EXISTS (SELECT 1 FROM storage_removed s WHERE s.kind='delivery'
                      AND s.owner=current_delivery AND s.removed_at>0)
"""


def advance(wb, queue):
    with connect(wb.db_path) as db:
        pending = db.execute(_CANDIDATES + " ORDER BY f.rowid", (time.time(),)).fetchall()
    for item in pending:
        eid, publish = item["episode_id"], item["publish"]
        delivery = item["current_delivery"]
        try:
            if delivery is None:
                delivery = wb.deliver(eid, "full", "publish-final:" + publish)["deliveryId"]
            with queue.db() as qdb:
                if qdb.execute("SELECT 1 FROM episode_control WHERE delivery=? AND paused=1", (delivery,)).fetchone():
                    continue
            slots, _ = videoplan.material(queue.data, delivery)
            links = (
                videoplan.plan(queue.data, delivery, queue.root / delivery)
                if len(slots) > 1
                else []
            )
            items = [
                dict(
                    segment=s,
                    automatic=True,
                    dependencies=[
                        {k: v for k, v in d.items() if k != "target"}
                        for d in links
                        if d["target"] == s
                    ],
                )
                for s in slots
            ]
            with transaction(wb.db_path) as db:
                latest = db.execute(_CANDIDATES + " AND f.episode_id=? AND f.publish=?",
                                    (time.time(), eid, publish)).fetchone()
                if not latest or latest["current_delivery"] != delivery:
                    continue
                queue.enqueue_batch(delivery, items, whole_episode=True, respect_pause=True)
                db.execute(
                    "UPDATE film_start SET state='已开工',error='',delivery=? WHERE episode_id=?",
                    (delivery, eid),
                )
        except PollInterrupted:
            continue
        except Exception as exc:
            logging.exception("视频与配乐开工受阻")
            retryable = isinstance(exc, (videoplan.RetryablePlanError, ConnectionError, TimeoutError, FileNotFoundError))
            if isinstance(exc, sqlite3.OperationalError):
                code = getattr(exc, "sqlite_errorcode", 0) & 255
                retryable = code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
            attempts = (item["attempts"] or 0) + 1 if delivery == item["previous_delivery"] else 1
            retry_at = time.time() + min(300, 15 * 2 ** min(attempts - 1, 5)) if retryable and item["mode"] == "expert" else None
            with transaction(wb.db_path) as db:
                changed = db.execute(
                    "UPDATE film_start SET state=?,error=? WHERE episode_id=? AND publish=? AND state=?",
                    ("等待自动重试" if retry_at else "开工受阻", str(exc)[:500], eid, publish, item["state"]),
                )
                if changed.rowcount:
                    db.execute("INSERT INTO film_start_recovery(episode_id,publish,delivery,attempts,retry_at) VALUES(?,?,?,?,?) "
                               "ON CONFLICT(episode_id) DO UPDATE SET publish=excluded.publish,delivery=excluded.delivery,"
                               "attempts=excluded.attempts,retry_at=excluded.retry_at", (eid,publish,delivery,attempts,retry_at))
