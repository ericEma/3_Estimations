"""
engine_matching.py — Moteur de parsing sémantique (Lot 2)

Responsabilités :
  1. Nettoyage des désignations (suppression expressions parasites)
  2. Normalisation via la table synonyms
  3. Fuzzy matching cloisonné par lot (CFO / CFA / PV)
  4. Préparation des candidats pour l'interface de revue (Lot 3)
"""

import re
import sqlite3
from typing import Optional

try:
    from rapidfuzz import fuzz, process as rfprocess
    _RAPIDFUZZ_OK = True
except ImportError:
    _RAPIDFUZZ_OK = False


# ─── Expressions parasites à ignorer ─────────────────────────────────────────
# Chaque entrée est une chaîne normalisée en minuscules, sans ponctuation finale.

NOISE_PHRASES = [
    r"fourniture et pose d[e']?",
    r"fourniture[,]? pose et raccordement",
    r"fourniture[,]? pose",
    r"mise en service(?:\s+d[e'])?",
    r"mise en oeuvre(?:\s+d[e'])?",
    r"mise en œuvre(?:\s+d[e'])?",
    r"compris fourniture et pose",
    r"y[\s./]*compris[\s:]*",
    r"\by[./]c[./]?",
    r"installation(?:\s+d[e'])?",
    r"livraison et installation",
    r"fourniture\b",
    r"pose\b",
    r"raccordement\b",
    r"\bincluant\b",
    r"\bcomprenant\b",
]

_NOISE_RE = re.compile(
    r"(?i)\b(?:" + "|".join(NOISE_PHRASES) + r")[\s,;:]*"
)

# Caractères parasites résiduels en début / fin
_TRIM_RE = re.compile(r"^[\s,;:\-–—]+|[\s,;:\-–—]+$")


# ─── Nettoyage ────────────────────────────────────────────────────────────────

def clean_designation(text: str) -> str:
    """
    Supprime les expressions parasites d'une désignation de devis.

    Exemples :
      "Fourniture et pose de câble U-1000" → "câble U-1000"
      "Mise en service onduleur 10 kVA"    → "onduleur 10 kVA"
    """
    if not text:
        return ""
    cleaned = _NOISE_RE.sub(" ", text)
    cleaned = _TRIM_RE.sub("", cleaned)
    # Normalise les espaces multiples
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


# ─── Normalisation via synonymes ──────────────────────────────────────────────

def normalize_with_synonyms(conn: sqlite3.Connection, text: str) -> str:
    """
    Remplace les termes techniques selon la table synonyms.

    La table synonyms stocke (original_term, mapped_term).
    Substitutions appliquées dans l'ordre d'insertion (longest-match implicite
    si les termes ne se chevauchent pas).
    """
    if not text:
        return text

    rows = conn.execute(
        "SELECT original_term, mapped_term FROM synonyms ORDER BY LENGTH(original_term) DESC"
    ).fetchall()

    result = text
    for original, mapped in rows:
        pattern = re.compile(re.escape(original), re.IGNORECASE)
        result = pattern.sub(mapped, result)
    return result


# ─── Matching principal ───────────────────────────────────────────────────────

def _load_lot_articles(conn: sqlite3.Connection, lot: str) -> list[dict]:
    """Charge tous les articles actifs du lot depuis la bibliothèque."""
    rows = conn.execute(
        """SELECT id, designation, unit, pu_ht_ref, section, chapter
           FROM dpgf_articles
           WHERE lot = ?
             AND row_type = 'article'
             AND (is_hidden IS NULL OR is_hidden = 0)
           ORDER BY id""",
        (lot,),
    ).fetchall()
    return [dict(r) for r in rows]


def _check_mapping_knowledge(
    conn: sqlite3.Connection, clean_text: str, lot: str
) -> Optional[dict]:
    """
    Consulte mapping_knowledge pour un match exact déjà connu.
    Retourne l'article si trouvé et valide pour ce lot, None sinon.
    """
    row = conn.execute(
        """SELECT mk.dpgf_article_id, da.designation, da.unit, da.pu_ht_ref,
                  mk.occurrence_count
           FROM mapping_knowledge mk
           JOIN dpgf_articles da ON mk.dpgf_article_id = da.id
           WHERE LOWER(mk.source_designation) = LOWER(?)
             AND da.lot = ?
             AND (da.is_hidden IS NULL OR da.is_hidden = 0)
           ORDER BY mk.occurrence_count DESC
           LIMIT 1""",
        (clean_text, lot),
    ).fetchone()
    if row:
        return {
            'article_id':  row[0],
            'designation': row[1],
            'unit':        row[2],
            'pu_ht_ref':   row[3],
            'score':       100.0,
            'match_type':  'knowledge',
            'occurrence':  row[4],
        }
    return None


