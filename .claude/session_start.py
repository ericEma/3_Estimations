#!/usr/bin/env python3
"""
Hook SessionStart - Estimation Elec
Injecte le contexte de pilotage dans chaque nouvelle session Claude Code.
Routine : instructions.md + scan Excel + etat TODO.md + coherence BDD
"""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime

PROJECT_DIR = Path("E:/16_ Claude Code/3_ Estimations")
DB_PATH     = PROJECT_DIR / "estimation_elec.db"
TODO_PATH   = PROJECT_DIR / "TODO.md"
INSTR_PATH  = PROJECT_DIR / "instructions.md"

lines = []
lines.append("=" * 62)
lines.append("  SESSION DEMARRE - Estimation Elec")
lines.append(f"  {datetime.now().strftime('%d/%m/%Y a %H:%M')}")
lines.append("=" * 62)

# ── 1. instructions.md ────────────────────────────────────────
if INSTR_PATH.exists():
    lines.append("\n[OK] instructions.md charge")
    lines.append("     Roles : Claude Code (dev) / Eric (metier)")
    lines.append("     Regles cles : neutralisation complexite, inflation BT01, alertes aberrations")
else:
    lines.append("\n[WARN] instructions.md INTROUVABLE - verifier le repertoire")

# ── 2. Scan des fichiers Excel ────────────────────────────────
lines.append("\n[FICHIERS EXCEL]")
excel_files = sorted(PROJECT_DIR.glob("*.xlsx")) + sorted(PROJECT_DIR.glob("*.xls"))
if excel_files:
    for f in excel_files:
        size_kb = f.stat().st_size / 1024
        tag = ""
        name_lower = f.name.lower()
        if "dpgf" in name_lower or "modele" in name_lower or "mod" in name_lower:
            tag = " <- MODELE REFERENCE"
        elif "devis" in name_lower or "psa" in name_lower:
            tag = " <- DEVIS A IMPORTER"
        lines.append(f"  OK  {f.name} ({size_kb:.0f} Ko){tag}")
else:
    lines.append("  WARN Aucun fichier Excel trouve dans le repertoire projet")

# ── 3. Etat TODO.md ──────────────────────────────────────────
lines.append("\n[TODO.md - AVANCEMENT]")
if TODO_PATH.exists():
    todo_text = TODO_PATH.read_text(encoding="utf-8")
    todo_lines = todo_text.split("\n")
    done  = sum(1 for l in todo_lines if l.strip().startswith("- [x]"))
    total = sum(1 for l in todo_lines if l.strip().startswith("- ["))
    pct   = int(done / total * 100) if total else 0
    lines.append(f"  Progression : {done}/{total} taches ({pct}%)")
    for l in todo_lines:
        stripped = l.strip()
        if "**Sprint" in stripped:
            if stripped.startswith("- [x]"):
                lines.append(f"  [DONE] {stripped.replace('- [x] ', '')}")
            elif stripped.startswith("- [ ]"):
                lines.append(f"  [TODO] {stripped.replace('- [ ] ', '')}")
else:
    lines.append("  WARN TODO.md non trouve")

# ── 4. Coherence base SQLite ──────────────────────────────────
lines.append("\n[BASE DE DONNEES SQLite]")
if DB_PATH.exists():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur  = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cur.fetchall()]
        # Compte rapide des enregistrements par table
        counts = {}
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM [{t}]")
            counts[t] = cur.fetchone()[0]
        conn.close()
        lines.append(f"  OK  {DB_PATH.name}")
        for t in tables:
            lines.append(f"      Table [{t}] : {counts[t]} lignes")
        # Verification des tables attendues
        expected = {"dpgf_articles", "building_categories", "projects",
                    "devis_lines"}
        missing  = expected - set(tables)
        if missing:
            lines.append(f"  WARN Tables manquantes : {', '.join(sorted(missing))}")
        else:
            lines.append("  OK  Schema complet (toutes les tables presentes)")
    except Exception as e:
        lines.append(f"  ERR Erreur lecture BDD : {e}")
else:
    lines.append(f"  INFO Base non encore creee (Sprint 1 en attente)")
    lines.append(f"       Chemin prevu : {DB_PATH}")

lines.append("\n" + "=" * 62)
lines.append("  Contexte charge. Pret a travailler.")
lines.append("=" * 62)

context = "\n".join(lines)

# Output JSON -> injecte dans le contexte de Claude
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context
    }
}, ensure_ascii=False))
