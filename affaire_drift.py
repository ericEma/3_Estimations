"""
Dérives ±3 % vs dernière baseline remise — détection, historique, justifications.

Compare fiche vs fiche et estimation vs estimation (baseline scope homogène).
Pas de contrôle ±3 % entre deux re-validations internes (superseded) même phase.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import affaire_baselines as bl
from models import (
    compute_estimation_kpis,
    find_affaire_profile,
    get_affaire,
    get_db,
    get_estimation_catalog_rows,
    get_estimation_section_state,
    normalize_profile,
)

DRIFT_THRESHOLD = 0.03
SCOPE_FICHE = bl.SCOPE_FICHE
SCOPE_ESTIMATION = bl.SCOPE_ESTIMATION

TRIGGER_SAVE = "save_drift"
TRIGGER_PHASE = "phase_change"
TRIGGER_VERSION = "version_change"

STATUS_PENDING = "pending"
STATUS_JUSTIFIED = "justified"


def ensure_drift_tables(conn: sqlite3.Connection) -> None:
    bl.ensure_baseline_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS affaire_change_events (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            affaire_id              INTEGER NOT NULL,
            scope                   TEXT NOT NULL
                CHECK (scope IN ('fiche', 'estimation')),
            trigger_type            TEXT NOT NULL DEFAULT 'save_drift',
            reference_baseline_id   INTEGER
                REFERENCES affaire_client_baselines(id),
            old_phase               TEXT,
            new_phase               TEXT,
            old_total_ht            REAL,
            new_total_ht            REAL,
            drift_pct               REAL,
            global_justification    TEXT,
            status                  TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'justified')),
            created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
            justified_at            TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS affaire_change_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id        INTEGER NOT NULL
                REFERENCES affaire_change_events(id) ON DELETE CASCADE,
            item_type       TEXT NOT NULL,
            item_key        TEXT,
            label           TEXT NOT NULL,
            old_value       REAL,
            new_value       REAL,
            drift_pct       REAL,
            justification   TEXT,
            sort_order      INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_change_evt_affaire_scope
        ON affaire_change_events(affaire_id, scope, status)
        """
    )


def pct_drift(old_val: float | None, new_val: float | None) -> float:
    """Écart relatif (0.05 = +5 %)."""
    old_f = float(old_val or 0)
    new_f = float(new_val or 0)
    if abs(old_f) < 0.005:
        return 0.0 if abs(new_f) < 0.005 else 1.0
    return (new_f - old_f) / abs(old_f)


def exceeds_drift_threshold(drift_ratio: float) -> bool:
    return abs(drift_ratio) > DRIFT_THRESHOLD


def get_remise_reference_baseline(
    affaire_id: int,
    scope: str,
    profile: str | None = None,
) -> dict | None:
    """Dernière baseline remise — référence comparaison ±3 %."""
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        return None
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_drift_tables(conn)
        row = conn.execute(
            """
            SELECT * FROM affaire_client_baselines
            WHERE affaire_id = ? AND scope = ? AND status = ?
            ORDER BY validated_at DESC, id DESC
            LIMIT 1
            """,
            (affaire_id, scope, bl.STATUS_REMISE),
        ).fetchone()
        return bl._row_to_dict(row) if row else None
    finally:
        conn.close()


