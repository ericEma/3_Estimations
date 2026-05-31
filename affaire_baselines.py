"""
Snapshots « carte client » et estimation — validation, correction, historique.

Scope fiche : ratios + preview sommaire (DIAG / APS / concours).
Scope estimation : réservé sprint suivant.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

import db_profiles
from models import find_affaire_profile, get_affaire, get_db, normalize_profile


SCOPE_FICHE = "fiche"
SCOPE_ESTIMATION = "estimation"

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_REMISE = "remise"


def _migrate_baselines_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS affaire_client_baselines (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            affaire_id          INTEGER NOT NULL REFERENCES affaires(id) ON DELETE CASCADE,
            scope               TEXT NOT NULL DEFAULT 'fiche'
                CHECK (scope IN ('fiche', 'estimation')),
            phase_etude         TEXT NOT NULL,
            version_num         INTEGER NOT NULL DEFAULT 1,
            status              TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'superseded', 'remise')),
            total_ht            REAL,
            prix_cfo            REAL,
            prix_cfa            REAL,
            prix_pv             REAL,
            payload_json        TEXT NOT NULL,
            label               TEXT,
            validated_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            superseded_at       TEXT,
            superseded_by_id    INTEGER REFERENCES affaire_client_baselines(id),
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_baseline_affaire_scope
        ON affaire_client_baselines(affaire_id, scope, status)
        """
    )


def ensure_baseline_tables(conn: sqlite3.Connection) -> None:
    _migrate_baselines_table(conn)


def build_fiche_payload(fiche_fields: dict, preview: dict) -> dict:
    """Instantané JSON de la carte sommaire."""
    return {
        "name": fiche_fields.get("name"),
        "category_id": fiche_fields.get("category_id"),
        "surface_sdo": float(fiche_fields.get("surface_sdo") or 0),
        "kva_cible": float(fiche_fields.get("kva_cible") or 0),
        "puissance_pv_kwc": float(fiche_fields.get("puissance_pv_kwc") or 0),
        "ratio_global_cfo_m2": fiche_fields.get("ratio_global_cfo_m2"),
        "ratio_global_cfa_m2": fiche_fields.get("ratio_global_cfa_m2"),
        "ratio_global_pv_kwc": fiche_fields.get("ratio_global_pv_kwc"),
        "coef_complexity_cfo": float(fiche_fields.get("coef_complexity_cfo") or 1),
        "coef_complexity_cfa": float(fiche_fields.get("coef_complexity_cfa") or 1),
        "pv_system_type": fiche_fields.get("pv_system_type"),
        "phase_etude": fiche_fields.get("phase_etude"),
        "taux_phase": float(fiche_fields.get("taux_phase") or 0),
        "taux_incertitude": float(fiche_fields.get("taux_incertitude") or 0),
        "coef_risque": float(fiche_fields.get("coef_risque") or 0),
        "preview": {
            "prix_cfo": preview.get("prix_cfo"),
            "prix_cfa": preview.get("prix_cfa"),
            "prix_pv": preview.get("prix_pv"),
            "prix_total": preview.get("prix_total"),
            "ratio_m2_cfo": preview.get("ratio_m2_cfo"),
            "ratio_m2_cfa": preview.get("ratio_m2_cfa"),
            "ratio_kwc_pv": preview.get("ratio_kwc_pv"),
        },
    }


def _next_version_num(
    conn: sqlite3.Connection,
    affaire_id: int,
    scope: str,
    phase: str,
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(version_num), 0) + 1 AS n
        FROM affaire_client_baselines
        WHERE affaire_id = ? AND scope = ? AND phase_etude = ?
        """,
        (affaire_id, scope, phase),
    ).fetchone()
    return int(row["n"] if row else 1)


def validate_fiche_baseline(
    affaire_id: int,
    profile: str | None,
    fiche_fields: dict,
    preview: dict,
    label: str | None = None,
) -> dict:
    """
    Valide (ou re-valide) la carte client scope fiche.
    Remplace la baseline active de la phase courante (ancienne → superseded).
    """
    return _validate_baseline(
        affaire_id,
        profile,
        SCOPE_FICHE,
        fiche_fields,
        preview,
        label=label,
    )


def build_estimation_payload(
    affaire: dict,
    totals: dict,
    detail_snapshot: dict | None = None,
) -> dict:
    """Instantané JSON de l'estimation détaillée (totaux par lot)."""
    payload = {
        "surface_sdo": float(affaire.get("surface_sdo") or 0),
        "puissance_pv_kwc": float(affaire.get("puissance_pv_kwc") or 0),
        "phase_etude": affaire.get("phase_etude"),
        "taux_phase": float(affaire.get("taux_phase") or 0),
        "taux_incertitude": float(affaire.get("taux_incertitude") or 0),
        "coef_risque": float(affaire.get("coef_risque") or 0),
        "coef_complexity_cfo": float(affaire.get("coef_complexity_cfo") or 1),
        "coef_complexity_cfa": float(affaire.get("coef_complexity_cfa") or 1),
        "coef_complexity_pv": float(affaire.get("coef_complexity_pv") or 1),
        "totals": {
            "CFO": totals.get("CFO"),
            "CFA": totals.get("CFA"),
            "PV": totals.get("PV"),
            "ALL": totals.get("ALL"),
        },
    }
    if detail_snapshot:
        payload["sections"] = detail_snapshot.get("sections") or {}
        payload["chapters"] = detail_snapshot.get("chapters") or {}
        payload["lines"] = detail_snapshot.get("lines") or []
        payload["params"] = detail_snapshot.get("params") or {}
    return payload


