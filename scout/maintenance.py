"""One-off repairs:
    .venv/bin/python -m scout.maintenance repair-vehicles
    .venv/bin/python -m scout.maintenance repair-roles
"""
from __future__ import annotations

import sys

from scout import db


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    db.init_db()
    if cmd == "repair-roles":
        from scout.ingest import repair_roles
        print(repair_roles())
        return 0
    if cmd == "repair-vehicles":
        from scout.provenance import repair_vehicle_links
        print(repair_vehicle_links())
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
