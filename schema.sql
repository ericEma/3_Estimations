-- =============================================================
-- schema.sql — Estimation Élec
-- Export depuis estimation_elec.db — 2026-04-17
-- Version : v3.1 (ratio_type_source inclut 'explicit')
--
-- Notes :
--   - coef_lot : coef_cfo | coef_cfa | coef_pv selon le lot de la ligne
--   - ratio_type : 'SURFACIQUE' (EUR/m2 SDO) | 'UNITAIRE' (EUR/unite)
--   - ratio_type_source : 'auto_unit' | 'auto_chapter' | 'manual' | 'explicit'
--     → 'explicit' = valeur lue directement depuis la colonne B du DPGF v2
--   - mapping_status : 'auto' (score>=80%) | 'manual' | 'pending' | 'unmapped'
-- =============================================================

-- -------------------------------------------------------------
-- TABLE 1 : config  (paramètres globaux de l'application)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT
);

-- -------------------------------------------------------------
-- TABLE 2 : building_categories  (types de bâtiment)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS building_categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- -------------------------------------------------------------
-- TABLE 3 : dpgf_articles  (référentiel maître — modèle DPGF)
-- -------------------------------------------------------------
-- Format v2 du fichier Excel DPGF (9 colonnes) :
--   A=Art. | B=Type ratio (Surfacique|Unitaire) | C=Nature (Titre|Article)
--   D=DESIGNATION | E=U | F=Q MOE | G=Q Entreprise | H=PU HT | I=Montant HT
--
-- Seules les lignes Nature='Article' sont importées (row_type='article').
-- Les lignes Nature='Titre' sont stockées en row_type='section' pour le context_path.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dpgf_articles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identification
    code                TEXT,           -- ex: '1.2.3' | NULL pour headers/subtotaux
    designation         TEXT NOT NULL,
    unit                TEXT,           -- 'u', 'ens', 'ml', 'm²', etc.

    -- Hiérarchie
    chapter             TEXT NOT NULL,  -- 'Courants Forts' | 'Photovoltaïque' | 'Courants Faibles'
    chapter_num         TEXT,           -- '1' | '2' | '3'
    section             TEXT,           -- sous-section ex: 'Éclairage'
    row_order           INTEGER NOT NULL,   -- ordre de parsing (compteur)
    excel_row_num       INTEGER,            -- n° de ligne réel dans le fichier Excel DPGF
    excel_row_label     TEXT,               -- repère d'affichage : '134' (réel) | '134.1' (virtuel)
    is_virtual          INTEGER NOT NULL DEFAULT 0,  -- 1 = absent du fichier Excel original
    is_custom           INTEGER NOT NULL DEFAULT 0,  -- 1 = créé par l'utilisateur
    version_model       TEXT DEFAULT '1.0',

    -- Ratios de référence consolidés (calculés après mapping multi-projets)
    pu_ht_ref           REAL,           -- PU HT de référence Complexité 1 actualisé
    densite_qte_sdo     REAL,           -- densité typique : Qte / SDO

    row_type            TEXT NOT NULL
                        CHECK (row_type IN ('chapter', 'section', 'article', 'subtotal')),

    -- Typage ratio
    -- 'explicit' = lu directement depuis col B du fichier DPGF v2 (source la plus fiable)
    ratio_type          TEXT NOT NULL DEFAULT 'SURFACIQUE'
                        CHECK (ratio_type IN ('SURFACIQUE', 'UNITAIRE')),
    ratio_type_source   TEXT NOT NULL DEFAULT 'auto_unit'
                        CHECK (ratio_type_source IN ('auto_unit', 'auto_chapter', 'manual', 'explicit')),

    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dpgf_code    ON dpgf_articles(code);
CREATE INDEX IF NOT EXISTS idx_dpgf_chapter ON dpgf_articles(chapter);
CREATE INDEX IF NOT EXISTS idx_dpgf_type    ON dpgf_articles(row_type);

