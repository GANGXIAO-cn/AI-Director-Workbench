"""Command errors and small row helpers shared by all service modules."""
import json


class NotFound(Exception):
    status = 404


class Rejected(Exception):
    status = 422

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail


class ExecutorUnavailable(Rejected):
    """A dispatch problem; rewriting a manuscript cannot resolve it."""


class Conflict(Exception):
    status = 409

    def __init__(self, message, current=None):
        super().__init__(message)
        self.current = current


def row(db, sql, *args, missing="对象不存在"):
    r = db.execute(sql, args).fetchone()
    if r is None:
        raise NotFound(missing)
    return dict(r)


def loads(r, *keys):
    for key in keys:
        if r.get(key) is not None and isinstance(r[key], str):
            r[key] = json.loads(r[key])
    return r
