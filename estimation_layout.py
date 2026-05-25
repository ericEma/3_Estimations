"""Opérations layout page Estimation (sections / articles affaire-only, ordre)."""

from __future__ import annotations

import sqlite3

from models import (
    ESTIMATION_CHAPTER_DESIGNATIONS,
    derive_lot_from_chapter,
    get_db,
    _compute_estimation_kpis_conn,
    _round_money2,
    _verify_foreign_keys_enabled,
)


def _chap_index(chapter: str) -> int:
    try:
        return ESTIMATION_CHAPTER_DESIGNATIONS.index(chapter)
    except ValueError:
        return 99


def init_section_sort_from_catalog(conn: sqlite3.Connection, affaire_id: int) -> None:
    """Initialise l'ordre des sections depuis le catalogue snapshot."""
    rows = conn.execute(
        """
        SELECT da.chapter, da.section, MIN(COALESCE(al.sort_order, da.row_order * 10)) AS mo
        FROM affaire_lines al
        INNER JOIN dpgf_articles da ON da.id = al.dpgf_article_id
        WHERE al.affaire_id = ?
        GROUP BY da.chapter, da.section
        ORDER BY da.chapter, mo
        """,
        (affaire_id,),
    ).fetchall()
    order = 0
    for r in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO affaire_estimation_section_sort
                (affaire_id, chapter, section, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (affaire_id, r["chapter"], r["section"], order),
        )
        order += 10


def get_section_sort_map(conn: sqlite3.Connection, affaire_id: int) -> dict:
    rows = conn.execute(
        """
        SELECT chapter, section, sort_order
        FROM affaire_estimation_section_sort
        WHERE affaire_id = ?
        """,
        (affaire_id,),
    ).fetchall()
    return {f"{r['chapter']}|{r['section']}": int(r["sort_order"]) for r in rows}


def handle_layout_action(affaire_id: int, action: str, data: dict) -> dict:
    """Actions : add_section, add_article, delete_section, move_section, move_article."""
    conn = get_db()
    try:
        _verify_foreign_keys_enabled(conn)
        aff = conn.execute(
            "SELECT id FROM affaires WHERE id = ?", (affaire_id,)
        ).fetchone()
        if not aff:
            return {"status": "error", "message": "Affaire introuvable"}

        if action == "add_section":
            out = _add_section(conn, affaire_id, data)
        elif action == "add_article":
            out = _add_article(conn, affaire_id, data)
        elif action == "delete_section":
            out = _delete_section(conn, affaire_id, data)
        elif action == "move_section":
            out = _move_section(conn, affaire_id, data)
        elif action == "move_article":
            out = _move_article(conn, affaire_id, data)
        elif action == "rename_section":
            out = _rename_section(conn, affaire_id, data)
        else:
            return {"status": "error", "message": f"Action inconnue : {action}"}

        conn.commit()
        out["totals"] = _compute_estimation_kpis_conn(conn, affaire_id)
        out["section_sort"] = [
            dict(r)
            for r in conn.execute(
                """
                SELECT chapter, section, sort_order
                FROM affaire_estimation_section_sort
                WHERE affaire_id = ?
                ORDER BY chapter, sort_order
                """,
                (affaire_id,),
            ).fetchall()
        ]
        out["status"] = "ok"
        return out
    except ValueError as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def _unique_section_name(
    conn: sqlite3.Connection, affaire_id: int, chapter: str, base: str
) -> str:
    """Évite les doublons de libellé de section dans un chapitre."""
    existing = {
        r["section"]
        for r in conn.execute(
            """
            SELECT section FROM affaire_estimation_section_sort
            WHERE affaire_id = ? AND chapter = ?
            """,
            (affaire_id, chapter),
        ).fetchall()
    }
    if base not in existing:
        return base
    n = 2
    while f"{base} ({n})" in existing:
        n += 1
    return f"{base} ({n})"


