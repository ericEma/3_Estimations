"""
backup_db.py — Sauvegarde SQLite Estimation Élec (cloud local + sync Drive/OneDrive).

Profils : Hopitaux, Industriel, Autres
- Phase transition : une seule estimation_elec.db → copie dans les 3 dossiers / jour
- Phase cible : estimation_hopitaux.db, estimation_industriel.db, estimation_autres.db

Usage :
  python scripts/backup_db.py --launch     # quotidien + hebdo si vendredi >= 16h
  python scripts/backup_db.py --daily
  python scripts/backup_db.py --weekly
  python scripts/backup_db.py --force      # ignore marqueurs date
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LEGACY_DB = ROOT / "estimation_elec.db"
CLOUD_ROOT = ROOT / "backups" / "cloud"
LOG_FILE = ROOT / "logs" / "backup.log"

DAILY_RETENTION_DAYS = 14
WEEKLY_RETENTION_WEEKS = 4

PROFILES = {
    "Hopitaux": "estimation_hopitaux.db",
    "Industriel": "estimation_industriel.db",
    "Autres": "estimation_autres.db",
}


def _log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _iso_week_id(d: date | None = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _resolve_source_db(profile_name: str) -> Path | None:
    """Fichier source pour un profil : BDD dédiée si présente, sinon legacy."""
    dedicated = ROOT / PROFILES[profile_name]
    if dedicated.is_file():
        return dedicated
    if LEGACY_DB.is_file():
        return LEGACY_DB
    return None


def _sqlite_backup(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _read_marker(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _write_marker(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _rotate_daily(profile_dir: Path, prefix: str) -> None:
    pattern = re.compile(
        rf"^{re.escape(prefix)}_(\d{{4}}-\d{{2}}-\d{{2}})\.db$", re.IGNORECASE
    )
    files: list[tuple[date, Path]] = []
    for p in profile_dir.glob(f"{prefix}_*.db"):
        m = pattern.match(p.name)
        if m:
            try:
                files.append((date.fromisoformat(m.group(1)), p))
            except ValueError:
                continue
    files.sort(key=lambda x: x[0], reverse=True)
    for _, path in files[DAILY_RETENTION_DAYS:]:
        try:
            path.unlink()
            _log(f"rotation daily supprimé {path}")
        except OSError as exc:
            _log(f"rotation daily erreur {path}: {exc}")


def _rotate_weekly(weekly_dir: Path, prefix: str) -> None:
    pattern = re.compile(
        rf"^{re.escape(prefix)}_(\d{{4}}-W\d{{2}})\.db$", re.IGNORECASE
    )
    files: list[tuple[str, Path]] = []
    for p in weekly_dir.glob(f"{prefix}_*.db"):
        m = pattern.match(p.name)
        if m:
            files.append((m.group(1), p))
    files.sort(key=lambda x: x[0], reverse=True)
    for _, path in files[WEEKLY_RETENTION_WEEKS:]:
        try:
            path.unlink()
            _log(f"rotation weekly supprimé {path}")
        except OSError as exc:
            _log(f"rotation weekly erreur {path}: {exc}")


def backup_profile_daily(profile_name: str, force: bool = False) -> bool:
    src = _resolve_source_db(profile_name)
    if src is None:
        _log(f"daily {profile_name}: aucune BDD source")
        return False

    profile_dir = CLOUD_ROOT / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)
    marker = profile_dir / ".last_backup_date"
    today = date.today().isoformat()
    if not force and _read_marker(marker) == today:
        _log(f"daily {profile_name}: déjà fait ({today})")
        return False

    prefix = PROFILES[profile_name].replace(".db", "")
    dst = profile_dir / f"{prefix}_{today}.db"
    try:
        _sqlite_backup(src, dst)
        _write_marker(marker, today)
        _rotate_daily(profile_dir, prefix)
        _log(f"daily {profile_name}: OK {src.name} -> {dst}")
        return True
    except Exception as exc:
        _log(f"daily {profile_name}: ERREUR {exc}")
        return False


def backup_profile_weekly(profile_name: str, force: bool = False) -> bool:
    src = _resolve_source_db(profile_name)
    if src is None:
        _log(f"weekly {profile_name}: aucune BDD source")
        return False

    profile_dir = CLOUD_ROOT / profile_name
    weekly_dir = profile_dir / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    marker = profile_dir / ".last_weekly_backup"
    week_id = _iso_week_id()
    if not force and _read_marker(marker) == week_id:
        _log(f"weekly {profile_name}: déjà fait ({week_id})")
        return False

    prefix = PROFILES[profile_name].replace(".db", "")
    dst = weekly_dir / f"{prefix}_{week_id}.db"
    try:
        _sqlite_backup(src, dst)
        _write_marker(marker, week_id)
        _rotate_weekly(weekly_dir, prefix)
        _log(f"weekly {profile_name}: OK {src.name} -> {dst}")
        return True
    except Exception as exc:
        _log(f"weekly {profile_name}: ERREUR {exc}")
        return False


def run_daily(force: bool = False) -> int:
    ok = 0
    for name in PROFILES:
        if backup_profile_daily(name, force=force):
            ok += 1
    return ok


def run_weekly(force: bool = False) -> int:
    ok = 0
    for name in PROFILES:
        if backup_profile_weekly(name, force=force):
            ok += 1
    return ok


def should_run_weekly_on_launch() -> bool:
    now = datetime.now()
    if now.weekday() != 4:  # vendredi
        return False
    if now.hour < 16:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Sauvegarde BDD Estimation Élec")
    parser.add_argument("--launch", action="store_true", help="Quotidien + hebdo si vendredi >= 16h")
    parser.add_argument("--daily", action="store_true", help="Sauvegarde quotidienne (3 profils)")
    parser.add_argument("--weekly", action="store_true", help="Sauvegarde hebdomadaire (3 profils)")
    parser.add_argument("--force", action="store_true", help="Ignorer marqueurs date/semaine")
    args = parser.parse_args()

    if not (args.launch or args.daily or args.weekly):
        args.launch = True

    CLOUD_ROOT.mkdir(parents=True, exist_ok=True)
    _log("--- backup_db demarre ---")

    if args.launch or args.daily:
        run_daily(force=args.force)

    if args.weekly or (args.launch and should_run_weekly_on_launch()):
        run_weekly(force=args.force)

    _log("--- backup_db termine ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
