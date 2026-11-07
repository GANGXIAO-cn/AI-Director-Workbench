"""Darwin OS file-access probe, using only generated fixtures and system binaries."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

PROFILE = """(version 1)
(deny default)
(import "dyld-support.sb")
(allow process-exec (literal "/bin/cat") (literal "/usr/bin/tee"))
(allow file-read* (subpath "/usr/lib") (subpath "/System/Library")
                  (literal "/bin/cat") (literal "/usr/bin/tee"))
"""
LABELS = ["prompt", "reference-image", "settings", "prior-observation", "unlisted",
          "previous-task", "creation-chat", "global-config", "project-write"]


def run(root):
    for name in LABELS:
        (root / name).write_bytes(name.encode())
    (root / "reference-image").write_bytes(b"P6\n1 1\n255\n\x00\xcc\xff")
    (root / "linked-bait").symlink_to(root / "unlisted")
    labels = [*LABELS, "linked-bait"]
    contents = {label: (root / label).read_bytes() for label in labels}
    for label in LABELS:
        with (root / label).open("ab"):  # Host control: fixtures allow writes.
            pass
    env = {"HOME": str(root / "empty-home"), "LC_ALL": "C"}
    for stage, reads in [("cold_read", LABELS[:2]), ("comparison", LABELS[:4])]:
        policy, params = PROFILE, []
        for i, name in enumerate(reads):
            key = f"INPUT{i}"
            params += ["-D", f"{key}={root / name}"]
            policy += f'(allow file-read* (literal (param "{key}")))\n'
        profile = root / f"{stage}.sb"
        profile.write_text(policy)
        prefix = ["/usr/bin/sandbox-exec", *params, "-f", str(profile)]
        checks = []
        for mode, group in [("read", labels), ("write", ["project-write", *reads])]:
            for label in group:
                path = root / label
                command = ["/bin/cat", str(path)] if mode == "read" else ["/usr/bin/tee", "-a", str(path)]
                rc = subprocess.run([*prefix, *command], cwd=root, env=env, input=b"",
                                    capture_output=True, timeout=10)
                if rc.stderr.startswith(b"sandbox-exec:"):
                    print(rc.stderr.decode().strip())
                    return False
                allowed = mode == "read" and label in reads
                # A missing fixture, launch failure, or unrelated error is never an isolation pass.
                denied = rc.returncode != 0 and str(path).encode() in rc.stderr and any(
                    msg in rc.stderr for msg in (b"Operation not permitted", b"Permission denied"))
                ok = (rc.returncode == 0 and rc.stdout == contents[label]) if allowed else denied
                if not ok:
                    print(stage, mode, label, rc.returncode, rc.stderr[:300])
                    return False
                checks.append((mode, label, "allow" if allowed else "deny", ok))
        print(json.dumps(dict(stage=stage, profile_sha256=hashlib.sha256(policy.encode()).hexdigest(),
                              checks=checks), ensure_ascii=False))
        if not all(check[-1] for check in checks):
            return False
    return True


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="r7-isolation-") as tmp:
        root = Path(tmp).resolve()
        (root / "empty-home").mkdir()
        raise SystemExit(0 if run(root) else 1)
