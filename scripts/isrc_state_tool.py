"""Safely show / backup / set / restore the pipeline's isrc_state.yaml (the 'next ISRC' counter).

The pipeline WRITES this on /submit (it increments last_sequence). For a live test:
    python scripts/isrc_state_tool.py backup                 # one-time safety copy
    python scripts/isrc_state_tool.py set --seq 899          # -> pipeline assigns UKPZD2600900+
    ... run the governed submit ...
    python scripts/isrc_state_tool.py restore                # put it back exactly as it was

ISRC = registrant(UKPZD) + 2-digit year(26) + 5-digit sequence. seq 899 -> next 900 -> UKPZD2600900.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DEFAULT = r"C:\Users\24kar\OneDrive\24kr-release-pipeline\config\isrc_state.yaml"


def _parse(p: Path) -> dict:
    out: dict = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and ":" in s:
            k, v = s.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _emit(p: Path, d: dict) -> None:
    p.write_text("".join(f"{k}: {v}\n" for k, v in d.items()), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="isrc_state.yaml backup/set/restore (live-test safety).")
    ap.add_argument("cmd", choices=["show", "backup", "set", "restore"])
    ap.add_argument("--path", default=DEFAULT)
    ap.add_argument("--seq", type=int, help="last_sequence to set (e.g. 899)")
    a = ap.parse_args()
    p = Path(a.path)
    bak = p.with_suffix(p.suffix + ".aurum-backup")
    if not p.exists():
        print(f"ERROR: {p} not found", file=sys.stderr)
        return 2

    if a.cmd == "show":
        print(p.read_text(encoding="utf-8"))
    elif a.cmd == "backup":
        shutil.copy2(p, bak)
        print(f"backed up -> {bak}")
    elif a.cmd == "restore":
        if not bak.exists():
            print(f"ERROR: no backup at {bak}", file=sys.stderr)
            return 2
        shutil.copy2(bak, p)
        print(f"restored from backup:\n{p.read_text(encoding='utf-8')}")
    elif a.cmd == "set":
        if a.seq is None:
            print("ERROR: --seq required", file=sys.stderr)
            return 2
        if not bak.exists():
            shutil.copy2(p, bak)
            print(f"(auto-backed up -> {bak})")
        d = _parse(p)
        d["last_sequence"] = str(a.seq)
        _emit(p, d)
        print(f"set last_sequence={a.seq}:\n{p.read_text(encoding='utf-8')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
