"""
utils.py — Moteur de pondération temporelle (Lot 2)

Source de vérité unique pour le calcul des prix pondérés.
Toute la logique d'actualisation et de confiance est ici.
"""

import sqlite3
from datetime import date, datetime
from typing import Optional


# ─── Constantes ───────────────────────────────────────────────────────────────

TAUX_ACTUALISATION = 0.03   # 3 % / an

POIDS_HAUTE_CONFIANCE = 4   # âge < 1 an
POIDS_BASSE_CONFIANCE = 1   # âge > 2 ans
# Entre 1 et 2 ans : interpolation linéaire (4 → 1)


# ─── Helpers internes ─────────────────────────────────────────────────────────

def _parse_date(value) -> Optional[date]:
    """Convertit str ISO ou date en objet date. Retourne None si invalide."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _age_years(price_date: date, reference: date) -> float:
    """Âge en années (flottant) depuis price_date jusqu'à reference."""
    delta = (reference - price_date).days
    return max(delta / 365.25, 0.0)


def _actualize(price: float, price_date: date, reference: date) -> float:
    """
    Actualise un prix de price_date vers reference au taux de 3 % / an.

    Prix_actualisé = prix × 1.03 ^ n   (n = âge en années)
    """
    n = _age_years(price_date, reference)
    return price * (1.0 + TAUX_ACTUALISATION) ** n


def _weight(age_years: float) -> float:
    """
    Calcule le poids de confiance en fonction de l'âge du prix.

    < 1 an  → 4  (haute confiance)
    > 2 ans → 1  (basse confiance)
    1–2 ans → interpolation linéaire entre 4 et 1
    """
    if age_years < 1.0:
        return float(POIDS_HAUTE_CONFIANCE)
    if age_years > 2.0:
        return float(POIDS_BASSE_CONFIANCE)
    # Interpolation : à 1 an → 4, à 2 ans → 1  (pente -3 sur 1 an)
    return 4.0 - (age_years - 1.0) * 3.0


# ─── Date Pivot ───────────────────────────────────────────────────────────────

def get_effective_date(conn: sqlite3.Connection, article_id: int) -> Optional[str]:
    """
    Retourne la date effective d'un article selon la règle Date Pivot :
      1. last_updated propre à l'article  → priorité absolue
      2. MAX(last_updated) de la section  → date pivot section
      3. MAX(last_updated) du chapitre    → date pivot chapitre
      4. None si aucune date disponible
    """
    row = conn.execute(
        "SELECT chapter, section, last_updated FROM dpgf_articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        return None

    if row[2]:
        return row[2]

    chapter, section = row[0], row[1]

    if section:
        pivot = conn.execute(
            """SELECT MAX(last_updated)
               FROM dpgf_articles
               WHERE chapter = ? AND section = ? AND last_updated IS NOT NULL""",
            (chapter, section),
        ).fetchone()[0]
        if pivot:
            return pivot

    if chapter:
        pivot = conn.execute(
            """SELECT MAX(last_updated)
               FROM dpgf_articles
               WHERE chapter = ? AND last_updated IS NOT NULL""",
            (chapter,),
        ).fetchone()[0]
        if pivot:
            return pivot

    return None


# ─── Fonction principale ───────────────────────────────────────────────────────

def calculate_weighted_price(
    base_price:  Optional[float],
    base_date,
    devis_price: Optional[float],
    devis_date,
    today=None,
) -> dict:
    """
    Calcule le prix pondéré combinant la bibliothèque de base et un devis importé.

    Paramètres
    ----------
    base_price  : prix unitaire HT de la bibliothèque (peut être None/0)
    base_date   : date associée au prix de la bibliothèque (str ISO ou date)
    devis_price : prix unitaire HT issu du devis importé (peut être None/0)
    devis_date  : date associée au devis importé (str ISO ou date)
    today       : date de référence pour l'actualisation (défaut : date du jour)

    Retourne un dict :
    {
        'weighted_price'      : float | None,  # résultat final
        'base_actualized'     : float | None,  # prix base actualisé
        'base_weight'         : float | None,
        'base_age_years'      : float | None,
        'devis_actualized'    : float | None,  # prix devis actualisé
        'devis_weight'        : float | None,
        'devis_age_years'     : float | None,
        'sources_used'        : int,           # 0, 1 ou 2
        'confidence'          : str,           # 'HIGH'|'MEDIUM'|'LOW'|'NONE'
    }
    """
    ref = _parse_date(today) or date.today()

    result = {
        'weighted_price':   None,
        'base_actualized':  None,
        'base_weight':      None,
        'base_age_years':   None,
        'devis_actualized': None,
        'devis_weight':     None,
        'devis_age_years':  None,
        'sources_used':     0,
        'confidence':       'NONE',
    }

    contributions = []

    # ── Prix de la bibliothèque ────────────────────────────────────────────────
    base_d = _parse_date(base_date)
    if base_price and base_price > 0 and base_d:
        age   = _age_years(base_d, ref)
        w     = _weight(age)
        act   = _actualize(base_price, base_d, ref)
        result['base_actualized']  = round(act, 2)
        result['base_weight']      = round(w, 4)
        result['base_age_years']   = round(age, 2)
        contributions.append((act, w))

    # ── Prix du devis importé ─────────────────────────────────────────────────
    devis_d = _parse_date(devis_date)
    if devis_price and devis_price > 0 and devis_d:
        age   = _age_years(devis_d, ref)
        w     = _weight(age)
        act   = _actualize(devis_price, devis_d, ref)
        result['devis_actualized']  = round(act, 2)
        result['devis_weight']      = round(w, 4)
        result['devis_age_years']   = round(age, 2)
        contributions.append((act, w))

    result['sources_used'] = len(contributions)

    if not contributions:
        return result

    # ── Moyenne pondérée ──────────────────────────────────────────────────────
    numerator   = sum(price * weight for price, weight in contributions)
    denominator = sum(weight for _, weight in contributions)
    result['weighted_price'] = round(numerator / denominator, 2)

    # ── Niveau de confiance ───────────────────────────────────────────────────
    max_weight = max(w for _, w in contributions)
    if result['sources_used'] == 2 and max_weight >= 3.5:
        result['confidence'] = 'HIGH'
    elif result['sources_used'] == 2 or max_weight >= 2.5:
        result['confidence'] = 'MEDIUM'
    else:
        result['confidence'] = 'LOW'

    return result
