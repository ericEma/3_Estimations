"""
Rapport des Ratios Strategiques - Estimation Elec
Sprint 3 : Intelligence Metier

Usage:
    python scripts/rapport_ratios.py [--lot CFO|CFA|ALL] [--no-export]
"""

import sqlite3
import sys
import os
import argparse
from datetime import date

# Force UTF-8 on Windows terminal
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ─── Constantes PSA Urgences ──────────────────────────────────────────────────
PROJECT_ID   = 1
SDO          = 5_000.0      # m²
KVA_CIBLE    = 800.0        # kVA (constante PSA — puissance non stockée en BDD)
TAUX_INFLATION = 0.065      # +6.5% vs base PSA (cible 2026 Branche Sud Egis)
COEF_CFO     = 1.08
COEF_CFA     = 1.12

DB_PATH      = "estimation_elec.db"
EXPORT_DIR   = "exports"
EXPORT_FILE  = os.path.join(EXPORT_DIR, "referentiel_ratios_2026.md")

# ─── Couleurs terminal ────────────────────────────────────────────────────────
RED    = "\033[91m"
ORANGE = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fmt_eur(v):
    if v is None:
        return "    N/D    "
    return f"{v:>10,.2f} €"


def alerte_color(nb):
    if nb <= 1:
        return RED + "⚠ SOURCE UNIQUE" + RESET
    if nb < 3:
        return ORANGE + "⚠ PRUDENCE" + RESET
    return GREEN + "OK" + RESET


# ═════════════════════════════════════════════════════════════════════════════
# 1. TOTAUX PAR LOT ET SECTION
# ═════════════════════════════════════════════════════════════════════════════

def get_section_totals(conn):
    rows = conn.execute("""
        SELECT da.chapter, da.section, dl.lot,
               SUM(dl.total_ht) AS total_ht,
               COUNT(DISTINCT dl.id) AS nb_lignes
        FROM devis_lines dl
        JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
        WHERE dl.project_id = ? AND dl.row_type = 'article'
          AND dl.mapping_status != 'unmapped'
        GROUP BY da.chapter, da.section, dl.lot
    """, (PROJECT_ID,)).fetchall()
    return {(r['chapter'], r['section'], r['lot']): r['total_ht'] for r in rows}


def get_lot_totals(conn):
    rows = conn.execute("""
        SELECT dl.lot, SUM(dl.total_ht) AS total
        FROM devis_lines dl
        WHERE dl.project_id = ? AND dl.row_type = 'article'
          AND dl.mapping_status != 'unmapped'
        GROUP BY dl.lot
    """, (PROJECT_ID,)).fetchall()
    return {r['lot']: r['total'] for r in rows}


def get_project_total(conn):
    r = conn.execute(
        "SELECT total_ht_source FROM projects WHERE id = ?", (PROJECT_ID,)
    ).fetchone()
    return r['total_ht_source'] if r else 0.0


# ═════════════════════════════════════════════════════════════════════════════
# 2. RATIOS STRATÉGIQUES
# ═════════════════════════════════════════════════════════════════════════════