def build_estimation_detail_snapshot(
    affaire_id: int,
    profile: str | None = None,
) -> dict[str, Any]:
    """Instantané détaillé (totaux lot / chapitre / section / lignes)."""
    affaire = get_affaire(affaire_id, profile=profile) or {}
    totals = compute_estimation_kpis(affaire_id, profile)
    sections: dict[str, float] = {}
    chapters: dict[str, float] = {}
    lines: list[dict] = []

    for row in get_estimation_catalog_rows(affaire_id):
        qty = float(row.get("quantity") or 0)
        pu = row.get("unit_price_ht")
        ref_pu = float(row.get("ref_pu_ht") or row.get("ratio_ref") or 0)
        pu_eff = float(pu) if pu is not None else ref_pu
        line_tot = round(qty * pu_eff, 2)
        if line_tot <= 0 and qty <= 0:
            continue
        ch = (row.get("chapter") or "").strip()
        sec = (row.get("section") or "").strip()
        sk = f"{ch}|{sec}"
        sections[sk] = round(sections.get(sk, 0) + line_tot, 2)
        chapters[ch] = round(chapters.get(ch, 0) + line_tot, 2)
        key = f"dpgf:{row.get('dpgf_id')}"
        if row.get("line_id"):
            key = f"line:{row['line_id']}"
        lines.append({
            "key": key,
            "designation": (row.get("designation") or "").strip(),
            "total_ht": line_tot,
        })

    for sec_row in get_estimation_section_state(affaire_id):
        if not sec_row.get("is_included") or not sec_row.get("use_macro"):
            continue
        ch = sec_row.get("chapter") or ""
        sec = sec_row.get("section") or ""
        ratio = float(sec_row.get("ratio_m2_override") or 0)
        divisor = float(sec_row.get("qty") or 0)
        if ratio <= 0 or divisor <= 0:
            continue
        macro_tot = round(ratio * divisor, 2)
        sk = f"{ch}|{sec}"
        sections[sk] = round(sections.get(sk, 0) + macro_tot, 2)
        chapters[ch] = round(chapters.get(ch, 0) + macro_tot, 2)
        lines.append({
            "key": f"macro:{sk}",
            "designation": f"[Macro] {sec}",
            "total_ht": macro_tot,
        })

    return {
        "totals": totals,
        "sections": sections,
        "chapters": chapters,
        "lines": lines,
        "params": {
            "surface_sdo": float(affaire.get("surface_sdo") or 0),
            "puissance_pv_kwc": float(affaire.get("puissance_pv_kwc") or 0),
            "taux_phase": float(affaire.get("taux_phase") or 0),
            "taux_incertitude": float(affaire.get("taux_incertitude") or 0),
            "coef_risque": float(affaire.get("coef_risque") or 0),
            "phase_etude": affaire.get("phase_etude"),
        },
    }


def _changed_item(
    item_type: str,
    item_key: str,
    label: str,
    old_val: float | None,
    new_val: float | None,
    sort_order: int,
) -> dict | None:
    old_f = float(old_val or 0)
    new_f = float(new_val or 0)
    if abs(old_f - new_f) < 0.5:
        return None
    return {
        "item_type": item_type,
        "item_key": item_key,
        "label": label,
        "old_value": old_f,
        "new_value": new_f,
        "drift_pct": round(pct_drift(old_f, new_f) * 100, 2),
        "sort_order": sort_order,
    }


def compare_fiche_to_reference(
    reference: dict,
    fiche_fields: dict,
    preview: dict,
) -> tuple[float, list[dict]]:
    ref_payload = reference.get("payload") or {}
    ref_preview = ref_payload.get("preview") or {}
    old_total = float(reference.get("total_ht") or ref_preview.get("prix_total") or 0)
    new_total = float(preview.get("prix_total") or 0)
    total_drift = pct_drift(old_total, new_total)

    items: list[dict] = []
    order = 0

    def add(item):
        nonlocal order
        if item:
            item["sort_order"] = order
            order += 1
            items.append(item)

    add(_changed_item("total", "total", "Total HT sommaire", old_total, new_total, 0))
    for lot_key, lbl in (
        ("cfo", "Total CFO"),
        ("cfa", "Total CFA"),
        ("pv", "Total PV"),
    ):
        pk = f"prix_{lot_key}"
        add(_changed_item(
            f"lot_{lot_key}",
            pk,
            lbl,
            ref_preview.get(pk),
            preview.get(pk),
            order,
        ))

    lever_fields = [
        ("lever_sdo", "surface_sdo", "SDO (m²)"),
        ("lever_ratio_cfo", "ratio_global_cfo_m2", "Ratio global CFO €/m²"),
        ("lever_ratio_cfa", "ratio_global_cfa_m2", "Ratio global CFA €/m²"),
        ("lever_ratio_pv", "ratio_global_pv_kwc", "Ratio global PV €/kWc"),
        ("lever_phase", "taux_phase", "Taux phase (%)"),
        ("lever_incertitude", "taux_incertitude", "Taux incertitude (%)"),
        ("lever_risque", "coef_risque", "Taux risque (%)"),
    ]
    for itype, field, label in lever_fields:
        add(_changed_item(
            itype,
            field,
            label,
            ref_payload.get(field),
            fiche_fields.get(field),
            order,
        ))

    changed = [i for i in items if i.get("item_key") != "total"]
    if not exceeds_drift_threshold(total_drift):
        return total_drift, []

    if not changed:
        return total_drift, [i for i in items if i.get("item_key") == "total"]

    return total_drift, items


