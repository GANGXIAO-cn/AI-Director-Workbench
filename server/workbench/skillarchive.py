"""Build the downloadable archive from the same skill directory production uses."""
import fcntl
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .bundledfinecut import SKILL


def archive(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (SKILL.name + ".zip")
    with (directory/"finecut-package.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        files = sorted(p for p in SKILL.rglob("*") if p.is_file() and p.name != ".DS_Store"
                       and "__pycache__" not in p.parts and p.suffix != ".pyc")
        entries = [p.relative_to(SKILL.parent).as_posix() for p in files]
        if target.exists() and target.stat().st_mtime_ns >= max(p.stat().st_mtime_ns for p in files):
            with ZipFile(target) as z:
                if z.namelist() == entries:
                    return target
        temp = target.with_suffix(".tmp")
        with ZipFile(temp, "w", ZIP_DEFLATED, compresslevel=3) as z:
            for p, entry in zip(files, entries):
                z.write(p, entry)
        os.replace(temp, target)
    return target