def _add_section(conn, affaire_id: int, data: dict) -> dict:
    chapter = (data.get("chapter") or "").strip()
    name = (data.get("section_name") or "").strip()
    after_section = (data.get("after_section") or "").strip() or None
    if not chapter:
        raise ValueError("Chapitre requis")
    if not name:
        name = "Nouvelle section"
    name = _unique_section_name(conn, affaire_id, chapter, name)
    if chapter not in ESTIMATION_CHAPTER_DESIGNATIONS:
        raise ValueError("Chapitre invalide")

    exists = conn.execute(
        """
        SELECT 1 FROM affaire_estimation_section_sort
        WHERE affaire_id = ? AND chapter = ? AND section = ?
        """,
        (affaire_id, chapter, name),
    ).fetchone()
    if exists:
        raise ValueError(f'La section "{name}" existe déjà dans ce chapitre')

    sort_map = get_section_sort_map(conn, affaire_id)
    chap_secs = sorted(
        [(s, sort_map.get(f"{chapter}|{s}", 0)) for s in set(
            k.split("|", 1)[1] for k in sort_map if k.startswith(chapter + "|")
        )],
        key=lambda x: x[1],
    )
    if after_section:
        after_ord = sort_map.get(f"{chapter}|{after_section}")
        if after_ord is None:
            for s, o in chap_secs:
                if s == after_section:
                    after_ord = o
                    break
        if after_ord is None:
            after_ord = max((o for _, o in chap_secs), default=-10)
        new_ord = after_ord + 1
        for s, o in chap_secs:
            if o >= new_ord:
                conn.execute(
                    """
                    UPDATE affaire_estimation_section_sort
                    SET sort_order = sort_order + 10
                    WHERE affaire_id = ? AND chapter = ? AND section = ?
                    """,
                    (affaire_id, chapter, s),
                )
    else:
        new_ord = max((o for _, o in chap_secs), default=-10) + 10

    aff = conn.execute(
        "SELECT surface_sdo, puissance_pv_kwc FROM affaires WHERE id = ?",
        (affaire_id,),
    ).fetchone()
    sdo = float(aff["surface_sdo"] or 1000) if aff else 1000.0
    kwc = float(aff["puissance_pv_kwc"] or 100) if aff else 100.0
    divisor = kwc if "photovolta" in chapter.lower() else sdo

    conn.execute(
        """
        INSERT INTO affaire_estimation_section_sort
            (affaire_id, chapter, section, sort_order)
        VALUES (?, ?, ?, ?)
        """,
        (affaire_id, chapter, name, new_ord),
    )
    conn.execute(
        """
        INSERT INTO affaire_chapter_settings
            (affaire_id, chapter_key, is_included, use_macro, qty,
             ratio_m2_override, is_local)
        VALUES (?, ?, 1, 1, ?, 0, 1)
        """,
        (affaire_id, f"sect:{chapter}|{name}", divisor),
    )
    art = _add_article(conn, affaire_id, {"chapter": chapter, "section": name})
    return {
        "section": name,
        "chapter": chapter,
        "sort_order": new_ord,
        "is_local": True,
        "use_macro": True,
        "line_id": art.get("line_id"),
    }