def compare_estimation_to_reference(
    reference: dict,
    snapshot: dict,
) -> tuple[float, list[dict]]:
    ref_payload = reference.get("payload") or {}
    ref_totals = ref_payload.get("totals") or {}
    cur_totals = snapshot.get("totals") or {}
    old_total = float(reference.get("total_ht") or ref_totals.get("ALL") or 0)
    new_total = float(cur_totals.get("ALL") or 0)
    total_drift = pct_drift(old_total, new_total)

    if not exceeds_drift_threshold(total_drift):
        return total_drift, []

    items: list[dict] = []
    order = 0

    def add(item):
        nonlocal order
        if item:
            item["sort_order"] = order
            order += 1
            items.append(item)

    add(_changed_item("total", "total", "Total HT estimation", old_total, new_total, 0))
    for lot in ("CFO", "CFA", "PV"):
        add(_changed_item(
            f"lot_{lot.lower()}",
            lot,
            f"Total lot {lot}",
            ref_totals.get(lot),
            cur_totals.get(lot),
            order,
        ))

    ref_sections = ref_payload.get("sections") or {}
    cur_sections = snapshot.get("sections") or {}
    for sk in sorted(set(ref_sections) | set(cur_sections)):
        ch, _, sec = sk.partition("|")
        add(_changed_item(
            "section",
            sk,
            f"Section {sec or sk}",
            ref_sections.get(sk),
            cur_sections.get(sk),
            order,
        ))

    ref_lines = {ln["key"]: ln for ln in (ref_payload.get("lines") or []) if ln.get("key")}
    cur_lines = {ln["key"]: ln for ln in (snapshot.get("lines") or []) if ln.get("key")}
    for key in sorted(set(ref_lines) | set(cur_lines)):
        old_ln = ref_lines.get(key, {})
        new_ln = cur_lines.get(key, {})
        label = new_ln.get("designation") or old_ln.get("designation") or key
        add(_changed_item(
            "article",
            key,
            label,
            old_ln.get("total_ht"),
            new_ln.get("total_ht"),
            order,
        ))

    ref_params = ref_payload.get("params") or ref_payload
    cur_params = snapshot.get("params") or {}
    for itype, field, label in (
        ("lever_sdo", "surface_sdo", "SDO (m²)"),
        ("lever_phase", "taux_phase", "Taux phase (%)"),
        ("lever_incertitude", "taux_incertitude", "Taux incertitude (%)"),
        ("lever_risque", "coef_risque", "Taux risque (%)"),
    ):
        add(_changed_item(
            itype,
            field,
            label,
            ref_params.get(field),
            cur_params.get(field),
            order,
        ))

    if not items:
        items.append({
            "item_type": "total",
            "item_key": "total",
            "label": "Total HT estimation",
            "old_value": old_total,
            "new_value": new_total,
            "drift_pct": round(total_drift * 100, 2),
            "sort_order": 0,
        })
    return total_drift, items


def get_pending_event(
    affaire_id: int,
    scope: str,
    profile: str | None = None,
) -> dict | None:
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        return None
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_drift_tables(conn)
        row = conn.execute(
            """
            SELECT * FROM affaire_change_events
            WHERE affaire_id = ? AND scope = ? AND status = ?
            ORDER BY id DESC LIMIT 1
            """,
            (affaire_id, scope, STATUS_PENDING),
        ).fetchone()
        if not row:
            return None
        event = dict(row)
        items = conn.execute(
            """
            SELECT * FROM affaire_change_items
            WHERE event_id = ?
            ORDER BY sort_order, id
            """,
            (event["id"],),
        ).fetchall()
        event["items"] = [dict(i) for i in items]
        return event
    finally:
        conn.close()


def _delete_pending_events(conn, affaire_id: int, scope: str) -> None:
    conn.execute(
        """
        DELETE FROM affaire_change_events
        WHERE affaire_id = ? AND scope = ? AND status = ?
        """,
        (affaire_id, scope, STATUS_PENDING),
    )


