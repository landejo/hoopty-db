"""One-off repairs:  .venv/bin/python -m scout.maintenance repair-vehicles"""
from __future__ import annotations

import sys

from scout import db


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    db.init_db()
    if cmd == "repair-vehicles":
        from scout.provenance import repair_vehicle_links
        print(repair_vehicle_links())
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
