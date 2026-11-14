"""Executor interface: probe / start / cancel. The service validates every result."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ExecRequest:
    run_id: str
    purpose: str            # create | revise | review
    kind: str               # contract branch: draft | review
    prompt: str             # full text, passed via stdin; built only from the input manifest
    schema: Optional[dict]  # vendor-shaped single branch; None for direct manuscript replies
    session_mode: str       # "new" (creation, may be resumed later) | "ephemeral" (review)
    workdir: Path           # empty per-run directory
    model: Optional[str] = None
    effort: Optional[str] = None
    timeout: int = 300
    images: list = field(default_factory=list)   # local files the reviewer must look at, in order
    connection: dict = field(default_factory=dict)
    attempt: int = 1        # initial request or the single format retry


@dataclass
class ExecOutcome:
    ok: bool
    structured: Optional[dict] = None
    session_id: Optional[str] = None
    reported_model: Optional[str] = None
    reported_effort: Optional[str] = None
    error_class: Optional[str] = None   # quota | authentication | timeout | network | schema | unknown
    diagnostic: str = ""
    quota_resets_at: Optional[str] = None
    events: list = field(default_factory=list)
    executable: Optional[str] = None
    version: Optional[str] = None
    isolation: dict = field(default_factory=dict)


class Executor:
    name = "base"

    def probe(self) -> dict:
        raise NotImplementedError

    def start(self, request: ExecRequest) -> ExecOutcome:
        raise NotImplementedError

    def cancel(self, run_id: str) -> bool:
        return False
