"""Rebuild the public website into docs/ (the folder GitHub Pages serves).

Run from the repository root:

    python scripts/publish.py

It reads whatever private checkpoints exist (``private-data/`` by default, or
the folder named by ``OBS_STATE``) and refreshes ``docs/`` with the finished
site. Commit and push ``docs/`` to publish.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "docs"
    env = dict(os.environ, OBS_OUTPUT=str(out))
    subprocess.run([sys.executable, str(ROOT / "scripts/build.py")], env=env, check=True)
    print(f"Published to {out}")


if __name__ == "__main__":
    main()
