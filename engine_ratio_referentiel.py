"""
Moteur ratios typologie — devis réels → ratio_devis_sources → agrégats par bâtiment.

- Totaux lot depuis parse brut (pas matching DPGF)
- Actualisation 3 % / an (CLAUDE §3.9)
- Pondération jeunesse (CLAUDE §3.10)
- Devis TOTAL SEUL : imputation parts par typologie (Option A)
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import db_ratios
from scripts.read_excel import parse_devis, ArticleRow

TAUX = db_ratios.TAUX_ACTUALISATION_ANNUEL


def derive_lot_from_chapter(chapter: str | None) -> str:
    d = (chapter or "").lower()
    if "faible" in d or "cfa" in d or "ssi" in d:
        return "CFA"
    if "photovolta" in d or "pv" in d:
        return "PV"
    return "CFO"


def compute_lot_totals_from_rows(rows: list[ArticleRow]) -> dict[str, float]:
    totals = {"CFO": 0.0, "CFA": 0.0, "PV": 0.0}
    for r in rows:
        if r.row_type not in ("article", "so"):
            continue
        th = r.total_ht
        if th is None or th <= 0:
            continue
        lot = derive_lot_from_chapter(r.chapter)
        totals[lot] += float(th)
    return totals


def classify_detail_level(
    lot_totals: dict[str, float],
    total_ht_source: float | None,
) -> tuple[str, float, float, float, float, bool]:
    """
    Retourne detail_level, total_ht, cfo, cfa, pv, imputed.
    """
    cfo = lot_totals.get("CFO") or 0.0
    cfa = lot_totals.get("CFA") or 0.0
    pv = lot_totals.get("PV") or 0.0
    line_sum = cfo + cfa + pv
    total_ht = total_ht_source if total_ht_source and total_ht_source > 0 else line_sum

    if line_sum <= 0 and total_ht > 0:
        return "total_only", total_ht, 0.0, 0.0, 0.0, True

    if cfo > 0 and cfa > 0:
        return "full", total_ht, cfo, cfa, pv, False

    if line_sum > 0:
        return "partial", total_ht, cfo, cfa, pv, False

    raise ValueError("Aucun montant HT exploitable dans le devis")


def _parse_devis_date(value: str) -> date:
    s = str(value or "")[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _age_months(devis_date: date, reference: date | None = None) -> float:
    ref = reference or date.today()
    days = max((ref - devis_date).days, 0)
    return days / 30.4375


def temporal_weight(age_months: float, conn: sqlite3.Connection) -> float:
    rows = conn.execute(
        """
        SELECT age_months_min, age_months_max, weight
        FROM ratio_temporal_weights
        ORDER BY age_months_min
        """
    ).fetchall()
    for r in rows:
        lo = int(r["age_months_min"])
        hi = r["age_months_max"]
        if hi is None:
            if age_months >= lo:
                return float(r["weight"])
        elif lo <= age_months <= hi:
            return float(r["weight"])
    return 0.1


def actualize_ratio(ratio: float, devis_date: date, ref_year: int) -> float:
    annee_devis = devis_date.year
    nb = ref_year - annee_devis
    return ratio * ((1.0 + TAUX) ** nb)


def _fiabilite(nb: int) -> str:
    if nb <= 0:
        return "AUCUNE_REF"
    if nb == 1:
        return "SOURCE_UNIQUE"
    if nb < 3:
        return "PRUDENCE"
    return "OK"


def compute_unit_ratios(
    *,
    total_ht_cfo: float,
    total_ht_cfa: float,
    total_ht_pv: float,
    total_ht: float,
    surface_sdo: float,
    puissance_pv_kwc: float,
    coef_cfo: float,
    coef_cfa: float,
    coef_pv: float,
    devis_date: str,
    ref_year: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    own = conn is None
    if own:
        conn = db_ratios.connect()
    if ref_year is None:
        ref_year = db_ratios.get_annee_reference(conn)
    d = _parse_devis_date(devis_date)
    sdo = max(float(surface_sdo), 1.0)
    kwc = max(float(puissance_pv_kwc or 0), 0.0)
    ccfo = max(float(coef_cfo or 1.0), 0.01)
    ccfa = max(float(coef_cfa or 1.0), 0.01)
    cpv = max(float(coef_pv or 1.0), 0.01)

    def _ratio_act(total_lot: float, divisor: float, coef: float) -> float | None:
        if total_lot <= 0 or divisor <= 0:
            return None
        raw = (total_lot / coef) / divisor
        return round(actualize_ratio(raw, d, ref_year), 4)

    out = {
        "annee_reference": ref_year,
        "ratio_cfo_m2_actualise": _ratio_act(total_ht_cfo, sdo, ccfo),
        "ratio_cfa_m2_actualise": _ratio_act(total_ht_cfa, sdo, ccfa),
        "ratio_pv_kwc_actualise": _ratio_act(total_ht_pv, kwc, cpv) if kwc > 0 else None,
        "ratio_total_m2_actualise": _ratio_act(total_ht, sdo, 1.0) if total_ht > 0 else None,
    }
    if own:
        conn.close()
    return out


def compute_typology_shares(
    category_name: str,
    conn: sqlite3.Connection,
    reference: date | None = None,
) -> dict[str, float]:
    """Parts CFO/CFA/PV depuis devis COMPLETS non imputés de la typologie."""
    rows = conn.execute(
        """
        SELECT total_ht, total_ht_cfo, total_ht_cfa, total_ht_pv, devis_date
        FROM ratio_devis_sources
        WHERE is_active = 1
          AND category_name = ?
          AND detail_level = 'full'
          AND imputed = 0
          AND total_ht > 0
        """,
        (category_name,),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            """
            SELECT total_ht, total_ht_cfo, total_ht_cfa, total_ht_pv, devis_date
            FROM ratio_devis_sources
            WHERE is_active = 1
              AND detail_level = 'full'
              AND imputed = 0
              AND total_ht > 0
            """
        ).fetchall()

    num_cfo = num_cfa = num_pv = 0.0
    den = 0.0
    for r in rows:
        th = float(r["total_ht"] or 0)
        if th <= 0:
            continue
        w = temporal_weight(_age_months(_parse_devis_date(r["devis_date"]), reference), conn)
        num_cfo += float(r["total_ht_cfo"] or 0) / th * w
        num_cfa += float(r["total_ht_cfa"] or 0) / th * w
        num_pv += float(r["total_ht_pv"] or 0) / th * w
        den += w

    if den <= 0:
        return {"share_cfo": 0.5, "share_cfa": 0.5, "share_pv": 0.0, "nb_full_sources": 0}

    return {
        "share_cfo": num_cfo / den,
        "share_cfa": num_cfa / den,
        "share_pv": num_pv / den,
        "nb_full_sources": len(rows),
    }


def impute_lot_totals(
    total_ht: float,
    shares: dict[str, float],
) -> tuple[float, float, float]:
    return (
        total_ht * shares.get("share_cfo", 0.5),
        total_ht * shares.get("share_cfa", 0.5),
        total_ht * shares.get("share_pv", 0.0),
    )


def upsert_typology_shares(category_name: str, conn: sqlite3.Connection) -> None:
    sh = compute_typology_shares(category_name, conn)
    conn.execute(
        """
        INSERT INTO ratio_typology_shares
            (category_name, share_cfo, share_cfa, share_pv, nb_full_sources, computed_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(category_name) DO UPDATE SET
            share_cfo = excluded.share_cfo,
            share_cfa = excluded.share_cfa,
            share_pv = excluded.share_pv,
            nb_full_sources = excluded.nb_full_sources,
            computed_at = CURRENT_TIMESTAMP
        """,
        (
            category_name,
            round(sh["share_cfo"], 6),
            round(sh["share_cfa"], 6),
            round(sh["share_pv"], 6),
            int(sh["nb_full_sources"]),
        ),
    )


def _insert_ratio_source_record(
    *,
    name: str,
    category_name: str,
    devis_date: str,
    surface_sdo: float,
    puissance_pv_kwc: float,
    kva_cible: float | None,
    cfo: float,
    cfa: float,
    pv: float,
    total_ht: float,
    detail_level: str,
    imputed: bool,
    coef_cfo: float,
    coef_cfa: float,
    coef_pv: float,
    source: str,
    source_file: str | None,
    price_profile: str | None,
    project_id: int | None,
    import_batch_id: str | None,
    notes: str | None,
    conn: sqlite3.Connection,
) -> int:
    """Persiste une source + computed + recalc agrégats typologie."""
    if pv > 0 and (not puissance_pv_kwc or float(puissance_pv_kwc) <= 0):
        raise ValueError(
            "Puissance PV (kWc) obligatoire : le devis contient un lot Photovoltaïque."
        )

    cur = conn.execute(
        """
        INSERT INTO ratio_devis_sources (
            name, category_name, devis_date, surface_sdo, kva_cible,
            puissance_pv_kwc, total_ht, total_ht_cfo, total_ht_cfa, total_ht_pv,
            coef_cfo, coef_cfa, coef_pv, detail_level, imputed,
            source, source_file, price_profile, project_id, import_batch_id, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            category_name,
            str(devis_date)[:10],
            float(surface_sdo),
            kva_cible,
            float(puissance_pv_kwc or 0),
            total_ht,
            cfo,
            cfa,
            pv,
            float(coef_cfo),
            float(coef_cfa),
            float(coef_pv),
            detail_level,
            1 if imputed else 0,
            source,
            source_file,
            price_profile,
            project_id,
            import_batch_id,
            notes,
        ),
    )
    source_id = int(cur.lastrowid)
    ref_year = db_ratios.get_annee_reference(conn)
    comp = compute_unit_ratios(
        total_ht_cfo=cfo,
        total_ht_cfa=cfa,
        total_ht_pv=pv,
        total_ht=total_ht,
        surface_sdo=surface_sdo,
        puissance_pv_kwc=puissance_pv_kwc or 0,
        coef_cfo=coef_cfo,
        coef_cfa=coef_cfa,
        coef_pv=coef_pv,
        devis_date=devis_date,
        ref_year=ref_year,
        conn=conn,
    )
    conn.execute(
        """
        INSERT INTO ratio_source_computed (
            source_id, annee_reference,
            ratio_cfo_m2_actualise, ratio_cfa_m2_actualise,
            ratio_pv_kwc_actualise, ratio_total_m2_actualise
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            comp["annee_reference"],
            comp["ratio_cfo_m2_actualise"],
            comp["ratio_cfa_m2_actualise"],
            comp["ratio_pv_kwc_actualise"],
            comp["ratio_total_m2_actualise"],
        ),
    )
    upsert_typology_shares(category_name, conn)
    recompute_aggregates(category_name, conn=conn)
    return source_id


def insert_archive_source(
    *,
    name: str,
    category_name: str,
    devis_date: str,
    surface_sdo: float,
    puissance_pv_kwc: float = 0.0,
    total_ht_cfo: float = 0.0,
    total_ht_cfa: float = 0.0,
    total_ht_pv: float = 0.0,
    total_ht_combined: float | None = None,
    notes: str | None = None,
    source_file: str | None = None,
    import_batch_id: str | None = None,
    coef_cfo: float = 1.0,
    coef_cfa: float = 1.0,
    coef_pv: float = 1.0,
) -> int:
    """
    Enregistre une ligne archive Excel (onglet REX) sans parse devis.
    Recalcule ratios via moteur app (§3.9 / §3.10) — ignore colonnes O–R Excel.
    """
    lot_totals = {
        "CFO": float(total_ht_cfo or 0),
        "CFA": float(total_ht_cfa or 0),
        "PV": float(total_ht_pv or 0),
    }
    total_ht_source = float(total_ht_combined) if total_ht_combined else None
    detail_level, total_ht, cfo, cfa, pv, imputed = classify_detail_level(
        lot_totals, total_ht_source
    )

    conn = db_ratios.connect()
    try:
        if detail_level == "total_only" or imputed:
            upsert_typology_shares(category_name, conn)
            row_sh = conn.execute(
                """
                SELECT share_cfo, share_cfa, share_pv FROM ratio_typology_shares
                WHERE category_name = ?
                """,
                (category_name,),
            ).fetchone()
            shares = {
                "share_cfo": float(row_sh["share_cfo"]) if row_sh else 0.5,
                "share_cfa": float(row_sh["share_cfa"]) if row_sh else 0.5,
                "share_pv": float(row_sh["share_pv"]) if row_sh else 0.0,
            }
            cfo, cfa, pv = impute_lot_totals(total_ht, shares)
            imputed = True
            detail_level = "total_only"

        if (not puissance_pv_kwc or float(puissance_pv_kwc) <= 0) and pv > 0:
            if cfo + cfa > 0:
                r = cfo / (cfo + cfa)
                cfo = round(cfo + pv * r, 2)
                cfa = round(cfa + pv * (1 - r), 2)
            else:
                cfo = round(cfo + pv, 2)
            pv = 0.0
            total_ht = round(cfo + cfa + pv, 2)

        source_id = _insert_ratio_source_record(
            name=name,
            category_name=category_name,
            devis_date=devis_date,
            surface_sdo=surface_sdo,
            puissance_pv_kwc=puissance_pv_kwc,
            kva_cible=None,
            cfo=cfo,
            cfa=cfa,
            pv=pv,
            total_ht=total_ht,
            detail_level=detail_level,
            imputed=imputed,
            coef_cfo=coef_cfo,
            coef_cfa=coef_cfa,
            coef_pv=coef_pv,
            source="excel_archive",
            source_file=source_file,
            price_profile=None,
            project_id=None,
            import_batch_id=import_batch_id,
            notes=notes,
            conn=conn,
        )
        conn.commit()
        return source_id
    finally:
        conn.close()


def delete_sources_by_batch(import_batch_id: str) -> int:
    """Supprime les sources d'un lot bootstrap (recalc agrégats après)."""
    conn = db_ratios.connect()
    try:
        cats = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT category_name FROM ratio_devis_sources
                WHERE import_batch_id = ?
                """,
                (import_batch_id,),
            ).fetchall()
        ]
        cur = conn.execute(
            "DELETE FROM ratio_devis_sources WHERE import_batch_id = ?",
            (import_batch_id,),
        )
        n = cur.rowcount
        for cat in cats:
            upsert_typology_shares(cat, conn)
            recompute_aggregates(cat, conn=conn)
        conn.commit()
        return n
    finally:
        conn.close()


def insert_devis_source(
    *,
    name: str,
    category_name: str,
    devis_date: str,
    surface_sdo: float,
    puissance_pv_kwc: float = 0.0,
    kva_cible: float | None = None,
    coef_cfo: float = 1.0,
    coef_cfa: float = 1.0,
    coef_pv: float = 1.0,
    filepath: str | Path,
    total_ht_cell: str | None = None,
    source_file: str | None = None,
    price_profile: str | None = None,
    project_id: int | None = None,
    source: str = "devis_import",
) -> int:
    """
    Parse le devis, enregistre ratio_devis_sources + computed, recalcule agrégats typologie.
    """
    from scripts.import_devis import read_total_ht_from_cell

    path = Path(filepath)
    rows, _stats, _auto_total = parse_devis(path)
    lot_totals = compute_lot_totals_from_rows(rows)

    total_ht_source = None
    if total_ht_cell:
        total_ht_source = read_total_ht_from_cell(path, total_ht_cell)
    if not total_ht_source and _auto_total:
        total_ht_source = float(_auto_total)

    detail_level, total_ht, cfo, cfa, pv, imputed = classify_detail_level(
        lot_totals, total_ht_source
    )

    conn = db_ratios.connect()
    try:
        if detail_level == "total_only" or imputed:
            upsert_typology_shares(category_name, conn)
            row_sh = conn.execute(
                "SELECT share_cfo, share_cfa, share_pv FROM ratio_typology_shares WHERE category_name = ?",
                (category_name,),
            ).fetchone()
            shares = {
                "share_cfo": float(row_sh["share_cfo"]) if row_sh else 0.5,
                "share_cfa": float(row_sh["share_cfa"]) if row_sh else 0.5,
                "share_pv": float(row_sh["share_pv"]) if row_sh else 0.0,
            }
            cfo, cfa, pv = impute_lot_totals(total_ht, shares)
            imputed = True
            detail_level = "total_only"

        if pv > 0 and (not puissance_pv_kwc or float(puissance_pv_kwc) <= 0):
            raise ValueError(
                "Puissance PV (kWc) obligatoire : le devis contient un lot Photovoltaïque."
            )

        source_id = _insert_ratio_source_record(
            name=name,
            category_name=category_name,
            devis_date=devis_date,
            surface_sdo=surface_sdo,
            puissance_pv_kwc=puissance_pv_kwc,
            kva_cible=kva_cible,
            cfo=cfo,
            cfa=cfa,
            pv=pv,
            total_ht=total_ht,
            detail_level=detail_level,
            imputed=imputed,
            coef_cfo=coef_cfo,
            coef_cfa=coef_cfa,
            coef_pv=coef_pv,
            source=source,
            source_file=source_file or path.name,
            price_profile=price_profile,
            project_id=project_id,
            import_batch_id=None,
            notes=None,
            conn=conn,
        )
        conn.commit()
        return source_id
    finally:
        conn.close()


def recompute_aggregates(
    category_name: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    own = conn is None
    if own:
        conn = db_ratios.connect()
    ref_year = db_ratios.get_annee_reference(conn)
    reference = date.today()

    if category_name:
        categories = [category_name]
    else:
        categories = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT category_name FROM ratio_devis_sources
                WHERE is_active = 1
                """
            ).fetchall()
        ]

    for cat in categories:
        sources = conn.execute(
            """
            SELECT s.id, s.devis_date, s.imputed, s.detail_level,
                   c.ratio_cfo_m2_actualise, c.ratio_cfa_m2_actualise,
                   c.ratio_pv_kwc_actualise
            FROM ratio_devis_sources s
            JOIN ratio_source_computed c ON c.source_id = s.id
            WHERE s.is_active = 1 AND s.category_name = ?
            """,
            (cat,),
        ).fetchall()

        buckets: dict[tuple[str, str], list[tuple[float, float]]] = {
            ("CFO", "EUR_M2"): [],
            ("CFA", "EUR_M2"): [],
            ("PV", "EUR_KWC"): [],
        }
        for s in sources:
            age = _age_months(_parse_devis_date(s["devis_date"]), reference)
            w = temporal_weight(age, conn)
            if int(s["imputed"]) == 1 or s["detail_level"] == "total_only":
                w *= 0.5
            if s["ratio_cfo_m2_actualise"] is not None:
                buckets[("CFO", "EUR_M2")].append((float(s["ratio_cfo_m2_actualise"]), w))
            if s["ratio_cfa_m2_actualise"] is not None:
                buckets[("CFA", "EUR_M2")].append((float(s["ratio_cfa_m2_actualise"]), w))
            if s["ratio_pv_kwc_actualise"] is not None:
                buckets[("PV", "EUR_KWC")].append((float(s["ratio_pv_kwc_actualise"]), w))

        for (lot, unit), pairs in buckets.items():
            if not pairs:
                conn.execute(
                    """
                    DELETE FROM ratio_building_type_aggregates
                    WHERE category_name = ? AND lot = ? AND unit = ? AND annee_reference = ?
                    """,
                    (cat, lot, unit, ref_year),
                )
                continue
            somme_p = sum(v * w for v, w in pairs)
            somme_w = sum(w for _, w in pairs)
            avg = somme_p / somme_w if somme_w > 0 else 0.0
            nb = len(pairs)
            has_imputed = any(
                int(s["imputed"]) == 1
                for s in sources
                if (
                    (lot == "CFO" and s["ratio_cfo_m2_actualise"] is not None)
                    or (lot == "CFA" and s["ratio_cfa_m2_actualise"] is not None)
                    or (lot == "PV" and s["ratio_pv_kwc_actualise"] is not None)
                )
            )
            fiab = _fiabilite(nb)
            if has_imputed and fiab == "OK":
                fiab = "IMPUTE"
            conn.execute(
                """
                INSERT INTO ratio_building_type_aggregates (
                    category_name, lot, unit, ratio_actualise,
                    nb_sources, fiabilite, annee_reference, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(category_name, lot, unit, annee_reference) DO UPDATE SET
                    ratio_actualise = excluded.ratio_actualise,
                    nb_sources = excluded.nb_sources,
                    fiabilite = excluded.fiabilite,
                    computed_at = CURRENT_TIMESTAMP
                """,
                (cat, lot, unit, round(avg, 4), nb, fiab, ref_year),
            )

    if own:
        conn.commit()
        conn.close()