def create_drift_event(
    affaire_id: int,
    scope: str,
    profile: str | None,
    trigger_type: str,
    reference: dict,
    items: list[dict],
    new_total: float,
    total_drift: float,
    old_phase: str | None = None,
    new_phase: str | None = None,
) -> dict:
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        raise ValueError("Affaire introuvable")
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_drift_tables(conn)
        _delete_pending_events(conn, affaire_id, scope)
        old_total = float(reference.get("total_ht") or 0)
        cur = conn.execute(
            """
            INSERT INTO affaire_change_events (
                affaire_id, scope, trigger_type, reference_baseline_id,
                old_phase, new_phase, old_total_ht, new_total_ht, drift_pct, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                affaire_id,
                scope,
                trigger_type,
                reference.get("id"),
                old_phase or reference.get("phase_etude"),
                new_phase,
                old_total,
                new_total,
                round(total_drift * 100, 2),
                STATUS_PENDING,
            ),
        )
        event_id = int(cur.lastrowid)
        for it in items:
            conn.execute(
                """
                INSERT INTO affaire_change_items (
                    event_id, item_type, item_key, label,
                    old_value, new_value, drift_pct, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    it.get("item_type"),
                    it.get("item_key"),
                    it.get("label"),
                    it.get("old_value"),
                    it.get("new_value"),
                    it.get("drift_pct"),
                    it.get("sort_order", 0),
                ),
            )
        conn.commit()
        return get_event_by_id(event_id, prof) or {}
    finally:
        conn.close()


