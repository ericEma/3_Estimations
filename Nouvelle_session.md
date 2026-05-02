# Prompt de relance — Sprint 9

## Contexte projet (à lire avant toute action)

Avant d'intervenir sur le code, lis dans cet ordre :
1. `instructions.md` — conventions techniques et règles métier immuables
2. `CURRENT_STATE.md` — état validé du projet et architecture actuelle (v8)
3. `TODO.md` — tâches prioritaires et points de vigilance
4. `Intentions_Projet.md` — objectifs fonctionnels et décisions d'architecture

---

## Résumé projet

**Estimation Élec** est une application Flask locale (SQLite3 natif, pas SQLAlchemy) permettant
à Eric, ingénieur électricien Egis, de produire rapidement des estimations budgétaires DPGF
pour des installations électriques de bâtiment (285 articles, 3 lots CFO/CFA/PV, 44 sections).

Le référentiel de ratios €/m² est issu de projets passés PSA. L'outil applique des coefficients
de complexité, de phase d'étude et de provisions selon les paliers Egis.

---

## État Sprint 8 (livré le 2026-04-19)

Sprint 8 a corrigé 5 rounds de retours QC d'Eric :

| # | Correction | Statut |
|---|---|---|
| 1 | Totaux CFO/CFA/PV (lot detection case-insensitive) | ✅ |
| 2 | Date JJ-MM-AAAA (filtre `date_fr` Jinja2) | ✅ |
| 3 | Sous-totaux par section (unitaires ET ratio €/m²) | ✅ |
| 4 | Sélecteur type de bâtiment dans la topbar | ✅ |
| 5 | Lien retour "Modifier la fiche" depuis le calculateur | ✅ |
| 6 | 15 types de bâtiments (migration FK-safe) | ✅ |
| 7 | Architecture section-first : chapitre = Σ sections | ✅ |
| 8 | Total dashboard = total calculateur (`total_estime_ht`) | ✅ |

**BDD** : v8 — nouvelle colonne `affaires.total_estime_ht REAL`
**Test** : migration, save_total_estime, COALESCE fallback → tous OK (2026-04-19)

---

## Objectif Sprint 9

À définir après retour d'Eric sur Sprint 8. Candidats prioritaires :

1. **Feedback visuel "saving…"** sur l'auto-save chapter_settings
2. **Statut affaire** : workflow UI brouillon → en_cours → finalisé → archivé
3. **Reset cellule orange** → retour valeur référentiel
4. **Export Excel** : intégrer les chapter_settings dans le récap

---

## Points de vigilance avant de coder

- **Lot detection** : toujours utiliser `| lower` en Jinja2 — ne jamais comparer des désignations de chapitre avec casse fixe
- **Totaux** : architecture section-first obligatoire — `chapterTotal = Σ sectionSubtotals`
- **total_estime_ht** : NULL pour les affaires pré-Sprint 8 → COALESCE assure le fallback automatique
- **Migrations** : pattern `try/except OperationalError: pass` dans `ensure_app_tables()`
- **FK migrations** : toujours encadrer avec `PRAGMA foreign_keys = OFF/ON` + nullifier les FK orphelines
- **Chargement strict** : ne jamais ré-injecter les 285 lignes si `affaire_lines` est déjà rempli
