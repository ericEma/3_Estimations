
---
script_testé: Estimation ELEC
date_test: 2026-04-19
testeur: Eric
version_app: [à compléter]
---

# Rapport de validation — Estimation ELEC

> **Légende statuts** :
> - ✅ Validé — fonctionne comme attendu
> - 🔄 En cours — partiellement fonctionnel, à revoir
> - ❌ Non validé — correction obligatoire avant mise en production
> - ⚪ Non testé — à tester ultérieurement

---
**********************************************************************************
## Contexte projet (à lire avant toute action)

Avant d'intervenir sur le code, lis dans cet ordre :
1. `instructions.md` — conventions techniques et règles métier immuables
2. `CURRENT_STATE.md` — état validé du projet et architecture actuelle (v8)
3. `TODO.md` — tâches prioritaires et points de vigilance
4. `Intentions_Projet.md` — objectifs fonctionnels et décisions d'architecture

---

**********************************************************************************
## 1. Page Création d'affaire

### 1.1 Visuel / respect de la charte graphique

- **Statut** : ✅ Validé

---

### 1.2 Fonctionnement des cases de saisie

- **Statut** : ✅ Validé

---

### 1.3 Fonctionnement des curseurs glissants

- **Statut** : ✅ Validé

---

### 1.4 Fonctionnement des boutons

- **Statut** : ✅ Validé

---

### 1.5 Mémorisation des données

- **Statut** : ⚪ Non testé
- **Commentaire** : test à finaliser

---

### 1.6 Validation du total

- **Statut** : 🔄 À revalider (corrigé 2026-04-21)
- **Priorité** : Haute
- **Fichier concerné** : `templates/affaire_new.html`, `app.py`
- **Description du test** : vérification du montant affiché et des libellés associés au bloc "Estimation prévisionnelle"
- **Comportement observé** : le total affiché ne couvre pas tous les lots ; libellés obsolètes
- **Comportement attendu** : total global CFO+CFA+PV avec libellés à jour
- **Corrections à réaliser** :
  - [x] Corriger le calcul du total affiché
    - *Détail* : total CFO+CFA+PV calculé via ratios €/m² par lot injectés depuis la BDD (`_compute_preview_context()`), multipliés par SDO × complexité de chaque lot dans `updatePreview()`.
  - [x] Renommer le libellé du bloc total
    - *Détail* : libellé remplacé par `Estimation prévisionnelle CFO/CFA/PV`.
  - [x] Dynamiser le texte de source des prix
    - *Détail* : `{{ maj_date_bdd }}` = mtime de `estimation_elec.db` formaté `JJ-MM-AAAA`.

---
--------------------------------------------------------------------------
## 2. Page affaire

### 2.1 Visuel / respect de la charte graphique

- **Statut** : ✅ Validé

---

### 2.2 Fonctionnement des cases de saisie

- **Statut** : ✅ Validé

---

### 2.3 Fonctionnement des curseurs glissants

- **Statut** : ✅ Validé

---

### 2.4 Fonctionnement des boutons

- **Statut** : ✅ Validé

---

### 2.5 Fonctionnement des cases à cocher

- **Statut** : ✅ Validé

---

### 2.6 Mémorisation des données

- **Statut** : ✅ Validé

---

### 2.7 Validation des totaux et sous totaux

- **Statut** : ✅ Validé

---

### 2.8 Validation du mode 1

- **Statut** : ✅ Validé

---

### 2.9 Validation du mode 2

- **Statut** : ✅ Validé

---

### 2.10 Validation du mode 3

- **Statut** : ✅ Validé


--------------------------------------------------------------------------
## 3 Page Import Devis

### Visuel / respect de la charte graphique

- **Statut** : ✅ Validé

---

### Fonctionnement des cases de saisie

- **Statut** : ✅ Validé

---

### Fonctionnement de la zone des curseurs de complexité

**Statut** : 🔄 À revalider (corrigé 2026-04-21)
- **Priorité** : Haute
- **Fichier concerné** : `templates/import.html`
- **Description du test** : vérification de la cohérence des valerus modifiées par les curseurs
- **Comportement observé** : la valeur ventrale n'affiche pas la valeur du curseur mais toujours la valeur 1. La valeur de gauche n'affiche pas la valeur minimale mais la valeur courante.
- **Comportement attendu** : La valeur de gauche doit afficher la valeur minimale. La valeur au centre doit afficher la valeur courante.
- **Corrections à réaliser** :
  - [x] Corriger l'affichage des valeurs de gauche des 3 curseurs glissants
    - *Détail* : valeur de gauche figée à `×0.7` (markup statique), plus écrasée par le JS.
  - [x] Corriger l'affichage des valeurs au centre des 3 curseurs glissants
    - *Détail* : `oninput` cible désormais `querySelectorAll('span')[1]` (span central) au lieu de `querySelector('span')` (qui renvoyait le premier span = gauche).

