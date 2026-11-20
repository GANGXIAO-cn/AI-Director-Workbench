"""Workbench-wide RunningHub workflow bindings, shared by all dispatch accounts."""
import re

DEFAULTS = {"h3": "2106938127764324354", "suno": "2123292947306582017"}
NAMES = {"h3": "H3 全能参考视频", "suno": "Suno 配乐"}


def initialize(db):
    if db.execute("SELECT count(*) FROM online_workflow").fetchone()[0] == len(
        DEFAULTS
    ):
        return
    defaults = DEFAULTS.copy()
    # A pre-upgrade queue stored H3's binding on the account. Preserve an
    # unambiguous existing choice when moving to a shared workflow catalog.
    if any(col[1] == "workflow" for col in db.execute("PRAGMA table_info(account)")):
        legacy = db.execute(
            "SELECT DISTINCT workflow FROM account WHERE workflow IS NOT NULL AND workflow<>''"
        ).fetchall()
        if len(legacy) == 1 and re.fullmatch(r"[0-9]+", legacy[0][0]):
            defaults["h3"] = legacy[0][0]
    db.executemany(
        "INSERT OR IGNORE INTO online_workflow(kind,workflow) VALUES(?,?)",
        defaults.items(),
    )


def list_entries(db):
    return [
        dict(
            kind=kind,
            name=NAMES[kind],
            workflow=workflow,
            url="https://www.runninghub.ai/zh-cn/workflow/" + workflow,
        )
        for kind, workflow in db.execute(
            "SELECT kind,workflow FROM online_workflow ORDER BY kind"
        )
    ]


def save(db, kind, workflow):
    if kind not in DEFAULTS or not re.fullmatch(r"[0-9]+", workflow.strip()):
        raise ValueError("请输入有效的在线工作流编号")
    db.execute(
        "UPDATE online_workflow SET workflow=? WHERE kind=?", (workflow.strip(), kind)
    )
