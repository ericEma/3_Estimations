"""
vui_validation.py - Interface Streamlit de validation des mappings DPGF
Sprint 3

Fonctionnalités :
  - Tableau des lignes 'pending' avec désignation, localisation, PU HT
  - Par ligne : sélection du lot (CFO/CFA/PV) + recalcul dynamique du prix_normalise
  - Par ligne : top 5 correspondances DPGF (fuzzy) → sélection via radio
  - Checkbox is_stat_valid (validité statistique)
  - Bouton "Enregistrer" par ligne + bouton "Tout valider" en bas
  - Enregistrement dans devis_lines + mapping_synonyms

Usage :
  streamlit run scripts/vui_validation.py
  python -m streamlit run scripts/vui_validation.py
"""
import sys
import sqlite3
from pathlib import Path

import streamlit as st
from rapidfuzz import process, fuzz
from loguru import logger

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

DB_PATH = PROJECT_DIR / "estimation_elec.db"
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
logger.add(LOG_DIR / "vui_validation.log", rotation="1 MB", level="DEBUG",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}")

FUZZY_SEUIL_AUTO = 80
LOTS = ["CFO", "CFA", "PV"]
LOT_LABELS = {"CFO": "CFO - Courants Forts", "CFA": "CFA - Courants Faibles/SSI", "PV": "PV - Photovoltaique"}


# ══════════════════════════════════════════════════════════════
# Accès base de données
# ══════════════════════════════════════════════════════════════

@st.cache_resource
def get_connection():
    """Connexion SQLite partagée (row_factory pour accès par nom de colonne)."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def load_project(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()


def load_all_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, devis_date, coef_cfo, coef_cfa, coef_pv FROM projects ORDER BY id DESC"
    ).fetchall()


def load_pending_lines(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT dl.id, dl.excel_row_num, dl.original_designation, dl.unit,
               dl.quantity, dl.unit_price_ht, dl.total_ht, dl.prix_normalise,
               dl.mapping_score, dl.mapping_candidate, dl.context_path,
               dl.lot, dl.is_stat_valid
        FROM devis_lines dl
        WHERE dl.project_id = ?
          AND dl.mapping_status = 'pending'
        ORDER BY dl.mapping_score ASC
        """,
        (project_id,),
    ).fetchall()


