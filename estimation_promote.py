"""Promotion sections / articles affaire-only → base de prix (bibliothèque DPGF)."""

from __future__ import annotations

import sqlite3

from models import (
    ESTIMATION_CHAPTER_DESIGNATIONS,
    _verify_foreign_keys_enabled,
    get_db,
)


def _is_pv_chapter(chapter: str) -> bool:
    return "photovolta" in (chapter or "").lower()


def _ratio_type_for_unit(unit: str | None) -> str:
    u = (unit or "").lower().replace("\u00b2", "2").replace(" ", "")
    return "SURFACIQUE" if u in ("m2", "m²") else "UNITAIRE"


def _section_exists_in_bibliotheque(conn: sqlite3.Connection, chapter: str, section: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM dpgf_articles
        WHERE chapter = ? AND section = ?
          AND (is_hidden IS NULL OR is_hidden = 0)
        LIMIT 1
        """,
        (chapter, section),
    ).fetchone()
    if row:
        return True
    row = conn.execute(
        """
        SELECT 1 FROM bibliotheque_section_ratios
        WHERE chapter = ? AND section = ?
        """,
        (chapter, section),
    ).fetchone()
    return bool(row)


def _promoted_section_insert_order(
    conn: sqlite3.Connection,
    affaire_id: int,
    chapter: str,
    section: str,
    article_count: int,
) -> int:
    """Positionne une section promue selon l'ordre de la page Estimation."""
    estimation_rows = conn.execute(
        """
        SELECT section
        FROM affaire_estimation_section_sort
        WHERE affaire_id = ? AND chapter = ?
        ORDER BY sort_order, section
        """,
        (affaire_id, chapter),
    ).fetchall()
    estimation_sections = [r["section"] for r in estimation_rows]
    try:
        idx = estimation_sections.index(section)
    except ValueError:
        idx = len(estimation_sections)

    existing_rows = conn.execute(
        """
        SELECT section, MIN(row_order) AS section_order, MIN(id) AS first_id
        FROM dpgf_articles
        WHERE chapter = ? AND row_type = 'article'
          AND (is_hidden IS NULL OR is_hidden = 0)
        GROUP BY section
        ORDER BY section_order, section, first_id
        """,
        (chapter,),
    ).fetchall()
    existing_order = [r["section"] for r in existing_rows]

    target_index = len(existing_order)
    for prev_sec in reversed(estimation_sections[:idx]):
        if prev_sec in existing_order:
            target_index = existing_order.index(prev_sec) + 1
            break
    else:
        for next_sec in estimation_sections[idx + 1 :]:
            if next_sec in existing_order:
                target_index = existing_order.index(next_sec)
                break

    final_sections = list(existing_order)
    final_sections.insert(target_index, section)

    order_value = 10
    insert_order: int | None = None
    reserve = max(int(article_count or 1), 1) * 10
    for sec in final_sections:
        if sec == section:
            insert_order = order_value
            order_value += reserve
            continue
        articles = conn.execute(
            """
            SELECT id
            FROM dpgf_articles
            WHERE chapter = ? AND section = ? AND row_type = 'article'
              AND (is_hidden IS NULL OR is_hidden = 0)
            ORDER BY row_order, id
            """,
            (chapter, sec),
        ).fetchall()
        for art in articles:
            conn.execute(
                "UPDATE dpgf_articles SET row_order = ? WHERE id = ?",
                (order_value, int(art["id"])),
            )
            order_value += 10

    if insert_order is None:
        insert_order = order_value
    return insert_order


def promote_section_to_bibliotheque(
    conn: sqlite3.Connection, affaire_id: int, chapter: str, section: str
) -> dict:
    """Copie une section locale (affaire) dans la base de prix et relie les lignes."""
    chapter = (chapter or "").strip()
    section = (section or "").strip()
    if chapter not in ESTIMATION_CHAPTER_DESIGNATIONS:
        raise ValueError("Chapitre invalide")
    if not section:
        raise ValueError("Section requise")

    key = f"sect:{chapter}|{section}"
    st = conn.execute(
        """
        SELECT COALESCE(is_local, 0) AS is_local, COALESCE(use_macro, 0) AS use_macro,
               ratio_m2_override, COALESCE(qty, 1) AS qty
        FROM affaire_chapter_settings
        WHERE affaire_id = ? AND chapter_key = ?
        """,
        (affaire_id, key),
    ).fetchone()
    if not st or not int(st["is_local"] or 0):
        raise ValueError("Seules les sections créées sur cette affaire peuvent être promues")

    if _section_exists_in_bibliotheque(conn, chapter, section):
        raise ValueError(
            f'La section « {section} » existe déjà dans la base de prix pour ce chapitre'
        )

    lines = conn.execute(
        """
        SELECT id, line_designation, unit_override, unit_price_ht, ratio_ref, quantity
        FROM affaire_lines
        WHERE affaire_id = ?
          AND line_chapter = ? AND line_section = ?
          AND dpgf_article_id IS NULL
        ORDER BY COALESCE(sort_order, id), id
        """,
        (affaire_id, chapter, section),
    ).fetchall()

    next_order = _promoted_section_insert_order(
        conn, affaire_id, chapter, section, len(lines)
    )

    promoted = []
    for ln in lines:
        dpgf_id = _insert_bibliotheque_article(
            conn,
            chapter=chapter,
            section=section,
            designation=(ln["line_designation"] or "Nouvel article").strip() or "Nouvel article",
            unit=(ln["unit_override"] or "u").strip() or "u",
            pu=_effective_pu(ln),
            row_order=next_order,
        )
        next_order += 10
        conn.execute(
            """
            UPDATE affaire_lines SET
                dpgf_article_id = ?,
                line_chapter = NULL,
                line_section = NULL,
                ratio_ref = COALESCE(ratio_ref, ?)
            WHERE id = ? AND affaire_id = ?
            """,
            (dpgf_id, _effective_pu(ln), ln["id"], affaire_id),
        )
        promoted.append(dpgf_id)

    if int(st["use_macro"] or 0) and st["ratio_m2_override"] is not None:
        ratio = float(st["ratio_m2_override"])
        if ratio > 0:
            ratio_unit = "kwc" if _is_pv_chapter(chapter) else "m2"
            conn.execute(
                """
                INSERT INTO bibliotheque_section_ratios (chapter, section, ratio_m2, ratio_unit)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chapter, section) DO UPDATE SET
                    ratio_m2 = excluded.ratio_m2,
                    ratio_unit = excluded.ratio_unit,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chapter, section, ratio, ratio_unit),
            )

    conn.execute(
        """
        UPDATE affaire_chapter_settings SET is_local = 0
        WHERE affaire_id = ? AND chapter_key = ?
        """,
        (affaire_id, key),
    )

    return {
        "section": section,
        "chapter": chapter,
        "articles_promoted": len(promoted),
        "dpgf_ids": promoted,
    }


def promote_article_to_bibliotheque(
    conn: sqlite3.Connection, affaire_id: int, line_id: int
) -> dict:
    """Copie un article affaire-only (arbre estimation) dans la base de prix."""
    ln = conn.execute(
        """
        SELECT id, line_chapter, line_section, line_designation, unit_override,
               unit_price_ht, ratio_ref, quantity
        FROM affaire_lines
        WHERE id = ? AND affaire_id = ? AND dpgf_article_id IS NULL
          AND line_chapter IS NOT NULL AND TRIM(line_chapter) != ''
        """,
        (line_id, affaire_id),
    ).fetchone()
    if not ln:
        raise ValueError("Article affaire introuvable ou déjà lié au référentiel")

    chapter = (ln["line_chapter"] or "").strip()
    section = (ln["line_section"] or "").strip()
    if not chapter or not section:
        raise ValueError("Chapitre et section requis")

    max_order = conn.execute(
        "SELECT COALESCE(MAX(row_order), 0) FROM dpgf_articles WHERE chapter=? AND section=?",
        (chapter, section),
    ).fetchone()[0]

    pu = _effective_pu(ln)
    desig = (ln["line_designation"] or "Nouvel article").strip() or "Nouvel article"
    unit = (ln["unit_override"] or "u").strip() or "u"

    dpgf_id = _insert_bibliotheque_article(
        conn,
        chapter=chapter,
        section=section,
        designation=desig,
        unit=unit,
        pu=pu,
        row_order=max_order + 1,
    )

    conn.execute(
        """
        UPDATE affaire_lines SET
            dpgf_article_id = ?,
            line_chapter = NULL,
            line_section = NULL,
            ratio_ref = COALESCE(NULLIF(ratio_ref, 0), ?)
        WHERE id = ? AND affaire_id = ?
        """,
        (dpgf_id, pu, line_id, affaire_id),
    )

    return {
        "line_id": line_id,
        "dpgf_id": dpgf_id,
        "chapter": chapter,
        "section": section,
        "designation": desig,
    }


def _effective_pu(ln: sqlite3.Row) -> float:
    pu = ln["unit_price_ht"]
    if pu is not None and float(pu or 0) > 0:
        return float(pu)
    return float(ln["ratio_ref"] or 0)


def _insert_bibliotheque_article(
    conn: sqlite3.Connection,
    *,
    chapter: str,
    section: str,
    designation: str,
    unit: str,
    pu: float,
    row_order: int,
) -> int:
    ratio_type = _ratio_type_for_unit(unit)
    cur = conn.execute(
        """
        INSERT INTO dpgf_articles
          (designation, unit, chapter, section, row_order,
           row_type, ratio_type, ratio_type_source, is_custom, qty_ref, pu_ht_ref)
        VALUES (?, ?, ?, ?, ?, 'article', ?, 'manual', 1, 0, ?)
        """,
        (designation, unit, chapter, section, row_order, ratio_type, pu if pu > 0 else None),
    )
    dpgf_id = int(cur.lastrowid)
    if pu > 0:
        conn.execute(
            """
            INSERT INTO ratio_overrides (dpgf_article_id, pu_override, raison)
            VALUES (?, ?, 'Promotion depuis estimation affaire')
            ON CONFLICT(dpgf_article_id) DO UPDATE SET
                pu_override = excluded.pu_override,
                created_at = CURRENT_TIMESTAMP
            """,
            (dpgf_id, pu),
        )
    return dpgf_id


def handle_promote_action(affaire_id: int, action: str, data: dict) -> dict:
    conn = get_db(affaire_id=affaire_id)
    try:
        _verify_foreign_keys_enabled(conn)
        aff = conn.execute("SELECT id FROM affaires WHERE id = ?", (affaire_id,)).fetchone()
        if not aff:
            return {"status": "error", "message": "Affaire introuvable"}

        if action == "promote_section":
            out = promote_section_to_bibliotheque(
                conn,
                affaire_id,
                (data.get("chapter") or "").strip(),
                (data.get("section") or "").strip(),
            )
        elif action == "promote_article":
            line_id = data.get("line_id")
            if line_id is None or line_id == "":
                raise ValueError("line_id requis")
            out = promote_article_to_bibliotheque(conn, affaire_id, int(line_id))
        else:
            return {"status": "error", "message": f"Action inconnue : {action}"}

        conn.commit()
        out["status"] = "ok"
        return out
    except ValueError as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()