---

### Fonctionnement des boutons

- **Statut** : ✅ Validé

---

### Mémorisation des données

- **Statut** : ⚪ Non testé

---

### Validation des totaux 

- **Statut** : ⚪ Non testé

---

### Validation de la liste des projets

- **Statut** : ⚪ Non testé

---

### Validation de l'importation d'un fichier

- **Statut** : ✅ Validé (2026-04-21)

---

### Rapport d'import — lisibilité (codes ANSI) [statut Eric]

- **Statut** : ✅ Validé (2026-04-21)

---

### Suppression d'un projet en attente de mapper

- **Statut** : 🔄 À revalider (corrigé 2026-04-21)
- **Priorité** : Haute
- **Fichier concerné** : `models.py`, `app.py`, `templates/import.html`
- **Description du test** : suppression d'un projet existant depuis la page `/import`, même lorsque son mapping n'est pas terminé.
- **Comportement observé** : aucun mécanisme de suppression n'était exposé dans l'UI. Aucune route backend `/api/project/<id>/delete` n'existait.
- **Comportement attendu** : un bouton 🗑 sur chaque ligne de la liste des projets permet la suppression avec confirmation, quel que soit l'état du mapping.
- **Corrections à réaliser** :
  - [x] Fonction `delete_project(project_id)` dans `models.py`
    - *Détail* : `PRAGMA foreign_keys = ON` puis `DELETE FROM devis_lines WHERE project_id = ?` suivi de `DELETE FROM projects WHERE id = ?`. Suppression en deux temps pour être robuste même si la contrainte CASCADE n'est pas définie sur la table.
  - [x] Route `POST /api/project/<int:project_id>/delete`
    - *Détail* : ajoutée dans `app.py` avant la section "Ratio overrides". Retourne `{'status':'ok'}` ou `404 {'status':'error'}` si projet introuvable.
  - [x] Bouton 🗑 dans `templates/import.html`
    - *Détail* : ajouté dans la colonne Actions de la liste des projets, classe `btn-sm btn-danger`, confirmation JS `confirm(...)` avant appel `fetch('/api/project/<id>/delete')` + `window.location.reload()`.

---

### Complétude du nombre de lignes du devis à mapper

- **Statut** : 🔄 À revalider (corrigé 2026-04-21)
- **Priorité** : Haute
- **Fichier concerné** : `scripts/read_excel.py`, `scripts/import_devis.py`
- **Description du test** : le mapping manuel doit contenir toutes les lignes de détail du devis ayant un Montant HT renseigné (hors totaux et sous-totaux).
- **Comportement observé** : des lignes forfaitaires avec un Montant HT mais sans unité ni PU (typiquement une ligne "Forfait général X" avec montant global) étaient classées `row_type='section'` ou filtrées par `if r.unit and ...` dans `import_devis.py`, donc **absentes** de la page mapping.
- **Cause racine identifiée** : double filtre trop restrictif.
  1. `read_excel.py:339` classait comme article uniquement si `code avec "." OU unit OU unit_price`. Une ligne avec seulement un `total_ht` devenait `section`.
  2. `import_devis.py:524` gardait uniquement `r.unit and r.row_type in ('article','so')`.
- **Comportement attendu** : toute ligne non-subtotal ayant `total_ht > 0` doit être importée et apparaître dans le mapping manuel (même sans unit/PU), pour que l'utilisateur puisse décider de sa cible DPGF.
- **Corrections à réaliser** :
  - [x] Étendre la détection d'article dans `read_excel.py`
    - *Détail* : `is_article = code avec "." OR unit OR unit_price OR (total_ht is not None and total_ht > 0)`. Commentaire de règle métier daté 2026-04-21.
  - [x] Assouplir le filtre d'import dans `import_devis.py`
    - *Détail* : nouvelle fonction locale `_keep_for_import(r)` qui retient toute ligne `article/so` avec `unit` OU `total_ht > 0`. Les lignes sans aucun des deux restent ignorées (entêtes vides, commentaires). Logs mis à jour : `Lignes à importer (unité OU montant HT)` / `Lignes ignorées (sans unité ET sans montant)`.

