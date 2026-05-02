# TODO List — Estimation Élec
> Dernière mise à jour : 2026-04-26

---

## ✅ Sprints terminés

- [x] **Sprint 1** : Infrastructure (schéma SQLite + lecture Excel)
- [x] **Sprint 2** : Import & Mapping (285 articles DPGF, fuzzy rapidfuzz, unit-aware scoring)
- [x] **Sprint 3** : Intelligence Métier (ratios pondérés, rapport `referentiel_ratios_2026.md`)
- [x] **Sprint 4** : Application Flask (calculateur DPGF, export Excel Egis)
- [x] **Sprint 5** : UX & Coefficients (KPI bar, Provisions bar, auto-save, injection 285 lignes)
- [x] **Sprint 6** : Refonte taxes + Complexité éditable
- [x] **Sprint 7** : Ergonomie & Logique Hybride Chapitre (sticky thead, checkboxes, Macro/Détail)
- [x] **Sprint 8** : QC Eric — 5 rounds de corrections
    - [x] Totaux CFO/CFA/PV corrigés (lot detection case-insensitive)
    - [x] Date format JJ-MM-AAAA (filtre `date_fr` Jinja2)
    - [x] Sous-totaux par section dans le tableau DPGF
    - [x] Sélecteur type de bâtiment dans la barre de paramètres (topbar)
    - [x] Lien "Modifier la fiche" depuis le calculateur → `/affaire/<id>/edit`
    - [x] 15 types de bâtiments (migration FK-safe)
    - [x] Sous-totaux ratio €/m² (pas seulement unitaires)
    - [x] Chapitre coché + qty=0 → ratio €/m² utilisé comme fallback
    - [x] Architecture section-first : chapitre = Σ sections (cohérence garantie)
    - [x] Total dashboard = total calculateur (`total_estime_ht` + COALESCE)

---

## ✅ Session 2026-04-26
- [x] **Page Bibliothèque DPGF** : `/bibliotheque` et `/bibliotheque/<id>` — explorateur tree-grid
- [x] **Lanceur corrigé** : `cmd /k` + délai adaptatif netstat (fenêtre reste visible si crash)

---

## 🎯 Sprint 9 — À prioriser selon retours Eric

> Sprint 9 commence après validation par Eric des corrections Sprint 8.

### Améliorations UX identifiées
- [ ] **Feedback visuel "saving…"** lors de l'auto-save chapter_settings (pas de retour actuellement)
- [ ] **Possibilité de saisir manuellement le ratio €/m²** d'une tête de chapitre (override)
- [ ] **Masquer/afficher les articles à qty=0** : bouton existant à vérifier après Sprint 8

### Logique métier
- [ ] **Export Excel** : inclure les chapter_settings (mode Macro/Détail) dans le récap
- [ ] **Statut affaire** : workflow brouillon → en_cours → finalisé → archivé
  (le champ existe en BDD mais l'UI ne permet pas de le changer)
- [ ] **Édition partielle des lignes** : reset d'une cellule orange vers valeur référentiel

### Qualité
- [ ] **Test E2E** Playwright pour le scénario hybride complet
  (section-first, ratio fallback, dashboard total)
- [ ] Documenter le comportement attendu de la popup "Corriger la base"
  (écrit dans `ratio_overrides` globalement — impact sur toutes les affaires)

---

## 🔮 Sprints futurs possibles

- [ ] Graphique radar CFO/CFA/PV sur le dashboard (comparaison référentiel vs affaire)
- [ ] Comparaison multi-affaires (tableau de bord côte à côte)
- [ ] Fiche PDF par affaire (weasyprint ou export dédié)
- [ ] Import de plusieurs projets de référence (enrichir ratios, lever alertes SOURCE_UNIQUE)
- [ ] Authentification simple multi-utilisateurs (login/mot de passe local)
- [ ] Duplication d'affaire (copier une affaire existante comme base)
- [ ] Historique des modifications par affaire (audit trail)

---

## 🚨 Points de vigilance technique

1. `total_estime_ht` est `NULL` pour les affaires créées avant Sprint 8.
   → COALESCE assure le fallback. Pas de rupture, mais total = qty×PU uniquement.
   → Se corrigera au premier "Sauvegarder" depuis le calculateur.

2. La popup "Corriger la base" écrit dans `ratio_overrides` **globalement**.
   Impact potentiel sur toutes les affaires utilisant le même article DPGF.
   → Pas encore de mécanisme de rollback / historique.

3. Les 200 articles `AUCUNE_REF` restent à qty=0, pu=0.
   → Pas de plan d'enrichissement du référentiel à court terme.