TAUX = db_ratios.TAUX_ACTUALISATION_ANNUEL

# Typologies sans stats dédiées → référentiel le plus proche (devis réels)
TYPOLOGY_RATIO_ALIASES = {
    "EHPAD": "Hôpital",
}

# Exclues de la moyenne « Autres » (profils Hôpitaux / Industriel)
AUTRES_AVERAGE_EXCLUDED = frozenset({"Hôpital", "Industrie"})

_FIAB_WORST_RANK = {
    "OK": 0,
    "IMPUTE": 1,
    "PRUDENCE": 2,
    "SOURCE_UNIQUE": 3,
    "AUCUNE_REF": 4,
}


def _weighted_lot_average(
    pairs: list[tuple[float, int, str]],
) -> tuple[float | None, int, str]:
    """Moyenne pondérée par nb_sources ; fiabilité = pire niveau rencontré."""
    if not pairs:
        return None, 0, "AUCUNE_REF"
    den = sum(n for _, n, _ in pairs)
    if den <= 0:
        return None, 0, "AUCUNE_REF"
    avg = sum(v * n for v, n, _ in pairs) / den
    nb = sum(n for _, n, _ in pairs)
    fiab = max((f for _, _, f in pairs), key=lambda f: _FIAB_WORST_RANK.get(f, 99))
    return round(avg, 4), nb, fiab


