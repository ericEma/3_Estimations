"""
engine_bibliotheque_ratios.py — Totaux par lot alignés sur la page Bibliothèque DPGF.

Même logique que static/js/bibliotheque.js (calcTotal, calcSectionRatio, updateKPIs) :
  - PU = pu_ht_ref (référentiel PSA, sans ratio_overrides sur l'écran bibliothèque nue)
  - SURFACIQUE : total = pu × SDO (CFO/CFA) ou pu × kWc (PV)
  - Ratios de section manuels (bibliotheque_section_ratios) prioritaires
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, "estimation_elec.db")

CHAP_ORDER = ["Courants Forts", "Courants faibles", "Photovoltaïque"]


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _is_pv_chapter(chapter: str) -> bool:
    c = (chapter or "").lower()
    return "photov" in c


def _lot_key(chapter: str) -> str:
    c = (chapter or "").lower()
    if "faible" in c or "cfa" in c:
        return "CFA"
    if _is_pv_chapter(chapter):
        return "PV"
    return "CFO"


def _article_line_total(art: dict, sdo: float, kwc: float) -> float:
    pu = float(art.get("pu_ht") or 0)
    if pu <= 0:
        return 0.0
    qty = float(art.get("quantity") or 0)
    ratio_type = (art.get("ratio_type") or "").upper()
    if ratio_type == "SURFACIQUE":
        divisor = max(kwc, 1.0) if _is_pv_chapter(art.get("chapter") or "") else max(sdo, 1.0)
        return qty * pu if qty > 0 else pu * divisor
    return qty * pu if qty > 0 else 0.0


def compute_bibliotheque_lot_totals(
    surface_sdo: float = 1000.0,
    puissance_pv_kwc: float = 100.0,
) -> dict:
    """
    Retourne les totaux HT par lot (CFO, CFA, PV) et ratios unitaires de la base de prix.

    ratio_m2_cfo / ratio_m2_cfa : €/m² (Σ articles / SDO)
    ratio_kwc_pv : €/kWc (Σ PV / kWc)
    """
    sdo = max(float(surface_sdo or 1000), 1.0)
    kwc = max(float(puissance_pv_kwc or 100), 1.0)

    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT da.chapter, da.section, da.ratio_type, da.qty_ref AS quantity,
                   da.pu_ht_ref AS pu_ht
            FROM dpgf_articles da
            WHERE da.row_type = 'article'
              AND (da.is_hidden IS NULL OR da.is_hidden = 0)
            ORDER BY da.chapter, da.row_order
            """
        ).fetchall()

        sec_ratios_rows = conn.execute(
            "SELECT chapter, section, ratio_m2, ratio_unit FROM bibliotheque_section_ratios"
        ).fetchall()
    finally:
        conn.close()

    sec_ratios = {
        f"{r['chapter']}|||{r['section']}": {
            "ratio": float(r["ratio_m2"]),
            "unit": (r["ratio_unit"] or "m2").lower(),
        }
        for r in sec_ratios_rows
    }

    tree: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        art = dict(r)
        tree[art["chapter"]][art["section"]].append(art)

    totals = {"CFO": 0.0, "CFA": 0.0, "PV": 0.0}

    for chap in CHAP_ORDER:
        if chap not in tree:
            continue
        lot = _lot_key(chap)
        for section, sec_arts in tree[chap].items():
            sec_key = f"{chap}|||{section}"
            if sec_key in sec_ratios:
                sr = sec_ratios[sec_key]
                unit = sr["unit"]
                divisor = kwc if unit == "kwc" else sdo
                sec_total = sr["ratio"] * divisor
            else:
                sec_total = sum(_article_line_total(a, sdo, kwc) for a in sec_arts)
            totals[lot] += sec_total

    return {
        "totals": totals,
        "ratio_m2_cfo": round(totals["CFO"] / sdo, 2),
        "ratio_m2_cfa": round(totals["CFA"] / sdo, 2),
        "ratio_kwc_pv": round(totals["PV"] / kwc, 4),
        "surface_sdo": sdo,
        "puissance_pv_kwc": kwc,
    }