def validate_estimation_baseline(
    affaire_id: int,
    profile: str | None,
    affaire: dict,
    totals: dict,
    label: str | None = None,
) -> dict:
    """Valide (ou re-valide) l'estimation détaillée scope estimation."""
    from affaire_drift import build_estimation_detail_snapshot

    phase = (affaire.get("phase_etude") or "APD").strip().upper()
    detail = build_estimation_detail_snapshot(affaire_id, profile)
    preview = {
        "prix_cfo": totals.get("CFO"),
        "prix_cfa": totals.get("CFA"),
        "prix_pv": totals.get("PV"),
        "prix_total": totals.get("ALL"),
    }
    fields = dict(affaire)
    fields["phase_etude"] = phase
    return _validate_baseline(
        affaire_id,
        profile,
        SCOPE_ESTIMATION,
        fields,
        preview,
        label=label or f"Validation estimation {phase}",
        payload_builder=lambda f, p: build_estimation_payload(f, totals, detail),
    )


def start_new_estimation_version(
    affaire_id: int,
    profile: str | None = None,
) -> dict:
    """
    Nouvelle version même phase (ex. APD v2) : baseline active → remise.
    Les lignes en base restent inchangées (copie implicite pour édition v2).
    """
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        raise ValueError("Affaire introuvable")
    affaire = get_affaire(affaire_id, profile=prof)
    if not affaire:
        raise ValueError("Affaire introuvable")
    phase = (affaire.get("phase_etude") or "APD").strip().upper()
    active = get_active_baseline(affaire_id, SCOPE_ESTIMATION, prof)
    if not active:
        raise ValueError(f"Aucune estimation validée pour la phase {phase}")
    if active.get("phase_etude") != phase:
        raise ValueError("Phase courante incompatible avec la baseline active")
    n = mark_phase_baselines_remise(affaire_id, phase, SCOPE_ESTIMATION, prof)
    if n < 1:
        raise ValueError("Impossible de clôturer la version courante")
    next_v = int(active.get("version_num") or 1) + 1
    return {
        "ok": True,
        "phase_etude": phase,
        "previous_version": int(active.get("version_num") or 1),
        "next_version": next_v,
    }


