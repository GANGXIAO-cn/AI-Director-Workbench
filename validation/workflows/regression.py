"""Named cross-cutting acceptance entry, without copying environments or data."""
from pathlib import Path
import subprocess
import sys


def test_X_T1_backend_suite():
    """X-T1: all existing manual and continuous backend tests must pass."""
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, '-m', 'pytest', 'tests', '-q'], cwd=root/'server', check=True)


if __name__ == '__main__':
    test_X_T1_backend_suite()
