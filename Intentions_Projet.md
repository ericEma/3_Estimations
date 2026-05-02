# Intentions Projet — Estimation Élec (Egis)
**Créé : 2026-04-19 | Dernière mise à jour : 2026-04-19**

---

## 1. Contexte et objectif

**Estimation Élec** est un outil d'estimation budgétaire d'installations électriques de bâtiment,
développé pour Eric (ingénieur électricien, Egis).

**Problème résolu** : la préparation d'une DPGF (Décomposition du Prix Global et Forfaitaire) pour
un projet neuf prend plusieurs jours à partir de zéro. L'outil vise à réduire ce temps à quelques
heures en proposant des ratios €/m² issus d'un référentiel de projets passés.

**Résultat attendu** : l'ingénieur saisit les paramètres d'un projet (surface, type de bâtiment,
phase d'étude, complexité) et obtient immédiatement une estimation structurée des lots CFO, CFA et PV,
avec possibilité d'affiner article par article.

---

## 2. Périmètre fonctionnel

### Ce que l'outil fait

- **Dashboard** : liste toutes les affaires en cours, avec total estimé et date.
- **Création d'affaire** : saisie des paramètres de cadrage (SDO, kVA, phase, complexité, type de bâtiment).
- **Calculateur DPGF** : tableau des 285 articles du référentiel PSA, organisés en 3 chapitres (CFO/CFA/PV) et 44 sections.
- **Logique hybride Macro/Détail** : chaque chapitre peut travailler en mode ratio €/m² (rapide) ou en mode détail article par article.
- **Provisions** : calcul automatique des provisions (phase, incertitude, risque) selon les paliers Egis.
- **Export Excel** : génération d'un fichier DPGF formaté aux standards Egis.
- **Édition de fiche** : modifier les paramètres d'une affaire existante sans perdre les valeurs saisies.

### Ce que l'outil ne fait PAS (hors périmètre)

- Pas de gestion des marchés / commandes fournisseurs.
- Pas de planning ou ordonnancement de travaux.
- Pas de calcul de dimensionnement électrique (bilans de puissance, chute de tension…).
- Pas de multi-utilisateurs (usage solo local).
- Pas d'intégration avec des outils tiers (BIM, ERP, etc.).

---

## 3. Contraintes

### Contraintes techniques
- **SQLite3 natif** : choix délibéré pour un usage local simple, sans serveur de base de données.
- **Pas de SQLAlchemy** : cohérence avec les scripts d'import existants.
- **Flask 3.x** : léger, démarrage rapide, adapté à un usage solo.
- **Zéro LaTeX** : tous les affichages en texte brut ou Markdown.

### Contraintes métier
- Le référentiel DPGF est fixé à 285 articles (master PSA). Toute modification passe par l'import Excel.
- Les trois lots (CFO/CFA/PV) doivent toujours être distingués dans les totaux.
- Les formules de provision (phase × incertitude × risque) sont cumulatives, jamais additives.
- La SDO est la surface de référence pour tous les ratios €/m² (sauf PV qui est en €/kWc).

### Contraintes UX
- L'outil doit être utilisable par un ingénieur non-développeur : pas de terminal, lancement par double-clic (`.bat`).
- Charte graphique Egis : fond sombre `#09212C`, accent jaune `#D5F311`, accent cyan `#24A9B5`.
- Les totaux affichés doivent être strictement cohérents entre eux (règle section-first Sprint 8).

---

## 4. Décisions d'architecture structurantes

### D-01 : Section-first pour les totaux (Sprint 8)
**Décision** : Le total d'un chapitre est toujours calculé comme la somme de ses sections.
Il n'existe pas de calcul indépendant du total chapitre.
**Raison** : garantit la cohérence entre les sous-totaux affichés et le total final,
quelle que soit la complexité des fallbacks ratio/qty.

### D-02 : ratio_ref_m2 calculé côté serveur avant écrasement (Sprint 7/8)
**Décision** : Le ratio théorique (basé sur le référentiel) est calculé dans `affaire_view`
avant que les valeurs sauvegardées ne soient appliquées. Il est transmis au HTML via `data-ratio-ref`.
**Raison** : permet au JS d'avoir un fallback fiable sans appel supplémentaire au serveur.

### D-03 : total_estime_ht stocké à chaque sauvegarde (Sprint 8)
**Décision** : Le JS calcule `currentTotal` (incluant les ratio fallbacks) et l'envoie
dans chaque POST save. La BDD stocke ce total dans `affaires.total_estime_ht`.
**Raison** : le serveur ne peut pas reconstituer les ratio fallbacks sans rejouer toute la
logique JS → délégation au client, persistance côté serveur.

### D-04 : Migrations auto-idempotentes (tous sprints)
**Décision** : Toutes les migrations SQL sont dans une liste dans `ensure_app_tables()`.
Chaque `ALTER TABLE` est dans un `try/except OperationalError: pass` (colonne déjà présente).
**Raison** : déploiement sans outil de migration dédié, cohérence garantie à chaque redémarrage.

### D-05 : Lot detection case-insensitive (Sprint 8)
**Décision** : La détection CFO/CFA/PV se fait via `| lower` en Jinja2 sur la désignation du chapitre.
**Raison** : les désignations du référentiel peuvent varier en casse selon les sources Excel.

### D-06 : DOM-priority pour les checkboxes (Sprint 8)
**Décision** : `chapterInclusion[chapId]` lit d'abord le checkbox DOM, puis CHAPTER_STATE.
**Raison** : évite les désynchronisations entre l'état JS mémorisé et l'état visuel réel.

---

## 5. Évolutions prévues (non engagées)

- Graphique radar CFO/CFA/PV pour comparer une affaire au référentiel.
- Duplication d'affaire (partir d'un projet similaire).
- Import multi-projets pour enrichir le référentiel de ratios.
- Export PDF de la fiche affaire.
- Authentification locale (multi-utilisateurs Egis).
