"""
Clone estimation_elec.db vers les 3 BDD profil (transition architecture multi-BDD).

Usage :
  python scripts/init_profile_databases.py
  python scripts/init_profile_databases.py --force
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_profiles import LEGACY_DB_FILE, PROFILE_DB_FILES, PROFILES, legacy_db_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise les 3 BDD profil depuis legacy")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Écrase les fichiers profil déjà présents",
    )
    args = parser.parse_args()

    src = legacy_db_path()
    if not src.is_file():
        print(f"Source introuvable : {src}")
        return 1

    for profile in PROFILES:
        dst = ROOT / PROFILE_DB_FILES[profile]
        if dst.is_file() and not args.force:
            print(f"  skip {dst.name} (existe, --force pour écraser)")
            continue
        shutil.copy2(src, dst)
        print(f"  OK  {dst.name} <- {LEGACY_DB_FILE}")

    print("Terminé. Relancez l'application et exécutez ensure_app_tables (démarrage Flask).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
