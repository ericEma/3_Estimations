"""
engine_ratios.py — Moteur de calcul des ratios de référence
Sprint 4 : Intelligence Métier

Calcule la moyenne glissante temporelle pondérée par :
  1. Ancienneté (paliers annuels : année courante = 1.0, -1an = 0.8, ...)
  2. Complexité du projet source (coef_lot → normalisation à complexité 1.0)
  3. Actualisation inflation vers l'année de référence

Usage standalone :
    python scripts/engine_ratios.py --sdo 3000
"""

import sqlite3
import sys
import os
from datetime import date

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, "estimation_elec.db")

# Poids temporels par ancienneté (en années depuis aujourd'hui)
POIDS_ANNUELS = {0: 1.0, 1: 0.85, 2: 0.65, 3: 0.45, 4: 0.25}
POIDS_DEFAUT  = 0.10   # > 4 ans

# Inflation annuelle cible (si non spécifié dans projects)
TAUX_INFLATION_DEFAUT = 0.03

ANNEE_REFERENCE = int(date.today().strftime("%Y"))


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_annee_reference():
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT value FROM config WHERE key='annee_reference'"
        ).fetchone()
        return int(r['value']) if r else ANNEE_REFERENCE
    finally:
        conn.close()


def _poids_temporal(devis_date_str: str, ref_year: int) -> float:
    """Retourne le poids temporel selon l'écart entre l'année du devis et ref_year."""
    try:
        annee_devis = int(str(devis_date_str)[:4])
    except (ValueError, TypeError):
        return POIDS_DEFAUT
    delta = ref_year - annee_devis
    if delta < 0:
        delta = 0   # devis futur ou même année → poids max
    return POIDS_ANNUELS.get(delta, POIDS_DEFAUT)


def _actualiser(prix: float, taux_inflation: float, annee_devis: int, annee_ref: int) -> float:
    """Actualise un prix de annee_devis vers annee_ref."""
    nb_ans = annee_ref - annee_devis
    return prix * ((1.0 + taux_inflation) ** nb_ans)