def compute_strategic_ratios(conn):
    """Calcule les 5 ratios directeurs demandés."""
    lot_totals   = get_lot_totals(conn)
    sec_totals   = get_section_totals(conn)
    total_projet = get_project_total(conn)

    total_cfo = lot_totals.get('CFO', 0.0)
    total_cfa = lot_totals.get('CFA', 0.0)

    # ── Éclairage ──────────────────────────────────────────────────────────
    eclairage_ht = sum(v for (ch, sec, lot), v in sec_totals.items()
                       if sec == 'Eclairage' and lot == 'CFO')

    # ── VDI : section Précâblage VDI (infrastructure + baies) ─────────────
    vdi_ht = sum(v for (ch, sec, lot), v in sec_totals.items()
                 if 'VDI' in sec)

    # Prises RJ45 : murales VDI (23) + PT1 (82×2 RJ45) + PT2 (30×1 RJ45)
    rj45_rows = conn.execute("""
        SELECT da.designation, dl.quantity
        FROM devis_lines dl
        JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
        WHERE dl.project_id = ? AND dl.row_type = 'article'
          AND dl.mapping_status != 'unmapped'
          AND (da.designation LIKE '%RJ 45%' OR da.designation LIKE '%RJ45%')
    """, (PROJECT_ID,)).fetchall()

    nb_rj45 = 0
    for r in rj45_rows:
        desig = (r['designation'] or '').lower()
        qty   = r['quantity'] or 0
        if 'pt1' in desig or '2 pc rj' in desig:
            nb_rj45 += int(qty) * 2
        elif 'pt2' in desig or '1 pc rj' in desig:
            nb_rj45 += int(qty) * 1
        else:
            nb_rj45 += int(qty)

    # ── SSI ────────────────────────────────────────────────────────────────
    ssi_ht = sum(v for (ch, sec, lot), v in sec_totals.items()
                 if 'incendie' in sec.lower() or 'SSI' in sec)

    # Déclencheurs manuels (DM) — seuls points SSI individuellement chiffrés
    dm_row = conn.execute("""
        SELECT SUM(dl.quantity) AS nb
        FROM devis_lines dl
        JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
        WHERE dl.project_id = ? AND dl.row_type = 'article'
          AND dl.mapping_status != 'unmapped'
          AND da.designation LIKE '%clencheur%'
    """, (PROJECT_ID,)).fetchone()
    nb_dm = int(dm_row['nb'] or 0)

    # ── Tableaux divisionnaires ────────────────────────────────────────────
    td_rows = conn.execute("""
        SELECT dl.unit_price_ht, dl.total_ht
        FROM devis_lines dl
        JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
        WHERE dl.project_id = ? AND dl.row_type = 'article'
          AND dl.mapping_status != 'unmapped'
          AND da.section LIKE '%divisionnaire%'
    """, (PROJECT_ID,)).fetchall()

    td_values  = [r['unit_price_ht'] for r in td_rows if r['unit_price_ht']]
    td_total   = sum(td_values)
    nb_td      = len(td_values)
    td_moyenne = td_total / nb_td if nb_td else 0

    return {
        'total_projet':  total_projet,
        'total_cfo':     total_cfo,
        'total_cfa':     total_cfa,
        'eclairage_ht':  eclairage_ht,
        'vdi_ht':        vdi_ht,
        'nb_rj45':       nb_rj45,
        'ssi_ht':        ssi_ht,
        'nb_dm':         nb_dm,
        'td_total':      td_total,
        'nb_td':         nb_td,
        'td_moyenne':    td_moyenne,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 3. PARETO 80/20
# ═════════════════════════════════════════════════════════════════════════════

def get_pareto(conn, n=20):
    """Retourne les N articles les plus lourds financièrement."""
    rows = conn.execute("""
        SELECT da.id AS article_id,
               da.designation,
               da.chapter,
               da.section,
               da.unit,
               da.ratio_type,
               dl.lot,
               dl.quantity,
               dl.unit_price_ht,
               dl.total_ht,
               dl.prix_normalise,
               vr.nb_occurrences,
               vr.avg_pu_normalise,
               vr.alerte
        FROM devis_lines dl
        JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
        LEFT JOIN v_ratios vr ON vr.dpgf_article_id = da.id
        WHERE dl.project_id = ? AND dl.row_type = 'article'
          AND dl.mapping_status != 'unmapped'
          AND dl.total_ht IS NOT NULL
        ORDER BY dl.total_ht DESC
        LIMIT ?
    """, (PROJECT_ID, n)).fetchall()
    return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# 4. TABLEAU COMPLET DES RATIOS (v_ratios enrichi)
# ═════════════════════════════════════════════════════════════════════════════

def get_all_ratios(conn, lot_filter=None):
    """Retourne tous les ratios v_ratios avec prix sec/actualisé/vendu."""
    where = ""
    params = []
    if lot_filter and lot_filter != "ALL":
        where = "AND dl.lot = ?"
        params = [lot_filter]

    rows = conn.execute(f"""
        SELECT vr.dpgf_article_id,
               vr.designation,
               vr.chapter,
               vr.section,
               vr.unit,
               vr.ratio_type,
               vr.nb_occurrences,
               vr.avg_pu_normalise,
               vr.avg_pu_actualise,
               vr.pu_min,
               vr.pu_max,
               vr.alerte,
               dl.lot
        FROM v_ratios vr
        JOIN devis_lines dl ON dl.dpgf_article_id = vr.dpgf_article_id
        WHERE dl.project_id = ? AND dl.row_type = 'article'
          AND dl.mapping_status != 'unmapped'
          {where}
        GROUP BY vr.dpgf_article_id, dl.lot
        ORDER BY vr.chapter, vr.section, vr.designation
    """, [PROJECT_ID] + params).fetchall()
    return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# 5. AFFICHAGE TERMINAL
# ═════════════════════════════════════════════════════════════════════════════

def print_separator(char="=", width=100):
    print(char * width)


def print_section_title(title):
    print()
    print_separator()
    print(f"  {BOLD}{title}{RESET}")
    print_separator()


def display_strategic_ratios(ratios):
    print_section_title("RATIOS STRATÉGIQUES DIRECTEURS — PSA Urgences 2026")

    def row(label, valeur_sec, valeur_26, valeur_vendu, lot, note=""):
        coef_v = COEF_CFO if lot == 'CFO' else COEF_CFA
        v_sec  = valeur_sec
        v_26   = valeur_sec * (1 + TAUX_INFLATION) if valeur_sec else None
        v_vdu  = v_26 * coef_v if v_26 else None
        print(f"  {label:<45}  "
              f"Sec: {fmt_eur(v_sec)}  "
              f"2026: {fmt_eur(v_26)}  "
              f"Vendu: {fmt_eur(v_vdu)}")
        if note:
            print(f"    {ORANGE}↳ {note}{RESET}")

    total    = ratios['total_projet']
    cfo      = ratios['total_cfo']
    cfa      = ratios['total_cfa']
    eclairage = ratios['eclairage_ht']
    vdi      = ratios['vdi_ht']
    nb_rj45  = ratios['nb_rj45']
    ssi      = ratios['ssi_ht']
    nb_dm    = ratios['nb_dm']
    td_moy   = ratios['td_moyenne']
    nb_td    = ratios['nb_td']

    print(f"\n  {CYAN}Projet : PSA Urgences | SDO = {SDO:,.0f} m2 | kVA cible = {KVA_CIBLE:.0f} kVA | Inflation 2026 = +{TAUX_INFLATION*100:.1f}%{RESET}")
    print(f"  {CYAN}coef_vendu CFO = x{COEF_CFO} | coef_vendu CFA = x{COEF_CFA}{RESET}\n")

    print(f"  {'Indicateur':<45}  {'Prix Sec':>14}  {'Actualisé 2026':>16}  {'Prix Vendu':>14}")
    print("  " + "─" * 95)

    # CFO GLOBAL
    ratio_cfo_m2  = cfo / SDO
    ratio_cfo_kva = cfo / KVA_CIBLE
    row(f"CFO Global — €/m² SDO  ({SDO:,.0f} m²)",        ratio_cfo_m2,  None, None, 'CFO')
    row(f"CFO Global — €/kVA     ({KVA_CIBLE:.0f} kVA)",  ratio_cfo_kva, None, None, 'CFO')

    # ÉCLAIRAGE
    ratio_ecl_m2 = eclairage / SDO
    row(f"Éclairage — €/m² SDO   ({eclairage:,.0f} € lot)", ratio_ecl_m2, None, None, 'CFO')

    # VDI
    if nb_rj45 > 0:
        ratio_vdi_rj45 = vdi / nb_rj45
        row(f"VDI — €/Prise RJ45     ({nb_rj45} prises | infra seule)",
            ratio_vdi_rj45, None, None, 'CFA')
    else:
        print(f"  {'VDI — €/Prise RJ45':<45}  {RED}Nb prises RJ45 non détecté{RESET}")

    # SSI
    if nb_dm > 0:
        ratio_ssi = ssi / nb_dm
        row(f"SSI — €/DM             ({nb_dm} DM | DA non individualisés)",
            ratio_ssi, None, None, 'CFA',
            note="Détecteurs Automatiques (DA) non chiffrés unitairement dans ce devis — ratio sur DM uniquement")
    else:
        print(f"  {'SSI — €/Point DA+DM':<45}  {RED}Aucun point SSI identifié individuellement{RESET}")

    # TABLEAUX DIVISIONNAIRES
    row(f"Tableau Divisionnaire — €/u (moy. {nb_td} TD)",
        td_moy, None, None, 'CFO')

    print("\n  " + "─" * 95)
    print(f"  {'TOTAL PROJET HT':<45}  {fmt_eur(total):>14}")
    print(f"  {'  dont CFO':<45}  {fmt_eur(cfo):>14}  ({cfo/total*100:.1f}%)")
    print(f"  {'  dont CFA':<45}  {fmt_eur(cfa):>14}  ({cfa/total*100:.1f}%)")
    unmapped = total - cfo - cfa
    print(f"  {'  dont hors périmètre DPGF (non mappé)':<45}  {fmt_eur(unmapped):>14}  ({unmapped/total*100:.1f}%)")


def display_pareto(conn, ratios):
    print_section_title("PARETO 80/20 — TOP 20 ARTICLES (par montant HT)")
    total = ratios['total_projet']
    seuil_80 = total * 0.80

    articles = get_pareto(conn, 20)
    cumul = 0.0

    print(f"\n  {'Rang':<5} {'Désignation':<45} {'Lot':<5} {'Qté':>6} {'U':>5} {'PU HT':>12} {'Total HT':>12} {'Cumul%':>7} {'Source'}")
    print("  " + "─" * 118)

    for i, a in enumerate(articles, 1):
        cumul += (a['total_ht'] or 0)
        pct    = cumul / total * 100
        nb_occ = a.get('nb_occurrences') or 0

        if nb_occ <= 1:
            src_label = RED + "SOURCE UNIQUE" + RESET
        elif nb_occ < 3:
            src_label = ORANGE + f"PRUDENCE ({nb_occ})" + RESET
        else:
            src_label = GREEN + f"OK ({nb_occ})" + RESET

        at_80 = " ◄ 80%" if cumul >= seuil_80 and (cumul - (a['total_ht'] or 0)) < seuil_80 else ""

        desig = (a['designation'] or '')[:44]
        print(f"  {i:<5} {desig:<45} {(a['lot'] or ''):<5} "
              f"{(a['quantity'] or 0):>6.0f} {(a['unit'] or ''):>5} "
              f"{fmt_eur(a['unit_price_ht']):>12} {fmt_eur(a['total_ht']):>12} "
              f"{pct:>6.1f}%{at_80} {src_label}")

    print(f"\n  Seuil 80% : {fmt_eur(seuil_80)} | Total projet : {fmt_eur(total)}")


def display_ratios_table(conn, lot_filter="ALL"):
    title = f"TABLEAU DES RATIOS UNITAIRES — Lot : {lot_filter}"
    print_section_title(title)

    ratios = get_all_ratios(conn, lot_filter)

    print(f"\n  {'Désignation':<40} {'Lot':<5} {'U':>4} {'Type':>10} "
          f"{'Prix Sec':>12} {'Prix 2026':>12} {'Prix Vendu':>12} {'Occ':>4} {'Alerte'}")
    print("  " + "─" * 115)

    chapter_prev = None
    for r in ratios:
        ch = r['chapter']
        if ch != chapter_prev:
            print(f"\n  {BOLD}{CYAN}>> {ch}{RESET}")
            chapter_prev = ch

        lot        = r.get('lot') or ''
        pu_sec     = r['avg_pu_normalise'] or 0
        pu_26      = pu_sec * (1 + TAUX_INFLATION)
        coef_v     = COEF_CFO if lot == 'CFO' else COEF_CFA
        pu_vendu   = pu_26 * coef_v
        nb_occ     = r['nb_occurrences'] or 0

        if nb_occ <= 1:
            alerte_str = RED + "⚠ SOURCE UNIQUE" + RESET
        elif nb_occ < 3:
            alerte_str = ORANGE + "⚠ PRUDENCE" + RESET
        else:
            alerte_str = GREEN + "OK" + RESET

        desig = (r['designation'] or '')[:39]
        print(f"  {desig:<40} {lot:<5} {(r['unit'] or ''):>4} "
              f"{(r['ratio_type'] or ''):>10} "
              f"{fmt_eur(pu_sec):>12} {fmt_eur(pu_26):>12} {fmt_eur(pu_vendu):>12} "
              f"{nb_occ:>4}   {alerte_str}")


# ═════════════════════════════════════════════════════════════════════════════
# 6. EXPORT MARKDOWN
# ═════════════════════════════════════════════════════════════════════════════

def export_markdown(conn, ratios):
    os.makedirs(EXPORT_DIR, exist_ok=True)

    total   = ratios['total_projet']
    cfo     = ratios['total_cfo']
    cfa     = ratios['total_cfa']
    eclairage = ratios['eclairage_ht']
    vdi     = ratios['vdi_ht']
    nb_rj45 = ratios['nb_rj45']
    ssi     = ratios['ssi_ht']
    nb_dm   = ratios['nb_dm']
    td_moy  = ratios['td_moyenne']
    nb_td   = ratios['nb_td']

    def pu_row(label, pu_sec, lot, note=""):
        pu_26  = pu_sec * (1 + TAUX_INFLATION)
        coef_v = COEF_CFO if lot == 'CFO' else COEF_CFA
        pu_vdu = pu_26 * coef_v
        row = f"| {label} | {pu_sec:>10,.2f} € | {pu_26:>10,.2f} € | {pu_vdu:>10,.2f} € |"
        if note:
            row += f"\n> ⚠ {note}"
        return row

    pareto = get_pareto(conn, 20)
    all_ratios = get_all_ratios(conn)

    lines = [
        f"# Référentiel Ratios Électriques — PSA Urgences",
        f"",
        f"> Généré le {date.today().isoformat()} | Projet : PSA Urgences | SDO = {SDO:,.0f} m²",
        f"> Inflation appliquée : **+{TAUX_INFLATION*100:.1f}%** (base PSA 2026-04-10 → cible 2026)",
        f"> Coefficients de vente : CFO ×{COEF_CFO} | CFA ×{COEF_CFA}",
        f"> Source unique — alertes de rareté actives (1 seul projet en base)",
        f"",
        f"---",
        f"",
        f"## 1. Ratios Stratégiques Directeurs",
        f"",
        f"| Indicateur | Prix Sec | Prix Actualisé 2026 | Prix Vendu |",
        f"|---|---:|---:|---:|",
    ]

    # CFO/m²
    r_cfo_m2  = cfo / SDO
    r_cfo_kva = cfo / KVA_CIBLE
    r_ecl_m2  = eclairage / SDO
    lines.append(pu_row(f"**CFO Global** — €/m² SDO ({SDO:,.0f} m²)", r_cfo_m2, 'CFO'))
    lines.append(pu_row(f"**CFO Global** — €/kVA ({KVA_CIBLE:.0f} kVA — constante PSA)", r_cfo_kva, 'CFO'))
    lines.append(pu_row(f"**Éclairage** — €/m² SDO", r_ecl_m2, 'CFO'))

    if nb_rj45 > 0:
        r_vdi = vdi / nb_rj45
        lines.append(pu_row(f"**VDI** — €/Prise RJ45 ({nb_rj45} prises, infra seule)", r_vdi, 'CFA'))
    else:
        lines.append(f"| **VDI** — €/Prise RJ45 | N/D | N/D | N/D |")

    if nb_dm > 0:
        r_ssi = ssi / nb_dm
        lines.append(pu_row(
            f"**SSI** — €/DM ({nb_dm} DM — DA non individualisés dans ce devis)", r_ssi, 'CFA',
            note="Les Détecteurs Automatiques (DA) ne sont pas chiffrés unitairement — ratio à compléter sur projet multi-références"
        ))
    else:
        lines.append(f"| **SSI** — €/Point DA+DM | N/D | N/D | N/D |")

    lines.append(pu_row(f"**Tableau Divisionnaire** — €/u (moyenne {nb_td} TD)", td_moy, 'CFO'))

    lines += [
        f"",
        f"### Répartition du Projet",
        f"",
        f"| Lot | Montant HT | % du Total |",
        f"|---|---:|---:|",
        f"| CFO | {cfo:>12,.2f} € | {cfo/total*100:.1f}% |",
        f"| CFA | {cfa:>12,.2f} € | {cfa/total*100:.1f}% |",
        f"| Hors périmètre DPGF | {total-cfo-cfa:>12,.2f} € | {(total-cfo-cfa)/total*100:.1f}% |",
        f"| **TOTAL PROJET HT** | **{total:>12,.2f} €** | **100%** |",
        f"",
        f"---",
        f"",
        f"## 2. Pareto 80/20 — Top 20 Articles",
        f"",
        f"> Seuil 80% du projet = {total*0.8:,.2f} €",
        f"",
        f"| Rang | Désignation | Lot | Qté | U | PU HT | Total HT | Cumul% | Fiabilité |",
        f"|---:|---|---|---:|---|---:|---:|---:|---|",
    ]

    cumul = 0.0
    seuil_80 = total * 0.80
    for i, a in enumerate(pareto, 1):
        cumul += (a['total_ht'] or 0)
        pct = cumul / total * 100
        nb_occ = a.get('nb_occurrences') or 0

        if nb_occ <= 1:
            fiab = "🔴 SOURCE UNIQUE"
        elif nb_occ < 3:
            fiab = f"🟠 PRUDENCE ({nb_occ})"
        else:
            fiab = f"🟢 OK ({nb_occ})"

        marker = " **◄ 80%**" if cumul >= seuil_80 and (cumul - (a['total_ht'] or 0)) < seuil_80 else ""
        desig = (a['designation'] or '')
        lines.append(
            f"| {i} | {desig} | {a['lot'] or ''} | "
            f"{a['quantity'] or 0:.0f} | {a['unit'] or ''} | "
            f"{a['unit_price_ht'] or 0:,.2f} € | "
            f"{a['total_ht'] or 0:,.2f} € | "
            f"{pct:.1f}%{marker} | {fiab} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 3. Tableau Complet des Ratios Unitaires",
        f"",
        f"> 🔴 **SOURCE UNIQUE** = 1 seule référence — à confirmer  ",
        f"> 🟠 **PRUDENCE** = 2 références — tendance à valider  ",
        f"> 🟢 **OK** = 3 références ou plus",
        f"",
    ]

    chapter_prev = None
    for r in all_ratios:
        ch = r['chapter']
        if ch != chapter_prev:
            lines.append(f"\n### {ch}\n")
            lines.append(f"| Désignation | Lot | U | Type | Prix Sec | Prix 2026 | Prix Vendu | Occ | Alerte |")
            lines.append(f"|---|---|---|---|---:|---:|---:|---:|---|")
            chapter_prev = ch

        lot    = r.get('lot') or ''
        pu_sec = r['avg_pu_normalise'] or 0
        pu_26  = pu_sec * (1 + TAUX_INFLATION)
        coef_v = COEF_CFO if lot == 'CFO' else COEF_CFA
        pu_vdu = pu_26 * coef_v
        nb_occ = r['nb_occurrences'] or 0

        if nb_occ <= 1:
            alerte = "🔴 SOURCE UNIQUE"
        elif nb_occ < 3:
            alerte = f"🟠 PRUDENCE ({nb_occ})"
        else:
            alerte = f"🟢 OK ({nb_occ})"

        lines.append(
            f"| {r['designation'] or ''} | {lot} | {r['unit'] or ''} | "
            f"{r['ratio_type'] or ''} | "
            f"{pu_sec:,.2f} € | {pu_26:,.2f} € | {pu_vdu:,.2f} € | "
            f"{nb_occ} | {alerte} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 4. Notes Méthodologiques",
        f"",
        f"- **Prix Sec** : ratio brut extrait de la DPGF PSA (prix_normalise / coef_lot)",
        f"- **Prix Actualisé 2026** : Prix Sec × {1 + TAUX_INFLATION:.3f} (+{TAUX_INFLATION*100:.1f}%)",
        f"- **Prix Vendu** : Prix Actualisé × coef_vente (CFO ×{COEF_CFO} | CFA ×{COEF_CFA})",
        f"- **kVA** : constante PSA = {KVA_CIBLE:.0f} kVA (puissance cible non stockée en base)",
        f"- **VDI** : infrastructure seule (précâblage, baies, jarretières) / nb prises terminales",
        f"- **SSI** : ratio calculé sur Déclencheurs Manuels (DM) uniquement — DA non individualisés",
        f"- Toutes les alertes 'SOURCE UNIQUE' sont normales (1 seul projet en base à ce stade)",
        f"",
        f"*Référentiel Branche Sud Egis — Usage interne confidentiel*",
    ]

    with open(EXPORT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"\n  {GREEN}✓ Export Markdown : {EXPORT_FILE}{RESET}")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Rapport des ratios stratégiques Estimation Élec")
    parser.add_argument('--lot', choices=['CFO', 'CFA', 'ALL'], default='ALL',
                        help="Filtrer le tableau des ratios par lot")
    parser.add_argument('--no-export', action='store_true',
                        help="Ne pas générer le fichier Markdown")
    args = parser.parse_args()

    conn = connect()

    ratios = compute_strategic_ratios(conn)

    display_strategic_ratios(ratios)
    display_pareto(conn, ratios)
    display_ratios_table(conn, args.lot)

    if not args.no_export:
        export_markdown(conn, ratios)

    conn.close()


if __name__ == '__main__':
    main()