def _rename_section(conn, affaire_id: int, data: dict) -> dict:
    chapter = (data.get("chapter") or "").strip()
    old_section = (data.get("old_section") or "").strip()
    new_section = (data.get("new_section") or "").strip()
    if not chapter or not old_section or not new_section:
        raise ValueError("Chapitre, ancien et nouveau nom requis")
    if old_section == new_section:
        return {"section": new_section}

    key_old = f"sect:{chapter}|{old_section}"
    row = conn.execute(
        """
        SELECT COALESCE(is_local, 0) AS is_local FROM affaire_chapter_settings
        WHERE affaire_id = ? AND chapter_key = ?
        """,
        (affaire_id, key_old),
    ).fetchone()
    if not row or not int(row["is_local"] or 0):
        raise ValueError("Seules les sections créées sur cette affaire peuvent être renommées")

    dup = conn.execute(
        """
        SELECT 1 FROM affaire_estimation_section_sort
        WHERE affaire_id = ? AND chapter = ? AND section = ?
        """,
        (affaire_id, chapter, new_section),
    ).fetchone()
    if dup:
        raise ValueError(f'La section « {new_section} » existe déjà')

    conn.execute(
        """
        UPDATE affaire_estimation_section_sort SET section = ?
        WHERE affaire_id = ? AND chapter = ? AND section = ?
        """,
        (new_section, affaire_id, chapter, old_section),
    )
    key_new = f"sect:{chapter}|{new_section}"
    conn.execute(
        """
        UPDATE affaire_chapter_settings SET chapter_key = ?
        WHERE affaire_id = ? AND chapter_key = ?
        """,
        (key_new, affaire_id, key_old),
    )
    conn.execute(
        """
        UPDATE affaire_lines SET line_section = ?
        WHERE affaire_id = ? AND line_chapter = ? AND line_section = ?
        """,
        (new_section, affaire_id, chapter, old_section),
    )
    return {"section": new_section, "old_section": old_section}


def _next_line_sort(conn, affaire_id: int, chapter: str, section: str) -> float:
    row = conn.execute(
        """
        SELECT MAX(COALESCE(sort_order, 0)) AS m
        FROM affaire_lines
        WHERE affaire_id = ?
          AND (
            (dpgf_article_id IS NOT NULL AND dpgf_article_id IN (
                SELECT id FROM dpgf_articles WHERE chapter = ? AND section = ?
            ))
            OR (line_chapter = ? AND line_section = ?)
          )
        """,
        (affaire_id, chapter, section, chapter, section),
    ).fetchone()
    return float(row["m"] or 0) + 10.0


def _add_article(conn, affaire_id: int, data: dict) -> dict:
    chapter = (data.get("chapter") or "").strip()
    section = (data.get("section") or "").strip()
    after_dpgf = data.get("after_dpgf_id")
    after_line = data.get("after_line_id")
    if not chapter or not section:
        raise ValueError("Chapitre et section requis")

    sort_base = _next_line_sort(conn, affaire_id, chapter, section)
    if after_dpgf:
        ref = conn.execute(
            "SELECT sort_order FROM affaire_lines WHERE affaire_id = ? AND dpgf_article_id = ?",
            (affaire_id, int(after_dpgf)),
        ).fetchone()
        if ref and ref["sort_order"] is not None:
            sort_base = float(ref["sort_order"]) + 1.0
    elif after_line:
        ref = conn.execute(
            "SELECT sort_order FROM affaire_lines WHERE id = ? AND affaire_id = ?",
            (int(after_line), affaire_id),
        ).fetchone()
        if ref and ref["sort_order"] is not None:
            sort_base = float(ref["sort_order"]) + 1.0

    lot = derive_lot_from_chapter(chapter)
    cur = conn.execute(
        """
        INSERT INTO affaire_lines (
            affaire_id, dpgf_article_id, quantity, quantity_source,
            unit_price_ht, unit_price_source, total_ht, is_included,
            ratio_ref, deviation_pct, line_designation, unit_override, line_lot,
            line_chapter, line_section, sort_order
        ) VALUES (?, NULL, 0, 'manual', 0, 'manual', 0, 1, 0, 0,
                  'Nouvel article', 'u', ?, ?, ?, ?)
        """,
        (affaire_id, lot, chapter, section, sort_base),
    )
    line_id = int(cur.lastrowid)
    return {
        "line_id": line_id,
        "chapter": chapter,
        "section": section,
        "sort_order": sort_base,
        "is_tree_custom": True,
    }