def compute_ratios(
    target_sdo: float = 1000.0,
    target_complexity_cfo: float = 1.0,
    target_complexity_cfa: float = 1.0,
    target_complexity_pv: float = 1.0,
    ref_year: int | None = None,
) -> dict:
    """
    Calcule les ratios de référence pour tous les articles DPGF.

    Retourne un dict[dpgf_article_id] = {
        'avg_pu_sec':        float,   # prix normalisé Complexité 1 (moyenne glissante)
        'avg_pu_actualise':  float,   # prix actualisé vers ref_year
        'avg_pu_cible':      float,   # prix pour complexité cible (×coef_lot_cible)
        'qty_estimee':       float,   # quantité estimée pour target_sdo
        'total_estime':      float,   # total_ht estimé pour cette ligne
        'nb_refs':           int,
        'fiabilite':         str,     # 'OK' | 'PRUDENCE' | 'SOURCE_UNIQUE' | 'AUCUNE_REF'
        'pu_min':            float,
        'pu_max':            float,
        'ratio_type':        str,
        'lot':               str,
    }
    """
    if ref_year is None:
        ref_year = get_annee_reference()

    conn = _connect()
    try:
        # Récupère toutes les lignes mappées avec leur projet
        rows = conn.execute("""
            SELECT
                dl.dpgf_article_id,
                dl.prix_normalise,
                dl.quantity,
                dl.lot,
                da.ratio_type,
                p.devis_date,
                p.taux_inflation,
                p.coef_cfo,
                p.coef_cfa,
                p.coef_pv,
                p.surface_sdo
            FROM devis_lines dl
            JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
            JOIN projects p ON dl.project_id = p.id
            WHERE dl.mapping_status IN ('auto', 'manual')
              AND dl.row_type = 'article'
              AND dl.prix_normalise IS NOT NULL
              AND dl.prix_normalise > 0
              AND dl.quantity IS NOT NULL
              AND p.surface_sdo > 0
        """).fetchall()

        # Groupement par article
        from collections import defaultdict
        by_article = defaultdict(list)
        for r in rows:
            by_article[r['dpgf_article_id']].append(dict(r))

        # Récupère les overrides manuels de prix
        overrides = {}
        try:
            for r in conn.execute(
                "SELECT dpgf_article_id, pu_override FROM ratio_overrides ORDER BY created_at DESC"
            ).fetchall():
                if r['dpgf_article_id'] not in overrides:
                    overrides[r['dpgf_article_id']] = r['pu_override']
        except sqlite3.OperationalError:
            pass  # table pas encore créée

        result = {}

        for art_id, lignes in by_article.items():
            lot = lignes[0]['lot'] or 'CFO'
            ratio_type = lignes[0]['ratio_type']

            # Complexité cible pour ce lot
            coef_cible = {
                'CFO': target_complexity_cfo,
                'CFA': target_complexity_cfa,
                'PV':  target_complexity_pv,
            }.get(lot, 1.0)

            # Calcul de la moyenne pondérée
            somme_pond  = 0.0
            somme_poids = 0.0
            prix_list   = []
            densite_list = []

            for ligne in lignes:
                devis_date = ligne['devis_date'] or str(ref_year)
                annee_devis = int(str(devis_date)[:4])
                taux       = ligne['taux_inflation'] or TAUX_INFLATION_DEFAUT

                # Normalisation complexité source → Complexité 1
                # prix_normalise = unit_price_ht / coef_lot (déjà fait à l'import)
                # Pas de re-normalisation nécessaire ici

                # Actualisation
                pu_act = _actualiser(ligne['prix_normalise'], taux, annee_devis, ref_year)

                # Poids temporel
                poids = _poids_temporal(devis_date, ref_year)

                somme_pond  += pu_act * poids
                somme_poids += poids
                prix_list.append(pu_act)

                # Densité quantité / SDO (pour estimer la quantité sur un nouveau projet)
                if ligne['surface_sdo'] and ligne['quantity']:
                    densite_list.append(ligne['quantity'] / ligne['surface_sdo'])

            if somme_poids == 0:
                continue

            avg_pu_actualise = somme_pond / somme_poids
            avg_pu_sec       = avg_pu_actualise / ((1 + TAUX_INFLATION_DEFAUT) ** (ref_year - ANNEE_REFERENCE + 1)) \
                                if ref_year != ANNEE_REFERENCE else avg_pu_actualise

            # Application de l'override si présent
            if art_id in overrides:
                avg_pu_actualise = overrides[art_id]

            # Prix pour la complexité cible
            avg_pu_cible = avg_pu_actualise * coef_cible

            # Estimation quantité
            if densite_list:
                densite_avg = sum(densite_list) / len(densite_list)
            else:
                densite_avg = 1.0 / max(target_sdo, 1)   # fallback

            if ratio_type == 'SURFACIQUE':
                # Pour SURFACIQUE : total_estime = avg_pu_cible * target_sdo / SDO_ref
                # avg_pu_cible est le prix unitaire pour 1 ens (= total pour la surface)
                # On recalcule : ratio_m2 = avg_pu_cible / SDO_source → total = ratio_m2 * target_sdo
                # En pratique : avg_pu_cible est déjà normalisé (prix_normalise / coef_lot)
                # Donc total = avg_pu_cible * (target_sdo / ref_sdo_avg)
                ref_sdo_avg = sum(l['surface_sdo'] for l in lignes) / len(lignes)
                qty_estimee = 1.0
                unit_price  = avg_pu_cible * (target_sdo / ref_sdo_avg)
            else:
                # UNITAIRE : quantité estimée = densité × SDO cible
                qty_estimee = round(densite_avg * target_sdo, 1)
                unit_price  = avg_pu_cible

            total_estime = qty_estimee * unit_price

            # Fiabilité
            nb = len(lignes)
            if nb == 0:
                fiabilite = 'AUCUNE_REF'
            elif nb == 1:
                fiabilite = 'SOURCE_UNIQUE'
            elif nb < 3:
                fiabilite = 'PRUDENCE'
            else:
                fiabilite = 'OK'

            result[art_id] = {
                'avg_pu_sec':       round(avg_pu_sec, 2),
                'avg_pu_actualise': round(avg_pu_actualise, 2),
                'avg_pu_cible':     round(avg_pu_cible, 2),
                'qty_estimee':      qty_estimee,
                'unit_price':       round(unit_price, 2),
                'total_estime':     round(total_estime, 2),
                'nb_refs':          nb,
                'fiabilite':        fiabilite,
                'pu_min':           round(min(prix_list), 2),
                'pu_max':           round(max(prix_list), 2),
                'ratio_type':       ratio_type,
                'lot':              lot,
            }

        return result

    finally:
        conn.close()


