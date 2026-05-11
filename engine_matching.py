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
import unicodedata
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

# ─── Stop-words & hygiène Unicode ────────────────────────────────────────────

STOP_WORDS = {
    "et", "le", "la", "les", "des", "du", "un", "une", "pour", "avec",
    "yc", "y.c",
}

# Supprime les caractères invisibles qui cassent l'affichage / la comparaison
_INVISIBLE_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2060\uFEFF]")

_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", re.UNICODE)


def _sanitize_text(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = _INVISIBLE_RE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def _strip_stop_words(text: str) -> str:
    """Retire les stop-words du texte (conserve l'ordre)."""
    if not text:
        return ""
    tokens = _TOKEN_RE.findall(text)
    kept = []
    for tok in tokens:
        low = tok.lower()
        if low in STOP_WORDS:
            continue
        kept.append(tok)
    return " ".join(kept)


def _token_set(text: str) -> set[str]:
    """Tokens utiles (sans stop-words)."""
    if not text:
        return set()
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    return {t for t in tokens if t and t not in STOP_WORDS}


def _keyword_bonus(query_tokens: set[str], cand_tokens: set[str]) -> float:
    """Boost simple si mots-clés communs (noms techniques)."""
    if not query_tokens or not cand_tokens:
        return 0.0
    common = query_tokens & cand_tokens
    if not common:
        return 0.0
    # Bonus fort dès le 1er mot-clé, puis bonus marginal limité
    return min(12.0, 8.0 + 2.0 * max(0, len(common) - 1))


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
    text = _sanitize_text(text)
    cleaned = _NOISE_RE.sub(" ", text)
    cleaned = _TRIM_RE.sub("", cleaned)
    # Normalise les espaces multiples
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def clean_designation_radical(text: str) -> str:
    """Nettoyage + suppression stop-words (pour le matching et l'UI)."""
    return _strip_stop_words(clean_designation(text))


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

def _article_display_path(chapter: Optional[str], section: Optional[str], designation: str) -> str:
    parts = [p.strip() for p in [chapter or "", section or "", designation or ""] if p and p.strip()]
    return " ➔ ".join(parts) if parts else (designation or "")


def _article_breadcrumb(chapter: Optional[str], section: Optional[str]) -> str:
    parts = [p.strip() for p in [chapter or "", section or ""] if p and p.strip()]
    return " ➔ ".join(parts)


def _loc_tier(
    art_chapter: Optional[str],
    art_section: Optional[str],
    devis_chapter: Optional[str],
    devis_section: Optional[str],
) -> int:
    """0 = même section (sous-chapitre), 1 = même chapitre seulement, 2 = reste du lot."""
    ds = (devis_section or "").strip().lower()
    dc = (devis_chapter or "").strip().lower()
    ac = (art_chapter or "").strip().lower()
    asec = (art_section or "").strip().lower()
    if ds and asec == ds:
        return 0
    if dc and ac == dc:
        return 1
    return 2


def _load_lot_articles(conn: sqlite3.Connection, lot: str) -> list[dict]:
    """Charge tous les articles actifs du lot depuis la bibliothèque."""
    rows = conn.execute(
        """SELECT id, designation, unit, pu_ht_ref, section, chapter, lot
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
                  mk.occurrence_count, da.chapter, da.section, da.lot
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
        des = row[1]
        ch, sec, alot = row[5], row[6], row[7]
        return {
            'article_id':  row[0],
            'designation': des,
            'unit':        row[2],
            'pu_ht_ref':   row[3],
            'score':       100.0,
            'match_type':  'knowledge',
            'occurrence':  row[4],
            'chapter':     ch,
            'section':     sec,
            'lot':         (alot or lot).upper(),
            'breadcrumb':  _article_breadcrumb(ch, sec),
            'path':        _article_display_path(ch, sec, des),
        }
    return None


def find_best_match(
    conn: sqlite3.Connection,
    designation: str,
    lot: str,
    top_n: int = 5,
    *,
    devis_chapter: Optional[str] = None,
    devis_section: Optional[str] = None,
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

    Classement : d'abord les articles du même sous-chapitre (section) que le
    devis, puis même chapitre DPGF, puis reste du lot ; à égalité de niveau,
    score fuzzy décroissant. Chaque entrée peut inclure ``path`` (chapitre >
    section > désignation).

    Retourne une liste de dicts :
    [
      {
        'article_id'  : int,
        'designation' : str,
        'unit'        : str,
        'pu_ht_ref'   : float,
        'score'       : float,
        'match_type'  : str,
        'chapter'     : str | None,
        'section'     : str | None,
        'path'        : str,
      },
      ...
    ]
    """
    lot = (lot or "CFO").upper()
    dch = (devis_chapter or "").strip() or None
    dsec = (devis_section or "").strip() or None

    # ── Nettoyage + normalisation ─────────────────────────────────────────────
    cleaned   = clean_designation(designation)
    normalized = normalize_with_synonyms(conn, cleaned)
    normalized = _sanitize_text(normalized)
    normalized = _strip_stop_words(normalized)
    q_tokens   = _token_set(normalized)

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
        fallback = []
        for a in articles[:top_n]:
            des = a['designation']
            ch, sec = a.get('chapter'), a.get('section')
            fallback.append({
                'article_id':  a['id'],
                'designation': des,
                'unit':        a['unit'],
                'pu_ht_ref':   a['pu_ht_ref'] or 0.0,
                'score':       0.0,
                'match_type':  'fallback',
                'chapter':     ch,
                'section':     sec,
                'lot':         (a.get('lot') or lot).upper(),
                'breadcrumb':  _article_breadcrumb(ch, sec),
                'path':        _article_display_path(ch, sec, des),
            })
        return exact + fallback

    # ── Fuzzy matching (lot entier + priorisation section / chapitre devis) ───
    choices_all = {a['id']: a['designation'] for a in articles}
    art_by_id   = {a['id']: a for a in articles}

    ds_lower = (dsec or "").strip().lower()
    articles_same_section = [
        a for a in articles
        if ds_lower and (a.get('section') or '').strip().lower() == ds_lower
    ]
    choices_sec = {a['id']: a['designation'] for a in articles_same_section}

    # Même sous-chapitre d'abord (extract dédié), puis lot élargi pour compléter le pool
    lim_sec = max(top_n * 3, 15)
    lim_all = max(top_n * 8, 50)
    raw_sec = (
        rfprocess.extract(
            normalized,
            choices_sec,
            scorer=fuzz.WRatio,
            limit=lim_sec,
            score_cutoff=30,
        )
        if choices_sec
        else []
    )
    raw_all = rfprocess.extract(
        normalized,
        choices_all,
        scorer=fuzz.WRatio,
        limit=lim_all,
        score_cutoff=30,
    )

    pool_scores: dict[int, float] = {}
    for _mv, score, art_id in raw_sec:
        pool_scores[art_id] = max(pool_scores.get(art_id, 0.0), float(score))
    for _mv, score, art_id in raw_all:
        pool_scores[art_id] = max(pool_scores.get(art_id, 0.0), float(score))

    # Boost mots-clés techniques (présence immédiate)
    boosted_scores: dict[int, float] = {}
    for aid, base in pool_scores.items():
        cand = art_by_id.get(aid) or {}
        cand_tokens = _token_set(_sanitize_text(cand.get("designation") or ""))
        bonus = _keyword_bonus(q_tokens, cand_tokens)
        boosted_scores[aid] = min(100.0, float(base) + float(bonus))

    seen_ids = {e['article_id'] for e in exact}
    ranked_ids = sorted(
        pool_scores.keys(),
        key=lambda aid: (
            _loc_tier(
                art_by_id[aid].get('chapter'),
                art_by_id[aid].get('section'),
                dch,
                dsec,
            ),
            -boosted_scores.get(aid, pool_scores[aid]),
        ),
    )

    fuzzy_slots = max(0, top_n - len(exact))
    fuzzy_results = []
    for art_id in ranked_ids:
        if art_id in seen_ids:
            continue
        a = art_by_id[art_id]
        des = a['designation']
        ch, sec = a.get('chapter'), a.get('section')
        fuzzy_results.append({
            'article_id':  art_id,
            'designation': des,
            'unit':        a['unit'] or '',
            'pu_ht_ref':   a['pu_ht_ref'] or 0.0,
            'score':       round(boosted_scores.get(art_id, pool_scores[art_id]), 1),
            'match_type':  'fuzzy',
            'chapter':     ch,
            'section':     sec,
            'lot':         (a.get('lot') or lot).upper(),
            'breadcrumb':  _article_breadcrumb(ch, sec),
            'path':        _article_display_path(ch, sec, des),
        })
        seen_ids.add(art_id)
        if len(fuzzy_results) >= fuzzy_slots:
            break

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
        cleaned   = clean_designation_radical(raw_desig)
        ctx       = (line.get('context_path') or '').split(' > ')
        dch       = ctx[0].strip() if ctx else ''
        dsec      = ctx[1].strip() if len(ctx) > 1 else ''
        candidates = find_best_match(
            conn,
            raw_desig,
            lot,
            top_n=top_n,
            devis_chapter=dch or None,
            devis_section=dsec or None,
        )

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