def _delete_section(conn, affaire_id: int, data: dict) -> dict:
    chapter = (data.get("chapter") or "").strip()
    section = (data.get("section") or "").strip()
    key = f"sect:{chapter}|{section}"
    row = conn.execute(
        """
        SELECT is_local FROM affaire_chapter_settings
        WHERE affaire_id = ? AND chapter_key = ?
        """,
        (affaire_id, key),
    ).fetchone()
    if not row or not int(row["is_local"] or 0):
        raise ValueError("Seules les sections créées sur cette affaire peuvent être supprimées")

    conn.execute(
        "DELETE FROM affaire_lines WHERE affaire_id = ? AND line_chapter = ? AND line_section = ?",
        (affaire_id, chapter, section),
    )
    conn.execute(
        """
        DELETE FROM affaire_estimation_section_sort
        WHERE affaire_id = ? AND chapter = ? AND section = ?
        """,
        (affaire_id, chapter, section),
    )
    conn.execute(
        "DELETE FROM affaire_chapter_settings WHERE affaire_id = ? AND chapter_key = ?",
        (affaire_id, key),
    )
    return {"deleted_section": section}


def _move_section(conn, affaire_id: int, data: dict) -> dict:
    chapter = (data.get("chapter") or "").strip()
    section = (data.get("section") or "").strip()
    direction = (data.get("direction") or "up").lower()
    rows = conn.execute(
        """
        SELECT section, sort_order FROM affaire_estimation_section_sort
        WHERE affaire_id = ? AND chapter = ?
        ORDER BY sort_order, section
        """,
        (affaire_id, chapter),
    ).fetchall()
    if len(rows) < 2:
        return {}
    secs = [dict(r) for r in rows]
    idx = next((i for i, s in enumerate(secs) if s["section"] == section), None)
    if idx is None:
        raise ValueError("Section introuvable")
    swap = idx - 1 if direction == "up" else idx + 1
    if swap < 0 or swap >= len(secs):
        return {}
    a, b = secs[idx], secs[swap]
    conn.execute(
        """
        UPDATE affaire_estimation_section_sort SET sort_order = ?
        WHERE affaire_id = ? AND chapter = ? AND section = ?
        """,
        (b["sort_order"], affaire_id, chapter, a["section"]),
    )
    conn.execute(
        """
        UPDATE affaire_estimation_section_sort SET sort_order = ?
        WHERE affaire_id = ? AND chapter = ? AND section = ?
        """,
        (a["sort_order"], affaire_id, chapter, b["section"]),
    )
    return {"swapped": [a["section"], b["section"]]}


def _move_article(conn, affaire_id: int, data: dict) -> dict:
    chapter = (data.get("chapter") or "").strip()
    section = (data.get("section") or "").strip()
    direction = (data.get("direction") or "up").lower()
    dpgf_id = data.get("dpgf_id")
    line_id = data.get("line_id")

    lines = conn.execute(
        """
        SELECT al.id AS line_id, al.dpgf_article_id AS dpgf_id,
               COALESCE(al.sort_order, da.row_order * 10, al.id) AS sort_order
        FROM affaire_lines al
        LEFT JOIN dpgf_articles da ON da.id = al.dpgf_article_id
        WHERE al.affaire_id = ?
          AND (
            (da.chapter = ? AND da.section = ?)
            OR (al.line_chapter = ? AND al.line_section = ?)
          )
        ORDER BY sort_order, al.id
        """,
        (affaire_id, chapter, section, chapter, section),
    ).fetchall()
    items = [dict(r) for r in lines]
    if len(items) < 2:
        return {}
    idx = None
    for i, it in enumerate(items):
        if dpgf_id and it["dpgf_id"] == int(dpgf_id):
            idx = i
            break
        if line_id and it["line_id"] == int(line_id):
            idx = i
            break
    if idx is None:
        raise ValueError("Article introuvable")
    swap = idx - 1 if direction == "up" else idx + 1
    if swap < 0 or swap >= len(items):
        return {}
    a, b = items[idx], items[swap]
    conn.execute(
        "UPDATE affaire_lines SET sort_order = ? WHERE id = ?",
        (b["sort_order"], a["line_id"]),
    )
    conn.execute(
        "UPDATE affaire_lines SET sort_order = ? WHERE id = ?",
        (a["sort_order"], b["line_id"]),
    )
    return {"swapped": [a["line_id"], b["line_id"]]}