def get_dpgf_tree_with_ratios(
    target_sdo: float = 1000.0,
    target_complexity_cfo: float = 1.0,
    target_complexity_cfa: float = 1.0,
    target_complexity_pv: float = 1.0,
) -> list:
    """
    Retourne l'arborescence DPGF complète avec les ratios pré-calculés.

    Structure retournée :
    [
      {
        'id': ..., 'designation': ..., 'type': 'chapter',
        'sections': [
          {
            'id': ..., 'designation': ..., 'type': 'section',
            'articles': [
              {
                'id': ..., 'designation': ..., 'unit': ..., 'ratio_type': ...,
                'lot': ..., 'qty': ..., 'unit_price': ..., 'total': ...,
                'nb_refs': ..., 'fiabilite': ...,
                'avg_pu_sec': ..., 'avg_pu_actualise': ...,
                'is_included': bool,
              }, ...
            ]
          }, ...
        ]
      }, ...
    ]
    """
    ratios = compute_ratios(
        target_sdo, target_complexity_cfo, target_complexity_cfa, target_complexity_pv
    )

    conn = _connect()
    try:
        # Charge tout le référentiel. Le modèle stocke les 285 articles dans
        # dpgf_articles, avec les colonnes TEXTE `chapter`/`section`/`chapter_num`
        # qui définissent la hiérarchie (il n'y a pas de lignes `row_type=chapter`
        # dans cette table — on doit grouper à la volée).
        all_rows = conn.execute("""
            SELECT id, code, designation, unit, chapter, chapter_num, section,
                   ratio_type, row_order
            FROM dpgf_articles
            WHERE row_type = 'article'
            ORDER BY row_order, id
        """).fetchall()

        # Détermine le lot de chaque article depuis les données de mapping
        article_lots = {}
        for r in conn.execute("""
            SELECT DISTINCT dpgf_article_id, lot FROM devis_lines
            WHERE lot IS NOT NULL AND row_type='article'
        """).fetchall():
            article_lots[r['dpgf_article_id']] = r['lot']

        # Groupe les articles par (chapter, section) en préservant l'ordre
        # d'apparition. Les IDs chapter/section sont dérivés du row_order du
        # premier article rencontré → stables entre deux rendus.
        chapters = []
        chap_index   = {}   # chapter_key    → index dans chapters
        sect_index   = {}   # (chap, sect)   → index dans chapter.sections

        def _derive_lot(chapter_txt: str) -> str:
            c = (chapter_txt or '').lower()
            if 'faible' in c or 'cfa' in c or 'ssi' in c:
                return 'CFA'
            if 'photovolt' in c or 'pv' in c:
                return 'PV'
            return 'CFO'

        for idx, row in enumerate(all_rows):
            row = dict(row)
            chap_txt = row['chapter']    or '— Sans chapitre —'
            sect_txt = row['section']    or '—'
            chap_num = row['chapter_num'] or ''
            chap_label = f"{chap_num} {chap_txt}".strip() if chap_num else chap_txt

            if chap_txt not in chap_index:
                chap_index[chap_txt] = len(chapters)
                chapters.append({
                    'id':          10000 + len(chapters),   # id synthétique stable
                    'designation': chap_label,
                    'type':        'chapter',
                    'sections':    [],
                })

            chapter = chapters[chap_index[chap_txt]]

            sect_key = (chap_txt, sect_txt)
            if sect_key not in sect_index:
                sect_index[sect_key] = len(chapter['sections'])
                chapter['sections'].append({
                    'id':          20000 + len(sect_index),
                    'designation': sect_txt,
                    'type':        'section',
                    'articles':    [],
                })
            section = chapter['sections'][sect_index[sect_key]]

            art_id = row['id']
            ratio  = ratios.get(art_id, {})
            lot    = article_lots.get(art_id) or _derive_lot(chap_txt)

            section['articles'].append({
                'id':               art_id,
                'code':             row['code'],
                'designation':      row['designation'],
                'unit':             row['unit'] or 'u',
                'ratio_type':       row['ratio_type'],
                'lot':              lot,
                'qty':              ratio.get('qty_estimee', 0),
                'unit_price':       ratio.get('unit_price', 0),
                'total':            ratio.get('total_estime', 0),
                'nb_refs':          ratio.get('nb_refs', 0),
                'fiabilite':        ratio.get('fiabilite', 'AUCUNE_REF'),
                'avg_pu_sec':       ratio.get('avg_pu_sec', 0),
                'avg_pu_actualise': ratio.get('avg_pu_actualise', 0),
                'pu_min':           ratio.get('pu_min', 0),
                'pu_max':           ratio.get('pu_max', 0),
                # Toutes les lignes du référentiel sont visibles par défaut ;
                # l'utilisateur choisit d'exclure via la checkbox.
                'is_included':      True,
            })

        return chapters

    finally:
        conn.close()


if __name__ == '__main__':
    import argparse, json

    parser = argparse.ArgumentParser(description="Engine Ratios — Estimation Élec")
    parser.add_argument('--sdo',  type=float, default=1000, help="Surface SDO m2")
    parser.add_argument('--ccfo', type=float, default=1.0,  help="Complexité CFO cible")
    parser.add_argument('--ccfa', type=float, default=1.0,  help="Complexité CFA cible")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ratios = compute_ratios(args.sdo, args.ccfo, args.ccfa)
    print(f"Articles avec ratio : {len(ratios)}")
    for aid, r in list(ratios.items())[:5]:
        print(f"  [{aid}] pu_act={r['avg_pu_actualise']:.2f} nb={r['nb_refs']} fiab={r['fiabilite']}")