def get_autres_typology_average() -> dict | None:
    """
    Moyenne pondérée (nb_sources) des agrégats stats, hors Hôpital et Industrie.
    Repli réaliste pour typologies profil « Autres » sans stats dédiées.
    """
    conn = db_ratios.connect()
    try:
        ref_year = db_ratios.get_annee_reference(conn)
        rows = conn.execute(
            """
            SELECT category_name, lot, unit, ratio_actualise, nb_sources, fiabilite
            FROM ratio_building_type_aggregates
            WHERE annee_reference = ?
            """,
            (ref_year,),
        ).fetchall()
        autres_rows = [
            r for r in rows if r["category_name"] not in AUTRES_AVERAGE_EXCLUDED
        ]
        if not autres_rows:
            return None

        buckets: dict[str, list[tuple[float, int, str]]] = {
            "CFO": [],
            "CFA": [],
            "PV": [],
        }
        for r in autres_rows:
            lot = r["lot"]
            if lot not in buckets:
                continue
            buckets[lot].append(
                (float(r["ratio_actualise"]), int(r["nb_sources"]), r["fiabilite"])
            )

        cfo, ncfo, fcfo = _weighted_lot_average(buckets["CFO"])
        cfa, ncfa, fcfa = _weighted_lot_average(buckets["CFA"])
        pv, npv, fpv = _weighted_lot_average(buckets["PV"])

        if cfo is None and cfa is None and pv is None:
            return None

        typo_names = sorted({r["category_name"] for r in autres_rows})
        return {
            "category_name": "Moyenne typologies Autres",
            "annee_reference": ref_year,
            "ratio_m2_cfo": cfo,
            "ratio_m2_cfa": cfa,
            "ratio_kwc_pv": pv,
            "nb_sources_cfo": ncfo,
            "nb_sources_cfa": ncfa,
            "nb_sources_pv": npv,
            "fiabilite_cfo": fcfo,
            "fiabilite_cfa": fcfa,
            "fiabilite_pv": fpv,
            "nb_typologies": len(typo_names),
            "typologies_incluses": typo_names,
            "ratio_source_kind": "moyenne_autres",
        }
    finally:
        conn.close()


