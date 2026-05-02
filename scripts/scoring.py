"""
scoring.py - Logique de scoring fuzzy centralisée (unit-aware)

Règle métier : l'unité est un discriminant de premier ordre.
  - Même famille d'unité  → score textuel conservé (×1.0)
  - Familles différentes  → malus 50%              (×0.5)

Familles :
  'cable'      : ml, m         (câblage linéaire)
  'surface'    : m², m2        (surfacique)
  'equipement' : u, ens, ensemble, forfait, ft  (équipements à l'unité)
  'unknown'    : unité absente ou non reconnue  → pas de malus

Exemples :
  devis 'ml' vs DPGF 'ml'  → ×1.0 (même famille câble)
  devis 'ml' vs DPGF 'u'   → ×0.5 (câble vs équipement)
  devis 'u'  vs DPGF 'ens' → ×1.0 (même famille équipement)
  devis None vs DPGF 'ml'  → ×1.0 (unknown → pas de malus)
"""
from rapidfuzz import fuzz

UNIT_FAMILIES: dict[str, str] = {
    # Câblage linéaire
    "ml":       "cable",
    "m":        "cable",
    # Surfacique
    "m²":       "surface",
    "m2":       "surface",
    "m ²":      "surface",
    # Équipement (unité ou forfait)
    "u":        "equipement",
    "ens":      "equipement",
    "ensemble": "equipement",
    "forfait":  "equipement",
    "ft":       "equipement",
}

UNIT_PENALTY_FACTOR = 0.5   # malus appliqué quand les familles diffèrent


def unit_family(unit: str | None) -> str:
    """Retourne la famille d'unité : 'cable' | 'surface' | 'equipement' | 'unknown'."""
    if not unit:
        return "unknown"
    return UNIT_FAMILIES.get(unit.strip().lower(), "unknown")


def unit_penalty(devis_unit: str | None, dpgf_unit: str | None) -> float:
    """
    Facteur multiplicatif de pénalité unité.

    Returns 0.5 si les familles sont connues ET différentes.
    Returns 1.0 dans tous les autres cas (même famille, ou unknown).
    """
    f1 = unit_family(devis_unit)
    f2 = unit_family(dpgf_unit)
    if f1 == "unknown" or f2 == "unknown":
        return 1.0
    return 1.0 if f1 == f2 else UNIT_PENALTY_FACTOR


def unit_aware_score(
    text_score: float,
    devis_unit: str | None,
    dpgf_unit: str | None,
) -> float:
    """
    Applique la pénalité unité sur un score textuel brut.

    Args:
        text_score : score fuzzy textuel 0–100 (ex: fuzz.WRatio)
        devis_unit : unité de la ligne devis
        dpgf_unit  : unité de l'article DPGF

    Returns : score final 0–100 après pénalité.
    """
    return round(text_score * unit_penalty(devis_unit, dpgf_unit), 1)


def compute_text_score(desig_a: str, desig_b: str) -> float:
    """
    Score textuel brut entre deux désignations.
    Utilise WRatio (robuste aux permutations et sous-chaînes).
    """
    return float(fuzz.WRatio(desig_a.lower(), desig_b.lower()))


def score_candidate(
    devis_desig: str,
    devis_unit: str | None,
    dpgf_desig: str,
    dpgf_unit: str | None,
) -> tuple[float, float, float]:
    """
    Score complet d'un candidat DPGF pour une ligne devis.

    Returns:
        (score_final, score_textuel, facteur_penalite)
        score_final = score_textuel × facteur_penalite
    """
    text  = compute_text_score(devis_desig, dpgf_desig)
    pf    = unit_penalty(devis_unit, dpgf_unit)
    final = round(text * pf, 1)
    return final, text, pf


def unit_penalty_label(devis_unit: str | None, dpgf_unit: str | None) -> str:
    """Retourne une étiquette lisible pour affichage ('⚠ ml≠u', '' si OK)."""
    f1 = unit_family(devis_unit)
    f2 = unit_family(dpgf_unit)
    if f1 == "unknown" or f2 == "unknown":
        return ""
    if f1 != f2:
        u1 = devis_unit or "?"
        u2 = dpgf_unit  or "?"
        return f"[!unit {u1}≠{u2}]"
    return ""