def _validate_baseline(
    affaire_id: int,
    profile: str | None,
    scope: str,
    fiche_fields: dict,
    preview: dict,
    label: str | None = None,
    payload_builder=None,
) -> dict:
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        raise ValueError("Affaire introuvable")

    phase = (fiche_fields.get("phase_etude") or "APD").strip().upper()
    if payload_builder:
        payload = payload_builder(fiche_fields, preview)
    elif scope == SCOPE_FICHE:
        payload = build_fiche_payload(fiche_fields, preview)
    else:
        payload = build_estimation_payload(fiche_fields, preview.get("totals") or preview)
    total = float(preview.get("prix_total") or preview.get("ALL") or 0)

    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_baseline_tables(conn)
        prev = conn.execute(
            """
            SELECT id FROM affaire_client_baselines
            WHERE affaire_id = ? AND scope = ? AND phase_etude = ? AND status = ?
            """,
            (affaire_id, scope, phase, STATUS_ACTIVE),
        ).fetchone()

        version_num = _next_version_num(conn, affaire_id, scope, phase)
        cur = conn.execute(
            """
            INSERT INTO affaire_client_baselines (
                affaire_id, scope, phase_etude, version_num, status,
                total_ht, prix_cfo, prix_cfa, prix_pv,
                payload_json, label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                affaire_id,
                scope,
                phase,
                version_num,
                STATUS_ACTIVE,
                total,
                preview.get("prix_cfo") or preview.get("CFO"),
                preview.get("prix_cfa") or preview.get("CFA"),
                preview.get("prix_pv") or preview.get("PV"),
                json.dumps(payload, ensure_ascii=False),
                label or f"Validation {phase} v{version_num}",
            ),
        )
        new_id = int(cur.lastrowid)

        if prev:
            conn.execute(
                """
                UPDATE affaire_client_baselines
                SET status = ?, superseded_at = CURRENT_TIMESTAMP, superseded_by_id = ?
                WHERE id = ?
                """,
                (STATUS_SUPERSEDED, new_id, int(prev["id"])),
            )

        conn.commit()
        row = conn.execute(
            "SELECT * FROM affaire_client_baselines WHERE id = ?", (new_id,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def mark_phase_baselines_remise(
    affaire_id: int,
    phase_etude: str,
    scope: str,
    profile: str | None = None,
) -> int:
    """Passe la baseline active de la phase en statut remise (changement de phase)."""
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        return 0
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_baseline_tables(conn)
        cur = conn.execute(
            """
            UPDATE affaire_client_baselines
            SET status = ?
            WHERE affaire_id = ? AND scope = ? AND phase_etude = ? AND status = ?
            """,
            (STATUS_REMISE, affaire_id, scope, phase_etude, STATUS_ACTIVE),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_active_baseline(
    affaire_id: int,
    scope: str = SCOPE_FICHE,
    profile: str | None = None,
) -> dict | None:
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        return None
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_baseline_tables(conn)
        row = conn.execute(
            """
            SELECT * FROM affaire_client_baselines
            WHERE affaire_id = ? AND scope = ? AND status = ?
            ORDER BY validated_at DESC, id DESC
            LIMIT 1
            """,
            (affaire_id, scope, STATUS_ACTIVE),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_baselines(
    affaire_id: int,
    scope: str | None = None,
    profile: str | None = None,
    limit: int = 50,
) -> list[dict]:
    prof = normalize_profile(profile) if profile else find_affaire_profile(affaire_id)
    if not prof:
        return []
    conn = get_db(prof, prefer_profile=prof)
    try:
        ensure_baseline_tables(conn)
        if scope:
            rows = conn.execute(
                """
                SELECT * FROM affaire_client_baselines
                WHERE affaire_id = ? AND scope = ?
                ORDER BY validated_at DESC, id DESC
                LIMIT ?
                """,
                (affaire_id, scope, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM affaire_client_baselines
                WHERE affaire_id = ?
                ORDER BY validated_at DESC, id DESC
                LIMIT ?
                """,
                (affaire_id, limit),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def handle_affaire_update_meta(
    affaire_id: int,
    old_affaire: dict,
    new_data: dict,
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Après mise à jour fiche : gel auto des baselines si changement de phase.
    Retourne métadonnées UI (changement typologie, phase).
    """
    old_phase = (old_affaire.get("phase_etude") or "APD").strip().upper()
    new_phase = (new_data.get("phase_etude") or "APD").strip().upper()
    old_cat = old_affaire.get("category_id")
    new_cat = new_data.get("category_id")
    try:
        new_cat_int = int(new_cat) if new_cat else None
    except (TypeError, ValueError):
        new_cat_int = None
    try:
        old_cat_int = int(old_cat) if old_cat else None
    except (TypeError, ValueError):
        old_cat_int = None

    phase_changed = old_phase != new_phase
    category_changed = old_cat_int != new_cat_int

    if phase_changed:
        mark_phase_baselines_remise(affaire_id, old_phase, SCOPE_FICHE, profile)
        mark_phase_baselines_remise(affaire_id, old_phase, SCOPE_ESTIMATION, profile)

    return {
        "phase_changed": phase_changed,
        "old_phase": old_phase,
        "new_phase": new_phase,
        "category_changed": category_changed,
        "old_category_id": old_cat_int,
        "new_category_id": new_cat_int,
        "old_ratios": {
            "cfo_m2": old_affaire.get("ratio_global_cfo_m2"),
            "cfa_m2": old_affaire.get("ratio_global_cfa_m2"),
            "pv_kwc": old_affaire.get("ratio_global_pv_kwc"),
        },
    }


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    if d.get("payload_json"):
        try:
            d["payload"] = json.loads(d["payload_json"])
        except json.JSONDecodeError:
            d["payload"] = {}
    return d