-- -------------------------------------------------------------
-- TABLE 4 : projects  (un devis importé = un projet)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identification
    name                TEXT NOT NULL,
    source_file         TEXT NOT NULL,
    import_date         DATE DEFAULT CURRENT_DATE,
    devis_date          DATE NOT NULL,  -- date du devis source (pour calcul inflation)

    -- Classification
    category_id         INTEGER REFERENCES building_categories(id),
    surface_sdo         REAL NOT NULL   -- m² SDO — OBLIGATOIRE
                        CHECK (surface_sdo > 0),

    -- Coefficients de complexité par lot (paliers : 1.0 | 1.1 | 1.2)
    coef_cfo            REAL NOT NULL DEFAULT 1.0,
    coef_cfa            REAL NOT NULL DEFAULT 1.0,
    coef_pv             REAL NOT NULL DEFAULT 1.0,
    puissance_pv_kwp    REAL NOT NULL DEFAULT 0.0,

    -- Coefficients risque/incertitude
    coef_risque         REAL NOT NULL DEFAULT 0.0
                        CHECK (coef_risque >= 0.0),
    coef_incertitude    REAL NOT NULL DEFAULT 0.0
                        CHECK (coef_incertitude >= 0.0),

    -- Inflation
    taux_inflation      REAL NOT NULL DEFAULT 0.03,

    -- Contrôle
    total_ht_source     REAL,           -- Total HT extrait du fichier Excel
    total_ht_importe    REAL,           -- Somme calculée des lignes importées
    import_ok           INTEGER DEFAULT 0,  -- 1 si assertion somme == total_ht_source
    notes               TEXT,

    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_projects_date     ON projects(devis_date);
CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category_id);

-- -------------------------------------------------------------
-- TABLE 5 : devis_lines  (lignes brutes des devis importés)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devis_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Relations
    project_id          INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    dpgf_article_id     INTEGER REFERENCES dpgf_articles(id),

    -- Données source
    original_code       TEXT,
    original_designation TEXT NOT NULL,
    unit                TEXT,
    quantity            REAL,
    unit_price_ht       REAL,           -- prix brut du devis
    total_ht            REAL,           -- quantity * unit_price_ht

    -- Prix normalisé Complexité 1 : unit_price_ht / coef_lot
    prix_normalise      REAL,

    -- Mapping DPGF
    mapping_status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (mapping_status IN ('auto', 'manual', 'pending', 'unmapped', 'excluded')),
    mapping_score       REAL,           -- score Fuzzy Match 0–100
    mapping_candidate   TEXT,           -- désignation DPGF candidate

    -- Classification ligne
    row_type            TEXT NOT NULL DEFAULT 'article'
                        CHECK (row_type IN ('article', 'subtotal', 'header', 'so')),

    -- Contexte source
    excel_row_num       INTEGER,        -- n° de ligne réelle dans le devis Excel
    context_path        TEXT,           -- chemin : 'COURANTS FORTS > DISTRIBUTION'
    sub_chapter_context TEXT,

    -- Lot et validité statistique
    lot                 TEXT,           -- 'CFO' | 'CFA' | 'PV'
    is_stat_valid       INTEGER NOT NULL DEFAULT 1,
    -- 0 = exclu des ratios (ex: forfait sur article UNITAIRE explicit sans quantité)

    -- PU pondéré (matching) saisi manuellement dans le cockpit
    weighted_price_override REAL,

    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lines_project  ON devis_lines(project_id);
CREATE INDEX IF NOT EXISTS idx_lines_article  ON devis_lines(dpgf_article_id);
CREATE INDEX IF NOT EXISTS idx_lines_mapping  ON devis_lines(mapping_status);

-- -------------------------------------------------------------
-- TABLE 6 : mapping_synonyms  (apprentissage des correspondances)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping_synonyms (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    designation_entreprise  TEXT NOT NULL,
    dpgf_article_id         INTEGER NOT NULL REFERENCES dpgf_articles(id),
    source                  TEXT DEFAULT 'manual',  -- 'manual' | 'auto'
    created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(designation_entreprise, dpgf_article_id)
);

CREATE INDEX IF NOT EXISTS idx_synonyms_desig ON mapping_synonyms(designation_entreprise);