def load_dpgf_articles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, code, designation, unit, chapter, section, ratio_type
        FROM dpgf_articles
        WHERE row_type = 'article'
        ORDER BY chapter, row_order
        """
    ).fetchall()


# ══════════════════════════════════════════════════════════════
# Fuzzy matching
# ══════════════════════════════════════════════════════════════

def get_top5_candidates(
    designation: str,
    context_path: str,
    all_articles: list[sqlite3.Row],
) -> list[dict]:
    """Retourne les 5 meilleures correspondances DPGF pour une désignation devis.

    Stratégie : fuzzy sur la désignation, résultats filtrés par score décroissant.
    Retourne des dicts {id, designation, unit, chapter, score}.
    """
    desigs = [a["designation"] for a in all_articles]
    results = process.extract(designation, desigs, scorer=fuzz.WRatio, limit=5)
    candidates = []
    for matched_desig, score, idx in results:
        art = all_articles[idx]
        candidates.append({
            "id":          art["id"],
            "designation": art["designation"],
            "unit":        art["unit"] or "",
            "chapter":     art["chapter"],
            "score":       round(score, 1),
        })
    return candidates


# ══════════════════════════════════════════════════════════════
# Actions de sauvegarde
# ══════════════════════════════════════════════════════════════

def save_line(
    conn: sqlite3.Connection,
    line_id: int,
    dpgf_article_id: int | None,
    lot: str,
    coef_lot: float,
    is_stat_valid: bool,
    designation_entreprise: str,
) -> None:
    """Met à jour une ligne devis_lines et enregistre dans mapping_synonyms."""
    prix_normalise = None
    # Recalcul prix_normalise avec le lot sélectionné
    row = conn.execute(
        "SELECT unit_price_ht FROM devis_lines WHERE id = ?", (line_id,)
    ).fetchone()
    if row and row["unit_price_ht"] and coef_lot > 0:
        prix_normalise = round(row["unit_price_ht"] / coef_lot, 4)

    conn.execute(
        """
        UPDATE devis_lines
        SET mapping_status   = 'manual',
            dpgf_article_id  = ?,
            lot              = ?,
            prix_normalise   = ?,
            is_stat_valid    = ?
        WHERE id = ?
        """,
        (dpgf_article_id, lot, prix_normalise, 1 if is_stat_valid else 0, line_id),
    )

    # Enregistrement du synonyme si un article est sélectionné
    if dpgf_article_id is not None:
        conn.execute(
            """
            INSERT OR IGNORE INTO mapping_synonyms (designation_entreprise, dpgf_article_id, source)
            VALUES (?, ?, 'manual')
            """,
            (designation_entreprise, dpgf_article_id),
        )

    conn.commit()
    logger.info(
        f"Ligne id={line_id} validee : article={dpgf_article_id} | lot={lot} | "
        f"prix_normalise={prix_normalise} | is_stat_valid={is_stat_valid}"
    )


# ══════════════════════════════════════════════════════════════
# Interface principale
# ══════════════════════════════════════════════════════════════

def render_summary(pending_lines: list, project: sqlite3.Row) -> None:
    """Affiche le bandeau de résumé en haut de page."""
    n_pending = len(pending_lines)
    cols = st.columns(4)
    cols[0].metric("Lignes en attente", n_pending)
    cols[1].metric("Coef CFO", project["coef_cfo"])
    cols[2].metric("Coef CFA", project["coef_cfa"])
    cols[3].metric("Coef PV",  project["coef_pv"])


def render_line_card(
    line: sqlite3.Row,
    candidates: list[dict],
    project: sqlite3.Row,
    conn: sqlite3.Connection,
    line_index: int,
) -> bool:
    """Affiche une carte d'édition pour une ligne pending.

    Retourne True si la ligne a été sauvegardée.
    """
    row_label = f"L.{line['excel_row_num']}" if line["excel_row_num"] else "—"
    score_color = "red" if (line["mapping_score"] or 0) < 50 else "orange"
    score_val   = f"{line['mapping_score'] or 0:.0f}%"

    header = f"{row_label}  |  {(line['original_designation'] or '')[:55]}"
    with st.expander(header, expanded=False):

        col_info, col_edit = st.columns([2, 3])

        # ── Colonne gauche : infos source ────────────────────────
        with col_info:
            st.caption("**Source devis**")
            st.write(f"Désignation : **{line['original_designation']}**")
            st.write(f"Localisation : {line['context_path'] or '—'}")
            st.write(f"Unité : {line['unit'] or '—'}  |  Qté : {line['quantity'] or '—'}")
            st.write(f"PU HT brut : {line['unit_price_ht']:,.4f} €" if line["unit_price_ht"] else "PU HT : —")
            st.markdown(
                f"Score actuel : :{score_color}[{score_val}]  "
                f"Candidat : *{line['mapping_candidate'] or 'aucun'}*"
            )

        # ── Colonne droite : édition ────────────────────────────
        with col_edit:
            st.caption("**Validation**")

            # 1. Sélection du lot
            current_lot = line["lot"] or "CFO"
            lot_idx = LOTS.index(current_lot) if current_lot in LOTS else 0
            lot_sel = st.selectbox(
                "Lot",
                options=LOTS,
                index=lot_idx,
                format_func=lambda x: LOT_LABELS[x],
                key=f"lot_{line_index}",
            )
            coef_lot = project[f"coef_{lot_sel.lower()}"]

            # 2. Prix normalisé dynamique
            pu_brut = line["unit_price_ht"]
            prix_norm_calc = round(pu_brut / coef_lot, 4) if pu_brut and coef_lot > 0 else None
            prix_norm_display = f"{prix_norm_calc:,.4f} €" if prix_norm_calc else "—"
            st.metric(
                f"Prix normalisé (coef {coef_lot})",
                prix_norm_display,
                delta=f"{prix_norm_calc - pu_brut:+.4f} €" if prix_norm_calc and pu_brut else None,
                delta_color="normal",
            )

            # 3. Sélection correspondance DPGF (top 5 radio)
            if candidates:
                options_labels = [
                    f"[{c['score']:>5.1f}%]  {c['designation'][:50]}  ({c['unit'] or '—'})  — {c['chapter'][:25]}"
                    for c in candidates
                ]
                options_labels.append("[ Aucune correspondance — marquer is_stat_valid=0 ]")

                default_idx = 0
                # Préselectionner la ligne avec le meilleur score
                radio_sel = st.radio(
                    "Correspondance DPGF (top 5)",
                    options=list(range(len(options_labels))),
                    format_func=lambda i: options_labels[i],
                    index=default_idx,
                    key=f"radio_{line_index}",
                )
                is_no_match = (radio_sel == len(options_labels) - 1)
                selected_article_id = None if is_no_match else candidates[radio_sel]["id"]
            else:
                st.warning("Aucun candidat fuzzy trouvé.")
                selected_article_id = None
                is_no_match = True

            # 4. Validité statistique
            default_stat = not is_no_match
            is_stat_valid = st.checkbox(
                "Valide pour les statistiques (is_stat_valid)",
                value=default_stat,
                key=f"stat_{line_index}",
            )

            # 5. Bouton d'enregistrement
            if st.button("Enregistrer cette ligne", key=f"save_{line_index}", type="primary"):
                save_line(
                    conn=conn,
                    line_id=line["id"],
                    dpgf_article_id=selected_article_id,
                    lot=lot_sel,
                    coef_lot=coef_lot,
                    is_stat_valid=is_stat_valid,
                    designation_entreprise=line["original_designation"],
                )
                st.success(
                    f"Ligne validée : article_id={selected_article_id} | "
                    f"lot={lot_sel} | is_stat_valid={is_stat_valid}"
                )
                logger.info(f"UI : ligne id={line['id']} enregistree via bouton individuel")
                return True

    return False


def main():
    st.set_page_config(
        page_title="Validation DPGF",
        page_icon=":white_check_mark:",
        layout="wide",
    )
    st.title("Validation des mappings DPGF")

    conn = get_connection()

    # ── Sélection du projet ───────────────────────────────────
    projects = load_all_projects(conn)
    if not projects:
        st.error("Aucun projet dans la base. Lancez import_devis.py d'abord.")
        st.stop()

    project_options = {p["id"]: f"[{p['id']}] {p['name']} ({p['devis_date']})" for p in projects}
    project_id = st.selectbox(
        "Projet",
        options=list(project_options.keys()),
        format_func=lambda pid: project_options[pid],
    )

    project = load_project(conn, project_id)
    if project is None:
        st.error(f"Projet {project_id} introuvable.")
        st.stop()

    # ── Chargement des données ────────────────────────────────
    pending_lines  = load_pending_lines(conn, project_id)
    all_articles   = load_dpgf_articles(conn)

    if not pending_lines:
        st.success("Aucune ligne pending pour ce projet. Tout est validé !")
        st.balloons()
        st.stop()

    # ── Bandeau résumé ────────────────────────────────────────
    render_summary(pending_lines, project)
    st.divider()

    # ── Pré-calcul des top 5 pour toutes les lignes ───────────
    # (en dehors du loop pour éviter le recalcul à chaque interaction)
    article_list = list(all_articles)
    candidates_map = {}
    for line in pending_lines:
        candidates_map[line["id"]] = get_top5_candidates(
            line["original_designation"] or "",
            line["context_path"] or "",
            article_list,
        )

    # ── Affichage des lignes ──────────────────────────────────
    st.subheader(f"{len(pending_lines)} ligne(s) en attente de validation")
    st.caption("Triées par score croissant (les plus ambiguës en premier).")

    saved_count = 0
    for idx, line in enumerate(pending_lines):
        saved = render_line_card(
            line=line,
            candidates=candidates_map[line["id"]],
            project=project,
            conn=conn,
            line_index=idx,
        )
        if saved:
            saved_count += 1

    # ── Pied de page : sauvegarde globale ─────────────────────
    st.divider()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM devis_lines WHERE project_id=? AND mapping_status='pending'",
        (project_id,),
    ).fetchone()[0]

    col_stat, col_btn = st.columns([3, 1])
    with col_stat:
        if remaining == 0:
            st.success("Toutes les lignes ont été validées !")
        else:
            st.info(f"Restant à valider dans la base : {remaining} ligne(s)")
    with col_btn:
        if st.button("Rafraichir", use_container_width=True):
            st.cache_resource.clear()
            st.rerun()


if __name__ == "__main__":
    main()