---

### Colonne "désignation source (devis)"

- **Statut** : 🔄 À revalider (corrigé 2026-04-21)
- **Priorité** : Haute
- **Fichier concerné** : `templates/mapping.html`
- **Description du test** : page `/mapping/<project_id>`, colonne "Désignation source (devis)".
- **Comportement observé** : visuellement ambigu — l'utilisateur pouvait confondre la désignation devis (col 3) avec la désignation DPGF cible (col 7) car aucune distinction graphique ne séparait le bloc "source" du bloc "cible". Pas de colonne Montant HT affichée.
- **Comportement attendu** : séparation visuelle nette entre les données issues du devis (désignation, unité, quantité, PU, Montant HT) et la cible DPGF modifiable.
- **Corrections à réaliser** :
  - [x] Restructurer l'en-tête de table avec deux groupes
    - *Détail* : `<thead>` à 2 lignes. 1ʳᵉ ligne = en-têtes groupés (`📄 Ligne devis source` colspan=5, `🎯 Article DPGF cible (modifiable)`). 2ᵉ ligne = sous-en-têtes (Désignation / Unité / Qté / PU HT / Montant HT).
  - [x] Ajouter la colonne Montant HT (`line.total_ht`)
    - *Détail* : 5ᵉ colonne source en gras, alignement `tabular-nums`, format `| eur`.
  - [x] Fond coloré distinct pour les cellules source vs cible
    - *Détail* : `td.col-source { background:rgba(213,243,17,.04) }` (teinte lime Egis) et `td.col-target { background:rgba(36,169,181,.06) }` (teinte cyan Egis) pour un contraste subtil conforme à la charte.
  - [x] Afficher la cible DPGF actuelle au-dessus du select
    - *Détail* : `{% if line.dpgf_designation %}<div>Actuel : <span>{{ line.dpgf_designation }}</span></div>{% endif %}` → l'utilisateur voit immédiatement ce que le fuzzy matching a proposé, sans avoir à dérouler le `<select>`.

---

--------------------------------------------------------------------------
## 4 Lancement de l'application avec 'Lancer_Estimateur.vbs'

### Validation du démarrage de l'application html
- **statut** : 🔄 À revalider (corrigé 2026-04-21)
- **Priorité** : Haute
- **Fichier concerné** : `Lancer_Estimateur.vbs`
- **Description du test** : vérification de l'ouverture de la page web 'http://localhost:5000/' sur le PC bureau
- **Comportement observé** : La page web ne s'ouvre pas immédiatement, le serveur n'est pas détectable pendant au moins 30 secondes.
- **Comportement attendu** : lancemetn plus rapide du serveur et ouverture rapide de la page Web.
- **Corrections à réaliser** :
  - [x] améliorer la vitesse de création du serveur
    - *Détail* : remplacement du `WScript.Sleep 4000` fixe par un polling HTTP (`MSXML2.XMLHTTP`) sur `http://localhost:5000/` toutes les 400 ms jusqu'à 45 s. Le navigateur s'ouvre dès que Flask répond (typiquement < 5 s) au lieu d'attendre 4 s puis afficher une page vide si Flask n'est pas prêt.

### Validation de l'ouverture des autres pages 
- **statut** : 🔄 À revalider (corrigé 2026-04-21)
- **Fichier concerné** : `templates/base.html`
- **Description du test** : clique sur 'affaire' ou 'nouvelle afaire' ou 'Import Devis' pour l'ouverture de la page web correspondante
- **Comportement observé** : une nouvelle page s'ouvre mais affiche sur fond blanc 'cannot GET /affaire/11' ou 'cannot GET /import'
- **Cause racine identifiée** : `base.html` envoyait `navigator.sendBeacon('/api/shutdown')` sur chaque événement `beforeunload`. Or `beforeunload` se déclenche aussi à la **navigation interne** (clic sur un lien), donc Flask était tué entre deux pages → l'URL suivante tombait dans le vide (page blanche avec message d'erreur type "cannot GET").
- **Corrections à réaliser** :
  - [x] Retirer l'appel `sendBeacon('/api/shutdown')` du `beforeunload` dans `base.html`. Le watchdog serveur (timeout 15 s sans heartbeat) suffit pour arrêter Flask quand le dernier onglet est fermé.