-- -------------------------------------------------------------
-- TABLE 7 : mapping_knowledge  (mémoire d'apprentissage)
-- -------------------------------------------------------------
-- Mémorise chaque validation manuelle d'Eric (choix l NNN ou sélection
-- numérique dans validate_mapping.py).
--
-- Clé d'ancrage : (source_designation, source_unit) normalisés (lowercase, stripped).
-- Garde-fou unité : si l'unité change de famille (ml→u), la correspondance
--   apprise est ignorée et une re-validation est demandée.
-- N'est JAMAIS alimentée par les auto-mappings (<80% fuzzy) — apprentissage
-- uniquement sur choix explicites d'Eric.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping_knowledge (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Clé de recherche (normalisée : lowercase + strip)
    source_designation  TEXT NOT NULL,   -- désignation normalisée du devis
    source_unit         TEXT NOT NULL,   -- unité normalisée ('' si absente)

    -- Correspondance mémorisée
    dpgf_article_id     INTEGER NOT NULL REFERENCES dpgf_articles(id),

    -- Statistiques d'usage
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    last_used           DATE    NOT NULL DEFAULT CURRENT_DATE,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(source_designation, source_unit)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_desig ON mapping_knowledge(source_designation);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit  ON mapping_knowledge(source_unit);

-- -------------------------------------------------------------
-- VUE : v_ratios  (ratios consolidés multi-projets)
-- -------------------------------------------------------------
-- Filtre : mapping_status IN ('auto','manual'), row_type='article',
--          unit_price_ht > 0, quantity NOT NULL, surface_sdo > 0
-- Alerte : ROUGE si < 3 références | ROUGE si prix négatif | ORANGE si écart > 3x
-- -------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_ratios AS
SELECT
    a.id                AS dpgf_article_id,
    a.code,
    a.designation,
    a.unit,
    a.chapter,
    a.section,
    a.ratio_type,

    COUNT(d.id)         AS nb_occurrences,

    ROUND(AVG(d.prix_normalise), 2)
                        AS avg_pu_normalise,

    ROUND(AVG(
        d.prix_normalise *
        pow(
            1.0 + p.taux_inflation,
            CAST((SELECT value FROM config WHERE key='annee_reference') AS INTEGER)
            - CAST(strftime('%Y', p.devis_date) AS INTEGER)
        )
    ), 2)               AS avg_pu_actualise,

    ROUND(
        CASE WHEN a.ratio_type = 'SURFACIQUE' THEN
            AVG(
                (d.prix_normalise * d.quantity / p.surface_sdo) *
                pow(
                    1.0 + p.taux_inflation,
                    CAST((SELECT value FROM config WHERE key='annee_reference') AS INTEGER)
                    - CAST(strftime('%Y', p.devis_date) AS INTEGER)
                )
            )
        ELSE NULL END
    , 2)                AS avg_ratio_m2_actualise,

    ROUND(MIN(d.prix_normalise), 2)  AS pu_min,
    ROUND(MAX(d.prix_normalise), 2)  AS pu_max,
    MIN(p.devis_date)                AS devis_date_min,
    MAX(p.devis_date)                AS devis_date_max,

    CASE
        WHEN COUNT(d.id) < 3              THEN 'ROUGE - Peu de références'
        WHEN MIN(d.prix_normalise) < 0    THEN 'ROUGE - Prix négatif détecté'
        WHEN MAX(d.prix_normalise) > AVG(d.prix_normalise) * 3
                                          THEN 'ORANGE - Écart important'
        ELSE 'OK'
    END                 AS alerte

FROM dpgf_articles a
JOIN devis_lines d  ON d.dpgf_article_id = a.id
JOIN projects p     ON d.project_id = p.id
WHERE
    d.mapping_status IN ('auto', 'manual')
    AND d.row_type    = 'article'
    AND d.unit_price_ht > 0
    AND d.quantity IS NOT NULL
    AND d.is_stat_valid = 1
    AND p.surface_sdo > 0
GROUP BY
    a.id, a.code, a.designation, a.unit, a.chapter, a.section, a.ratio_type;
