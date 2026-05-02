1. Contexte & Objectif
La page d'affichage de la base de prix (bibliotheque.html) est validée sur le plan du design. Cependant, elle est actuellement incomplète fonctionnellement. L'objectif est de transformer cette vue en un outil d'édition dynamique permettant de consulter et de modifier le référentiel de prix (Master DPGF).

2. Corrections Immédiates (Affichage)
Activation des prix : Actuellement, les colonnes PU HT et Total HT sont vides.

Tu dois t'assurer que get_bibliotheque_data récupère les ratios moyens depuis la table referentiel_ratios.

Le Total HT d'une ligne doit être calculé dynamiquement : Quantité * PU HT.

Les totaux par Section et par Chapitre doivent être calculés en temps réel.

KPI Cards : Les montants globaux en haut de page (CFO, CFA, PV) doivent refléter la somme des totaux du tableau en fonction de la SDO saisie.

3. Nouvelles Fonctionnalités d'Édition
A. Insertion Libre de Lignes
Permettre l'insertion de lignes manuelles n'importe où dans l'arborescence (Chapitre ou Section).

Ajouter un bouton d'action "+" discret au survol des lignes de structure.

Persistance : Ces nouvelles lignes doivent être enregistrées en base de données. Prévoir un flag is_custom=True pour ces articles afin de les distinguer du Master PSA 285.

B. Édition Directe (Inline Editing)
Rendre les colonnes Désignation, Unité, Quantité et PU HT éditables pour toutes les lignes (existantes ou créées).

Règle métier (Cellules Oranges) : Toute cellule modifiée manuellement doit passer en fond orange (#f5a623) et porter l'attribut data-source="manual".

Calcul en cascade : La modification d'un PU HT ou d'une Quantité doit recalculer instantanément le total de la ligne et les totaux parents (Section/Chapitre/Global).

C. Sauvegarde & Persistance
Implémenter une route API POST /api/bibliotheque/save pour enregistrer les modifications du référentiel.

Utiliser un système de sauvegarde automatique (debounce de 800ms) lors de l'édition des cellules.

4. Contraintes Techniques
Design : Conserver strictement l'esthétique actuelle (Egis Dark Mode).

Zéro LaTeX : Les notations mathématiques doivent rester en texte brut lisible.

Hiérarchie : Respecter la structure "Section-First" : un chapitre est toujours la somme de ses sections.

Réactivité : Utiliser le JavaScript existant pour les mises à jour du DOM sans rechargement de page.

Action demandée : Analyse les fichiers app.py, models.py et bibliotheque.html pour implémenter ces modifications dès maintenant.