def resolve_category_ratios(category_name: str | None) -> tuple[dict | None, str]:
    """
    Résout les ratios pour une typologie.
    Retourne (dict ratios, source) avec source ∈ typologie | moyenne_autres | none.
    """
    if not category_name or not str(category_name).strip():
        return None, "none"
    direct = get_typology_ratios(category_name)
    if direct:
        return direct, "typologie"

    import db_profiles

    if db_profiles.profile_for_category_name(category_name.strip()) == db_profiles.DEFAULT_PROFILE:
        avg = get_autres_typology_average()
        if avg:
            out = dict(avg)
            out["category_name"] = category_name.strip()
            return out, "moyenne_autres"
    return None, "none"


def get_typology_ratios(category_name: str | None) -> dict | None:
    """Ratios agrégés CFO/CFA/PV pour une typologie (fiche affaire)."""
    if not category_name or not str(category_name).strip():
        return None
    name = category_name.strip()
    lookup = TYPOLOGY_RATIO_ALIASES.get(name, name)
    result = _fetch_typology_ratios(lookup)
    if result is None:
        return None
    if lookup != name:
        result = dict(result)
        result["category_name"] = name
        result["ratio_source_typology"] = lookup
    return result


def _fetch_typology_ratios(category_name: str) -> dict | None:
    conn = db_ratios.connect()
    try:
        ref_year = db_ratios.get_annee_reference(conn)
        rows = conn.execute(
            """
            SELECT lot, unit, ratio_actualise, nb_sources, fiabilite
            FROM ratio_building_type_aggregates
            WHERE category_name = ? AND annee_reference = ?
            """,
            (category_name.strip(), ref_year),
        ).fetchall()
        if not rows:
            return None
        out = {
            "category_name": category_name.strip(),
            "annee_reference": ref_year,
            "ratio_m2_cfo": None,
            "ratio_m2_cfa": None,
            "ratio_kwc_pv": None,
            "nb_sources_cfo": 0,
            "nb_sources_cfa": 0,
            "nb_sources_pv": 0,
            "fiabilite_cfo": "AUCUNE_REF",
            "fiabilite_cfa": "AUCUNE_REF",
            "fiabilite_pv": "AUCUNE_REF",
        }
        for r in rows:
            if r["lot"] == "CFO" and r["unit"] == "EUR_M2":
                out["ratio_m2_cfo"] = float(r["ratio_actualise"])
                out["nb_sources_cfo"] = int(r["nb_sources"])
                out["fiabilite_cfo"] = r["fiabilite"]
            elif r["lot"] == "CFA" and r["unit"] == "EUR_M2":
                out["ratio_m2_cfa"] = float(r["ratio_actualise"])
                out["nb_sources_cfa"] = int(r["nb_sources"])
                out["fiabilite_cfa"] = r["fiabilite"]
            elif r["lot"] == "PV" and r["unit"] == "EUR_KWC":
                out["ratio_kwc_pv"] = float(r["ratio_actualise"])
                out["nb_sources_pv"] = int(r["nb_sources"])
                out["fiabilite_pv"] = r["fiabilite"]
        if not any(out.get(k) for k in ("ratio_m2_cfo", "ratio_m2_cfa", "ratio_kwc_pv")):
            return None
        return out
    finally:
        conn.close()


def list_sources(limit: int = 500) -> list[dict]:
    conn = db_ratios.connect()
    try:
        rows = conn.execute(
            """
            SELECT s.*,
                   c.ratio_cfo_m2_actualise, c.ratio_cfa_m2_actualise,
                   c.ratio_pv_kwc_actualise, c.ratio_total_m2_actualise
            FROM ratio_devis_sources s
            LEFT JOIN ratio_source_computed c ON c.source_id = s.id
            WHERE s.is_active = 1
            ORDER BY s.devis_date DESC, s.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_aggregates() -> list[dict]:
    conn = db_ratios.connect()
    try:
        ref_year = db_ratios.get_annee_reference(conn)
        rows = conn.execute(
            """
            SELECT * FROM ratio_building_type_aggregates
            WHERE annee_reference = ?
            ORDER BY category_name, lot
            """,
            (ref_year,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def category_name_from_id(category_id, profile: str | None = None) -> str | None:
    if not category_id:
        return None
    import models

    conn = models.get_db(profile)
    try:
        row = conn.execute(
            "SELECT name FROM building_categories WHERE id = ?",
            (int(category_id),),
        ).fetchone()
        return row["name"] if row else None
    finally:
        conn.close()
