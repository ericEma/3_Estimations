# TODO List — Estimation Élec
> Dernière mise à jour : 2026-05-25

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

## ✅ Session 2026-05-25 — Consolidation état applicatif (Eric)
- [x] **Ratios globaux fiche affaire** : CFO/CFA/PV éditables, persistés, preview recalculée, réaffichés à la réouverture.
- [x] **Ratio total €/m²** : affiché sous le total preview fiche affaire (`prix_total / surface_sdo`).
- [x] **Dashboard** : bouton `Fiche` sur chaque carte affaire.
- [x] **Page Estimation** : ajout taux incertitude + risque dans le header, auto-save; suppression bouton `+ Ligne hors catalogue`.
- [x] **Sous-totaux section** : total macro si aucun article chiffré, sinon somme des articles.
- [x] **Layout section** : insertion/renommage corrigés, pas de doublon temporaire, suppression visible au survol pour sections locales.
- [x] **Promotion base de prix** : section promue implantée au bon endroit dans la bibliothèque.
- [x] **Bibliothèque** : déplacement ▲/▼ des sous-chapitres avec leurs articles, persistance `row_order`.
- [x] **Documentation** : état courant, architecture BDD/applicative et garde-fous anti-régression mis à jour.

## ✅ Session 2026-05-24 — Page Estimation (Eric)
- [x] **Snapshot** à la création d'affaire (`estimation_initialized_at`, PU figés, pas de resync biblio)
- [x] **PU snapshot** : `pu_ht_ref` ou `compute_ratios()` si NULL (aligné bibliothèque)
- [x] **Macro section** : ratio + surface sur ligne sous-chapitre (éditable)
- [x] **Layout** : `+`, `§+`, ▲/▼, sections/articles affaire-only (`estimation_layout.py`)
- [x] **Nouvelle section** : sous sous-chapitre courant, 1 article vide, ratio macro, pas hors catalogue
- [x] **Désignations** : tous articles + sections locales éditables (`estim-desig-edit`, `line_designation`)
- [x] **Promotion** vers base de prix (`estimation_promote.py`, bouton 📖)
- [x] **Header** : lien Calculateur retiré de la page estimation
- [x] **Tests** : `test_estimation_snapshot.py`, `test_estimation_promote.py`

## ✅ Session 2026-05-23
- [x] **Fiche affaire** (`/affaire/new`, `/affaire/<id>/edit`) : estimation prévisionnelle CFO/CFA/PV + total
- [x] **Ratios preview** : `engine_bibliotheque_ratios.py` (base `pu_ht_ref`, même logique que bibliothèque)
- [x] **PV** : slider 3 crans type système (`pv_system_type`) ; `coef_complexity_pv` = 1,0 sur fiche
- [x] **API** : `GET /api/affaire/preview_estimation` (SDO, kWc, provisions, complexité CFO/CFA, type PV)
- [x] **Validation Eric** : écart prix vs ratio × surface expliqué par provisions cumulatives (phase × incertitude × risque)

## ✅ Session 2026-05-12
- [x] **Revue Matching** : PU calculé éditable + persistance `weighted_price_override` ; modal fil DPGF ; désignations multilignes ; nettoyage texte avec apostrophes (`engine_matching`)

---

## ✅ Session 2026-04-26
- [x] **Page Bibliothèque DPGF** : `/bibliotheque` et `/bibliotheque/<id>` — explorateur tree-grid
- [x] **Lanceur corrigé** : `cmd /k` + délai adaptatif netstat (fenêtre reste visible si crash)

---

## 🎯 Prochaine session — priorité

- [ ] **Export Excel** depuis page Estimation (bouton placeholder actuel)
  - Exporter l'état snapshot/layout réel : macros section, articles locaux/promus, overrides de désignation, inclusions, totaux et provisions.
- [ ] **Feedback visuel saving…** sur auto-save paramètres, section settings et layout.

---

## 🎯 Sprint 9 — Autres retours Eric

### Améliorations UX identifiées
- [ ] **Feedback visuel "saving…"** lors de l'auto-save chapter_settings (pas de retour actuellement)
- [ ] **Possibilité de saisir manuellement le ratio €/m²** d'une tête de chapitre (override)
- [ ] **Masquer/afficher les articles à qty=0** : bouton existant à vérifier après Sprint 8

### Logique métier
- [ ] **Calculateur** : appliquer `pv_system_type` (coef 1,0 / 1,3 / 1,55) sur le lot PV si cohérence avec la fiche affaire souhaitée
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