def get_event_by_id(event_id: int, profile: str | None = None) -> dict | None:
    prof = normalize_profile(profile) if profile else None
    if not prof:
        return None
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_drift_tables(conn)
        row = conn.execute(
            "SELECT * FROM affaire_change_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            return None
        event = dict(row)
        items = conn.execute(
            """
            SELECT * FROM affaire_change_items WHERE event_id = ?
            ORDER BY sort_order, id
            """,
            (event_id,),
        ).fetchall()
        event["items"] = [dict(i) for i in items]
        return event
    finally:
        conn.close()


def save_justifications(
    event_id: int,
    items: list[dict],
    global_justification: str | None,
    profile: str | None = None,
) -> dict:
    prof = normalize_profile(profile) if profile else find_affaire_profile_by_event(event_id)
    if not prof:
        raise ValueError("Événement introuvable")
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_drift_tables(conn)
        event = conn.execute(
            "SELECT * FROM affaire_change_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not event:
            raise ValueError("Événement introuvable")
        if event["status"] != STATUS_PENDING:
            raise ValueError("Événement déjà traité")

        by_id = {int(it["id"]): it for it in items if it.get("id") is not None}
        rows = conn.execute(
            "SELECT id, label FROM affaire_change_items WHERE event_id = ?",
            (event_id,),
        ).fetchall()
        missing = []
        for r in rows:
            rid = int(r["id"])
            itype = conn.execute(
                "SELECT item_type FROM affaire_change_items WHERE id = ?", (rid,)
            ).fetchone()
            if itype and str(itype["item_type"]).startswith("lever_"):
                continue
            text = (by_id.get(rid) or {}).get("justification") or ""
            text = str(text).strip()
            if not text:
                missing.append(r["label"])
                continue
            conn.execute(
                "UPDATE affaire_change_items SET justification = ? WHERE id = ?",
                (text, rid),
            )
        glob = (global_justification or "").strip()
        lever_items = conn.execute(
            """
            SELECT id FROM affaire_change_items
            WHERE event_id = ? AND item_type LIKE 'lever_%'
            """,
            (event_id,),
        ).fetchall()
        if lever_items and not glob:
            missing.append("Justification globale (leviers hors détail)")

        if missing:
            raise ValueError(
                "Justifications manquantes : " + ", ".join(missing[:5])
                + ("…" if len(missing) > 5 else "")
            )

        conn.execute(
            """
            UPDATE affaire_change_events
            SET status = ?, global_justification = ?, justified_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (STATUS_JUSTIFIED, glob or None, event_id),
        )
        conn.commit()
        return get_event_by_id(event_id, prof) or {}
    finally:
        conn.close()


def find_affaire_profile_by_event(event_id: int) -> str | None:
    for prof in ("hopitaux", "industriel", "autres"):
        conn = get_db(prof)
        try:
            row = conn.execute(
                "SELECT affaire_id FROM affaire_change_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            if row:
                return prof
        finally:
            conn.close()
    return None


def check_fiche_drift(
    affaire_id: int,
    fiche_fields: dict,
    preview: dict,
    profile: str | None = None,
    trigger_type: str = TRIGGER_SAVE,
) -> dict:
    reference = get_remise_reference_baseline(affaire_id, SCOPE_FICHE, profile)
    if not reference:
        pending = get_pending_event(affaire_id, SCOPE_FICHE, profile)
        return {
            "requires_justification": False,
            "has_reference": False,
            "pending_event": pending,
        }

    total_drift, items = compare_fiche_to_reference(reference, fiche_fields, preview)
    pending = get_pending_event(affaire_id, SCOPE_FICHE, profile)
    if not exceeds_drift_threshold(total_drift):
        return {
            "requires_justification": False,
            "has_reference": True,
            "drift_pct": round(total_drift * 100, 2),
            "pending_event": pending,
        }

    affaire = get_affaire(affaire_id, profile=profile) or {}
    event = create_drift_event(
        affaire_id,
        SCOPE_FICHE,
        profile,
        trigger_type,
        reference,
        items,
        float(preview.get("prix_total") or 0),
        total_drift,
        old_phase=reference.get("phase_etude"),
        new_phase=affaire.get("phase_etude"),
    )
    return {
        "requires_justification": True,
        "has_reference": True,
        "drift_pct": round(total_drift * 100, 2),
        "event": event,
        "pending_event": event,
    }


def check_estimation_drift(
    affaire_id: int,
    profile: str | None = None,
    trigger_type: str = TRIGGER_SAVE,
) -> dict:
    reference = get_remise_reference_baseline(affaire_id, SCOPE_ESTIMATION, profile)
    snapshot = build_estimation_detail_snapshot(affaire_id, profile)
    pending = get_pending_event(affaire_id, SCOPE_ESTIMATION, profile)

    if not reference:
        return {
            "requires_justification": False,
            "has_reference": False,
            "pending_event": pending,
            "totals": snapshot.get("totals"),
        }

    total_drift, items = compare_estimation_to_reference(reference, snapshot)
    if not exceeds_drift_threshold(total_drift):
        return {
            "requires_justification": False,
            "has_reference": True,
            "drift_pct": round(total_drift * 100, 2),
            "pending_event": pending,
            "totals": snapshot.get("totals"),
        }

    affaire = get_affaire(affaire_id, profile=profile) or {}
    event = create_drift_event(
        affaire_id,
        SCOPE_ESTIMATION,
        profile,
        trigger_type,
        reference,
        items,
        float((snapshot.get("totals") or {}).get("ALL") or 0),
        total_drift,
        old_phase=reference.get("phase_etude"),
        new_phase=affaire.get("phase_etude"),
    )
    return {
        "requires_justification": True,
        "has_reference": True,
        "drift_pct": round(total_drift * 100, 2),
        "event": event,
        "pending_event": event,
        "totals": snapshot.get("totals"),
    }


def get_justifications_for_export(
    affaire_id: int,
    profile: str | None = None,
) -> dict[str, str]:
    """Map designation (ou clé section) → texte justification (dernier event justifié)."""
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        return {}
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_drift_tables(conn)
        row = conn.execute(
            """
            SELECT id FROM affaire_change_events
            WHERE affaire_id = ? AND scope = ? AND status = ?
            ORDER BY justified_at DESC, id DESC LIMIT 1
            """,
            (affaire_id, SCOPE_ESTIMATION, STATUS_JUSTIFIED),
        ).fetchone()
        if not row:
            return {}
        items = conn.execute(
            """
            SELECT label, item_key, justification FROM affaire_change_items
            WHERE event_id = ? AND justification IS NOT NULL AND TRIM(justification) != ''
            """,
            (int(row["id"]),),
        ).fetchall()
        out: dict[str, str] = {}
        for it in items:
            out[it["label"]] = it["justification"]
            if it["item_key"]:
                out[it["item_key"]] = it["justification"]
        return out
    finally:
        conn.close()


def get_latest_synthesis_rows(
    affaire_id: int,
    profile: str | None = None,
) -> list[dict]:
    """Lignes pour feuille synthèse Excel."""
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        return []
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_drift_tables(conn)
        ev = conn.execute(
            """
            SELECT * FROM affaire_change_events
            WHERE affaire_id = ? AND scope = ? AND status = ?
            ORDER BY justified_at DESC, id DESC LIMIT 1
            """,
            (affaire_id, SCOPE_ESTIMATION, STATUS_JUSTIFIED),
        ).fetchone()
        if not ev:
            return []
        items = conn.execute(
            """
            SELECT label, old_value, new_value, drift_pct, justification
            FROM affaire_change_items WHERE event_id = ?
            ORDER BY sort_order, id
            """,
            (int(ev["id"]),),
        ).fetchall()
        rows = [dict(i) for i in items]
        if ev["global_justification"]:
            rows.append({
                "label": "Justification globale (leviers)",
                "old_value": None,
                "new_value": None,
                "drift_pct": ev["drift_pct"],
                "justification": ev["global_justification"],
            })
        return rows
    finally:
        conn.close()