def find_best_match(
    conn: sqlite3.Connection,
    designation: str,
    lot: str,
    top_n: int = 5,
) -> list[dict]:
    """
    Recherche les meilleures correspondances pour une désignation de devis
    dans le lot donné (CFO, CFA ou PV). Cloisonnement strict : un article
    d'un autre lot ne peut jamais être retourné.

    Pipeline :
      1. Nettoyage de la désignation source
      2. Normalisation via synonyms
      3. Consultation mapping_knowledge (cache des choix validés)
      4. Fuzzy matching (rapidfuzz) sur le corpus du lot

    Retourne une liste de dicts triée par score décroissant :
    [
      {
        'article_id'  : int,
        'designation' : str,   # désignation bibliothèque
        'unit'        : str,
        'pu_ht_ref'   : float,
        'score'       : float, # 0–100
        'match_type'  : str,   # 'knowledge' | 'fuzzy' | 'fallback'
      },
      ...
    ]
    """
    lot = (lot or "CFO").upper()

    # ── Nettoyage + normalisation ─────────────────────────────────────────────
    cleaned   = clean_designation(designation)
    normalized = normalize_with_synonyms(conn, cleaned)

    # ── Cache mapping_knowledge ───────────────────────────────────────────────
    known = _check_mapping_knowledge(conn, normalized, lot)
    if known and known['score'] == 100.0:
        # Match parfait connu → on le retourne en tête, complété par fuzzy
        exact = [known]
    else:
        exact = []

    # ── Corpus du lot ─────────────────────────────────────────────────────────
    articles = _load_lot_articles(conn, lot)
    if not articles:
        return exact

    if not _RAPIDFUZZ_OK:
        # Fallback sans rapidfuzz : retourne les N premiers du lot
        fallback = [
            {
                'article_id':  a['id'],
                'designation': a['designation'],
                'unit':        a['unit'],
                'pu_ht_ref':   a['pu_ht_ref'] or 0.0,
                'score':       0.0,
                'match_type':  'fallback',
            }
            for a in articles[:top_n]
        ]
        return exact + fallback

    # ── Fuzzy matching ────────────────────────────────────────────────────────
    choices     = {a['id']: a['designation'] for a in articles}
    art_by_id   = {a['id']: a for a in articles}

    # WRatio est un scorer hybride robuste (token_set + token_sort + ratio)
    raw_matches = rfprocess.extract(
        normalized,
        choices,
        scorer=fuzz.WRatio,
        limit=top_n + len(exact),    # surcharge légère pour éliminer les doublons
        score_cutoff=30,
    )

    fuzzy_results = []
    seen_ids = {e['article_id'] for e in exact}

    for _matched_val, score, art_id in raw_matches:
        if art_id in seen_ids:
            continue
        a = art_by_id[art_id]
        fuzzy_results.append({
            'article_id':  a['id'],
            'designation': a['designation'],
            'unit':        a['unit'] or '',
            'pu_ht_ref':   a['pu_ht_ref'] or 0.0,
            'score':       round(score, 1),
            'match_type':  'fuzzy',
        })
        seen_ids.add(art_id)

    combined = (exact + fuzzy_results)[:top_n]
    return combined


# ─── Interface de revue (prépare les données pour Lot 3) ─────────────────────

def prepare_review_candidates(
    conn: sqlite3.Connection,
    devis_lines: list[dict],
    top_n: int = 5,
) -> list[dict]:
    """
    Prend une liste de lignes de devis et retourne, pour chacune, les
    top_n candidats de correspondance ainsi que les métadonnées de revue.

    Paramètre devis_lines : liste de dicts avec au minimum :
      { 'id', 'original_designation', 'lot', 'unit_price_ht', 'devis_date' }

    Retourne une liste de dicts :
    {
        'devis_line_id'    : int,
        'original'         : str,   # désignation brute du devis
        'cleaned'          : str,   # désignation nettoyée
        'lot'              : str,
        'devis_price'      : float,
        'devis_date'       : str,
        'candidates'       : [ {article_id, designation, unit, pu_ht_ref, score, match_type}, ...],
        'auto_match'       : dict | None,  # meilleur candidat si score >= 80
        'needs_review'     : bool,
    }
    """
    results = []
    for line in devis_lines:
        lot       = (line.get('lot') or 'CFO').upper()
        raw_desig = line.get('original_designation') or ''
        cleaned   = clean_designation(raw_desig)
        candidates = find_best_match(conn, raw_desig, lot, top_n=top_n)

        top        = candidates[0] if candidates else None
        auto_match = top if (top and top['score'] >= 80.0) else None

        results.append({
            'devis_line_id': line.get('id'),
            'original':      raw_desig,
            'cleaned':       cleaned,
            'lot':           lot,
            'devis_price':   line.get('unit_price_ht') or 0.0,
            'devis_date':    line.get('devis_date'),
            'candidates':    candidates,
            'auto_match':    auto_match,
            'needs_review':  auto_match is None,
        })

    return results
