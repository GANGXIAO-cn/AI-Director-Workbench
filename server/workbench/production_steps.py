"""Report work at the point it starts; nested media helpers share the worker's observer."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import time

from .storage import canonical, write_atomic

_observer = ContextVar("production_step_observer", default=None)


@contextmanager
def observe(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


def step(label):
    callback = _observer.get()
    if callback:
        callback(label)


def read_step(folder):
    path = folder / "work-step.json"
    return json.loads(path.read_bytes()) if path.is_file() else {}


def save_step(folder, label):
    previous = read_step(folder)
    if previous.get("label") == label:
        return
    entry = dict(label=label, since=time.time())
    history = previous.get("history", [previous] if previous else [])
    write_atomic(folder / "work-step.json", canonical(dict(**entry, history=[*history, entry])))
