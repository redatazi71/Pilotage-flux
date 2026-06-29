-- =====================================================================
-- Schema SQLite V0 - pilotage-flux
-- =====================================================================
-- Perimetre L0.1 : referentiels minimaux, commandes, ordres, executions,
-- evenements. Implantation lineaire, BOM mono-niveau, portes P1 et P4.
-- Toutes les capacites, rendements et seuils sont en table `parameters`
-- (preuve du data-driven).
-- =====================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------------
-- Referentiels
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS articles (
    article_id     TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    unit           TEXT NOT NULL DEFAULT 'PCE',
    is_purchased   INTEGER NOT NULL DEFAULT 0,   -- 1 = composant achete, 0 = fabrique
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workstations (
    workstation_id TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    sequence_idx   INTEGER NOT NULL,             -- position dans la ligne lineaire
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calendars (
    calendar_id    TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    daily_minutes  INTEGER NOT NULL,             -- minutes ouvrees / jour
    working_days   TEXT NOT NULL DEFAULT 'mon,tue,wed,thu,fri'
);

-- ---------------------------------------------------------------------
-- BOM et gammes (V0 : mono-niveau, gamme lineaire = 1 op par poste)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bom_lines (
    bom_line_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_article  TEXT NOT NULL REFERENCES articles(article_id),
    child_article   TEXT NOT NULL REFERENCES articles(article_id),
    quantity        REAL NOT NULL,
    UNIQUE (parent_article, child_article)
);

CREATE TABLE IF NOT EXISTS routing_operations (
    op_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      TEXT NOT NULL REFERENCES articles(article_id),
    sequence_idx    INTEGER NOT NULL,
    workstation_id  TEXT NOT NULL REFERENCES workstations(workstation_id),
    unit_time_min   REAL NOT NULL,               -- minutes / piece
    UNIQUE (article_id, sequence_idx)
);

-- ---------------------------------------------------------------------
-- Parametres data-driven (capacites, rendements, seuils)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS parameters (
    parameter_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT NOT NULL,               -- 'global' | 'workstation' | 'article'
    scope_ref       TEXT,                        -- id du scope (NULL si global)
    name            TEXT NOT NULL,               -- ex : 'capacity_factor', 'yield_rate'
    value_num       REAL,
    value_text      TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    valid_from      TEXT NOT NULL DEFAULT (datetime('now')),
    valid_to        TEXT,                        -- NULL = courant
    UNIQUE (scope, scope_ref, name, version)
);

-- ---------------------------------------------------------------------
-- Demande
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sales_orders (
    sales_order_id  TEXT PRIMARY KEY,
    article_id      TEXT NOT NULL REFERENCES articles(article_id),
    quantity        REAL NOT NULL,
    due_date        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',   -- open | closed | cancelled
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- APS : ordres candidats puis OF
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS candidate_orders (
    candidate_id    TEXT PRIMARY KEY,
    sales_order_id  TEXT REFERENCES sales_orders(sales_order_id),
    article_id      TEXT NOT NULL REFERENCES articles(article_id),
    quantity        REAL NOT NULL,
    earliest_start  TEXT,
    latest_end      TEXT,
    status          TEXT NOT NULL DEFAULT 'candidate',  -- candidate | promoted | rejected
    zone            TEXT NOT NULL DEFAULT 'libre',      -- libre | negociable | gelee
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manufacturing_orders (
    of_id           TEXT PRIMARY KEY,
    candidate_id    TEXT REFERENCES candidate_orders(candidate_id),
    article_id      TEXT NOT NULL REFERENCES articles(article_id),
    quantity        REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'created',
        -- created | launched | in_progress | closed | cancelled
    planned_start   TEXT,
    planned_end     TEXT,
    actual_start    TEXT,
    actual_end      TEXT,
    qty_good        REAL NOT NULL DEFAULT 0,
    qty_scrap       REAL NOT NULL DEFAULT 0,
    parent_of_id    TEXT REFERENCES manufacturing_orders(of_id),
        -- non-NULL si OF issu d'une fragmentation P3 inverse forme B
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mo_parent
    ON manufacturing_orders (parent_of_id);

CREATE TABLE IF NOT EXISTS order_operations (
    of_op_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    of_id           TEXT NOT NULL REFERENCES manufacturing_orders(of_id),
    sequence_idx    INTEGER NOT NULL,
    workstation_id  TEXT NOT NULL REFERENCES workstations(workstation_id),
    unit_time_min   REAL NOT NULL,
    planned_start   TEXT,
    planned_end     TEXT,
    actual_start    TEXT,
    actual_end      TEXT,
    qty_good        REAL NOT NULL DEFAULT 0,
    qty_scrap       REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
        -- pending | running | done | skipped
    UNIQUE (of_id, sequence_idx)
);

-- ---------------------------------------------------------------------
-- MES : declarations terrain
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mes_declarations (
    declaration_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    of_op_id        INTEGER NOT NULL REFERENCES order_operations(of_op_id),
    kind            TEXT NOT NULL,              -- start | finish
    at_time         TEXT NOT NULL,
    qty_good        REAL,
    qty_scrap       REAL,
    note            TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- Event store immuable
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS event_store (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at     TEXT NOT NULL DEFAULT (datetime('now')),
    aggregate_type  TEXT NOT NULL,              -- ex : 'manufacturing_order'
    aggregate_id    TEXT NOT NULL,              -- ex : OF id
    event_type      TEXT NOT NULL,
        -- OF_CREATED | OF_LAUNCHED | OP_STARTED | OP_FINISHED | OF_CLOSED | GATE_DECISION
    payload_json    TEXT NOT NULL DEFAULT '{}',
    actor           TEXT,                        -- user/system/api
    source_module   TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_store_aggregate
    ON event_store (aggregate_type, aggregate_id, event_id);

CREATE INDEX IF NOT EXISTS idx_event_store_type
    ON event_store (event_type, event_id);

-- ---------------------------------------------------------------------
-- Tracabilite des decisions de porte (V0 : P1, P4)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gate_decisions (
    decision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    gate            TEXT NOT NULL,              -- 'P1' | 'P4'
    subject_type    TEXT NOT NULL,              -- 'sales_order' | 'manufacturing_order'
    subject_id      TEXT NOT NULL,
    decision        TEXT NOT NULL,              -- 'CREATE' | 'CLOSE' | 'REJECT'
    rule_ref        TEXT,                        -- ref a la regle appliquee
    explanation     TEXT,
    event_id        INTEGER REFERENCES event_store(event_id),
    at_time         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- V1 : zones de planification et cycles territoriaux
-- ---------------------------------------------------------------------
-- Trois zones doctrinales (§6 du cadrage) : libre (apres CBN), negociable
-- (apres P2), gelee (apres P3). Le passage entre zones se fait via les
-- portes P2 et P3, evaluees a des cadences territoriales paramétrables.
-- L1.2 pose l'infrastructure ; L1.3 et L1.5 ajoutent la logique des portes.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS planning_zones (
    zone_id         TEXT PRIMARY KEY,           -- 'libre' | 'negociable' | 'gelee'
    label           TEXT NOT NULL,
    sort_order      INTEGER NOT NULL,
    is_modifiable   INTEGER NOT NULL DEFAULT 1, -- libre/negociable: 1, gelee: 0
    description     TEXT
);

-- Seed des 3 zones standard (idempotent via INSERT OR IGNORE).
INSERT OR IGNORE INTO planning_zones (zone_id, label, sort_order, is_modifiable, description) VALUES
    ('libre',      'Zone libre',     0, 1, 'Ordres candidats avant qualification P2'),
    ('negociable', 'Zone négociable', 1, 1, 'Candidats qualifiés P2, en négociation collective'),
    ('gelee',      'Zone gelée',     2, 0, 'Engagés pour exécution apres freeze P3');

CREATE TABLE IF NOT EXISTS gate_cycles (
    cycle_id        TEXT PRIMARY KEY,           -- 'P2-2026-07', 'P3-2026-W27'
    gate            TEXT NOT NULL,              -- 'P2' | 'P3'
    period_start    TEXT NOT NULL,              -- ISO date debut periode
    period_end      TEXT NOT NULL,              -- ISO date fin periode
    cadence_days    INTEGER NOT NULL,           -- 30 (P2 mensuel) / 7 (P3 hebdo)
    status          TEXT NOT NULL DEFAULT 'planned',
        -- planned | open | closed
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    opened_at       TEXT,
    closed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_gate_cycles_gate_status
    ON gate_cycles (gate, status);

CREATE TABLE IF NOT EXISTS zone_transitions (
    transition_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type    TEXT NOT NULL,              -- 'candidate_order' | 'manufacturing_order'
    subject_id      TEXT NOT NULL,
    from_zone       TEXT,                       -- NULL si creation
    to_zone         TEXT NOT NULL REFERENCES planning_zones(zone_id),
    cycle_id        TEXT REFERENCES gate_cycles(cycle_id),
    decision        TEXT,                       -- PASS | PASS_WITH_RISK | RECALCULATE | BLOCK | FREEZE | RETOUR | ...
    rule_ref        TEXT,
    explanation     TEXT,
    actor           TEXT,
    at_time         TEXT NOT NULL DEFAULT (datetime('now')),
    event_id        INTEGER REFERENCES event_store(event_id)
);

CREATE INDEX IF NOT EXISTS idx_zone_transitions_subject
    ON zone_transitions (subject_type, subject_id, at_time);

-- Champ zone sur candidate_orders et manufacturing_orders : ajoute via
-- ALTER TABLE pour rester additif (non-destructif pour les bases existantes).
-- Les ALTER sont idempotents : tente l'ajout, ignore l'erreur si la colonne
-- existe deja (cf. les wrappers Python qui catchent OperationalError).

-- ---------------------------------------------------------------------
-- V2 : implantations paralleles et hybrides (postes alternatifs)
-- ---------------------------------------------------------------------
-- Pour modeliser plusieurs postes capables d'executer la meme operation
-- (parallele) ou un routage conditionnel (hybride), on ajoute une table
-- routing_alternatives. Chaque ligne decrit un poste alternatif pour
-- une (article, sequence_idx). routing_operations garde la version
-- principale (legacy) ; routing_alternatives la complete.

CREATE TABLE IF NOT EXISTS routing_alternatives (
    alt_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id          TEXT NOT NULL REFERENCES articles(article_id),
    sequence_idx        INTEGER NOT NULL,
    workstation_id      TEXT NOT NULL REFERENCES workstations(workstation_id),
    unit_time_min       REAL NOT NULL,
    preference_order    INTEGER NOT NULL DEFAULT 100,  -- ordre de preference (faible = meilleur)
    condition_json      TEXT NOT NULL DEFAULT '{}',    -- conditions de selection (V3)
    UNIQUE (article_id, sequence_idx, workstation_id)
);

CREATE INDEX IF NOT EXISTS idx_routing_alt_article_seq
    ON routing_alternatives (article_id, sequence_idx);

-- ---------------------------------------------------------------------
-- V2 : logistique (emplacements + transferts + files)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS locations (
    location_id     TEXT PRIMARY KEY,           -- ex: STOCK-IN, WS-1-IN, WS-1-OUT
    label           TEXT NOT NULL,
    kind            TEXT NOT NULL,              -- stock | ws_in | ws_out | shipping
    workstation_id  TEXT REFERENCES workstations(workstation_id),
    capacity        INTEGER,                    -- file max (NULL = illimite)
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS logistic_events (
    log_event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    of_id           TEXT REFERENCES manufacturing_orders(of_id),
    of_op_id        INTEGER REFERENCES order_operations(of_op_id),
    article_id      TEXT REFERENCES articles(article_id),
    qty             REAL NOT NULL,
    from_location   TEXT REFERENCES locations(location_id),
    to_location     TEXT REFERENCES locations(location_id),
    event_type      TEXT NOT NULL,
        -- transfer | feed | evacuate | ship | receive
    explanation     TEXT,
    actor           TEXT,
    at_time         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_logistic_events_of
    ON logistic_events (of_id, event_type);
CREATE INDEX IF NOT EXISTS idx_logistic_events_location
    ON logistic_events (to_location, event_type);

-- ---------------------------------------------------------------------
-- V2 : qualite (controles + non-conformites + libérations)
-- ---------------------------------------------------------------------
-- quality_controls : plans de controle (article, criterion, frequency).
-- quality_events   : evenements terrain (control PASS/FAIL, NC, retouche,
--                    liberation, blocage). Lies a un OF (et optionnellement
--                    a une op_id pour la granularite).

CREATE TABLE IF NOT EXISTS quality_controls (
    control_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      TEXT NOT NULL REFERENCES articles(article_id),
    label           TEXT NOT NULL,
    criterion       TEXT NOT NULL,                  -- ex: 'tolerance_5pct'
    sample_rate     REAL NOT NULL DEFAULT 1.0,      -- 1.0 = 100% controle
    blocking        INTEGER NOT NULL DEFAULT 1,     -- 1 si FAIL doit bloquer
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_quality_controls_article
    ON quality_controls (article_id);

CREATE TABLE IF NOT EXISTS quality_events (
    quality_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    of_id           TEXT NOT NULL REFERENCES manufacturing_orders(of_id),
    of_op_id        INTEGER REFERENCES order_operations(of_op_id),
    control_id      INTEGER REFERENCES quality_controls(control_id),
    event_type      TEXT NOT NULL,
        -- control_pass | control_fail | nc_opened | nc_rework | nc_scrap | release | block
    severity        TEXT NOT NULL DEFAULT 'normal', -- normal | high | critical
    qty_concerned   REAL,
    explanation     TEXT,
    actor           TEXT,
    at_time         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_quality_events_of
    ON quality_events (of_id, event_type);

-- ---------------------------------------------------------------------
-- V2 : consommations matiere reelles (mes_consumptions)
-- ---------------------------------------------------------------------
-- Chaque consommation matiere reelle declaree pendant l'execution d'un OF
-- est tracee en mes_consumptions. La quantite est decompte de stocks.
-- L'ecart matiere = qty_real - qty_theoretical (depuis BOM × OF.quantity)
-- est calcule en lecture, pas persiste (recalculable depuis les donnees).

CREATE TABLE IF NOT EXISTS mes_consumptions (
    consumption_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    of_id           TEXT NOT NULL REFERENCES manufacturing_orders(of_id),
    of_op_id        INTEGER REFERENCES order_operations(of_op_id),
    article_id      TEXT NOT NULL REFERENCES articles(article_id),
    qty_consumed    REAL NOT NULL,
    at_time         TEXT NOT NULL DEFAULT (datetime('now')),
    note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_mes_consumptions_of
    ON mes_consumptions (of_id, article_id);

-- ---------------------------------------------------------------------
-- V3 : couche événementielle lean (événements attendus vs réels)
-- ---------------------------------------------------------------------
-- Les événements attendus sont générés depuis une tranche gelée + le
-- lissage du contrat de flux. À chaque event réel observé en MES, on
-- recherche son pendant attendu pour qualifier l'écart.

CREATE TABLE IF NOT EXISTS expected_events (
    expected_event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id            TEXT NOT NULL REFERENCES freeze_batches(batch_id),
    contract_id         TEXT NOT NULL,
    candidate_id        TEXT NOT NULL REFERENCES candidate_orders(candidate_id),
    event_type          TEXT NOT NULL,
        -- op_start | op_finish | transfer | control | bottleneck_pass | of_close
    sequence_idx        INTEGER,                    -- ordre op (si applicable)
    workstation_id      TEXT REFERENCES workstations(workstation_id),
    expected_at         TEXT NOT NULL,              -- ISO datetime
    expected_qty        REAL,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    matched_actual_id   INTEGER REFERENCES event_store(event_id),
    matched_at          TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_expected_events_batch
    ON expected_events (batch_id, event_type);
CREATE INDEX IF NOT EXISTS idx_expected_events_candidate
    ON expected_events (candidate_id, event_type, expected_at);

CREATE TABLE IF NOT EXISTS event_deviations (
    deviation_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    expected_event_id   INTEGER REFERENCES expected_events(expected_event_id),
    actual_event_id     INTEGER REFERENCES event_store(event_id),
    candidate_id        TEXT REFERENCES candidate_orders(candidate_id),
    deviation_kind      TEXT NOT NULL,
        -- time_delta | quantity_delta | missing_actual | unexpected_actual | qty_scrap_excess
    delta_value         REAL,                       -- minutes (time) / pieces (qty)
    score               REAL,                       -- 0..1 magnitude
    cpm_margin_used     REAL,                       -- minutes absorbed by CPM
    is_absorbed         INTEGER NOT NULL DEFAULT 0, -- 1 si écart dans le niveau 0 CPM
    qualification       TEXT,                       -- low | medium | high | critical
    detected_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_event_deviations_candidate
    ON event_deviations (candidate_id, deviation_kind);
CREATE INDEX IF NOT EXISTS idx_event_deviations_kind_score
    ON event_deviations (deviation_kind, score);

-- ---------------------------------------------------------------------
-- V2 : stocks et achats ouverts
-- ---------------------------------------------------------------------
-- Modele V2 simple : un stock global par article (qty_available +
-- qty_reserved) et des achats ouverts (purchase_orders) qui projettent une
-- arrivee future. L'evaluation R-P2-05 utilise ces deux sources pour
-- determiner si un composant achete est projetable.

CREATE TABLE IF NOT EXISTS stocks (
    article_id      TEXT PRIMARY KEY REFERENCES articles(article_id),
    qty_available   REAL NOT NULL DEFAULT 0,
    qty_reserved    REAL NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id           TEXT PRIMARY KEY,           -- ex: PO-0001
    article_id      TEXT NOT NULL REFERENCES articles(article_id),
    qty_ordered     REAL NOT NULL,
    qty_received    REAL NOT NULL DEFAULT 0,
    expected_at     TEXT,                       -- ISO date arrivee prevue
    status          TEXT NOT NULL DEFAULT 'open',
        -- open | partial | received | cancelled
    supplier_ref    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    received_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchase_orders_article
    ON purchase_orders (article_id, status);

-- ---------------------------------------------------------------------
-- V1 : contrats de flux versionnes (§7 bis.1)
-- ---------------------------------------------------------------------
-- Un contrat de flux regroupe plusieurs candidates negocies sur un meme
-- horizon (hebdo en V1). Il porte un takt contractuel, un WIP cible et
-- une distribution lissee des lancements. Chaque modification cree une
-- nouvelle version. La version courante est `current_version`.

CREATE TABLE IF NOT EXISTS flux_contracts (
    contract_id     TEXT PRIMARY KEY,           -- ex: FX-0001
    horizon_label   TEXT NOT NULL,              -- ex: 2026-W27
    horizon_start   TEXT NOT NULL,              -- ISO date
    horizon_end     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
        -- draft | coherent | incoherent | frozen | archived
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flux_contract_versions (
    contract_id     TEXT NOT NULL REFERENCES flux_contracts(contract_id),
    version         INTEGER NOT NULL,
    takt_target_min REAL,                       -- minutes par piece sur l'horizon
    wip_target      REAL,                       -- WIP cible en pieces
    total_quantity  REAL NOT NULL DEFAULT 0,    -- somme des qtes des candidates
    is_coherent     INTEGER NOT NULL DEFAULT 0, -- 1 si coherence verifiee OK
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (contract_id, version)
);

CREATE TABLE IF NOT EXISTS flux_contract_links (
    contract_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    candidate_id    TEXT NOT NULL REFERENCES candidate_orders(candidate_id),
    qty_in_contract REAL NOT NULL,              -- quantite negociee (= qty candidate en V1)
    sequence_idx    INTEGER NOT NULL DEFAULT 0, -- ordre dans le contrat
    PRIMARY KEY (contract_id, version, candidate_id),
    FOREIGN KEY (contract_id, version)
        REFERENCES flux_contract_versions(contract_id, version)
);

CREATE INDEX IF NOT EXISTS idx_flux_links_candidate
    ON flux_contract_links (candidate_id);

CREATE TABLE IF NOT EXISTS flux_coherence_checks (
    check_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    workstation_id  TEXT,                       -- NULL pour les checks globaux
    metric          TEXT NOT NULL,              -- 'workstation_load' | 'takt_vs_bottleneck'
    actual_value    REAL,
    limit_value     REAL,
    is_ok           INTEGER NOT NULL,
    explanation     TEXT,
    checked_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_flux_checks_contract
    ON flux_coherence_checks (contract_id, version);

CREATE TABLE IF NOT EXISTS freeze_batches (
    batch_id        TEXT PRIMARY KEY,           -- ex: FZ-0001
    cycle_id        TEXT REFERENCES gate_cycles(cycle_id),
    horizon_start   TEXT NOT NULL,
    horizon_end     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'frozen',
        -- frozen | partial | revoked
    decision        TEXT NOT NULL,              -- FREEZE | PARTIAL_FREEZE | RENEGOTIATE
    total_quantity  REAL NOT NULL DEFAULT 0,
    contract_count  INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    explanation     TEXT,
    frozen_at       TEXT NOT NULL DEFAULT (datetime('now')),
    event_id        INTEGER REFERENCES event_store(event_id)
);

CREATE TABLE IF NOT EXISTS freeze_batch_contracts (
    batch_id        TEXT NOT NULL REFERENCES freeze_batches(batch_id),
    contract_id     TEXT NOT NULL REFERENCES flux_contracts(contract_id),
    version         INTEGER NOT NULL,           -- version figee a l'instant du freeze
    PRIMARY KEY (batch_id, contract_id),
    FOREIGN KEY (contract_id, version)
        REFERENCES flux_contract_versions(contract_id, version)
);

CREATE INDEX IF NOT EXISTS idx_freeze_batch_contract
    ON freeze_batch_contracts (contract_id);

CREATE TABLE IF NOT EXISTS flux_smoothed_launches (
    smoothed_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    candidate_id    TEXT NOT NULL,
    offset_minutes  INTEGER NOT NULL,           -- minutes depuis horizon_start
    planned_start   TEXT NOT NULL,              -- ISO datetime
    UNIQUE (contract_id, version, candidate_id),
    FOREIGN KEY (contract_id, version)
        REFERENCES flux_contract_versions(contract_id, version)
);

CREATE INDEX IF NOT EXISTS idx_flux_smoothed_contract
    ON flux_smoothed_launches (contract_id, version);

-- ---------------------------------------------------------------------
-- V1 : moteur de regles, evaluations de portes, registre risk_debt
-- ---------------------------------------------------------------------
-- Le moteur de regles est volontairement minimal en V1 : les regles sont
-- declarees en table (data-driven), referencees par leur `criterion` qui
-- pointe sur un evaluateur Python. Les seuils sont dans `parameters`.
-- L'expression JSON est descriptive ; le DSL plein (filtre dual) viendra
-- en V3. Toute logique metier reste hors-code via decision_rules + parameters.

CREATE TABLE IF NOT EXISTS decision_rules (
    rule_id         TEXT NOT NULL,
    gate            TEXT NOT NULL,              -- 'P2' | 'P3' | 'P4'
    criterion       TEXT NOT NULL,              -- nom de l'evaluateur Python
    label           TEXT NOT NULL,
    expression_json TEXT NOT NULL DEFAULT '{}', -- description / metadata
    severity        TEXT NOT NULL DEFAULT 'normal',  -- normal | high | blocking
    version         INTEGER NOT NULL DEFAULT 1,
    valid_from      TEXT NOT NULL DEFAULT (datetime('now')),
    valid_to        TEXT,                       -- NULL = courante
    PRIMARY KEY (rule_id, version)
);

CREATE INDEX IF NOT EXISTS idx_decision_rules_gate
    ON decision_rules (gate, valid_to);

-- Seed des 5 regles standard de la porte P2 (cadrage §6).
INSERT OR IGNORE INTO decision_rules
    (rule_id, gate, criterion, label, expression_json, severity)
VALUES
    ('R-P2-01', 'P2', 'referentials_present',
     'Reférentiels présents et complets',
     '{"checks": ["article_exists", "routing_exists"]}', 'blocking'),
    ('R-P2-02', 'P2', 'internal_coherence',
     'Cohérence interne (quantité, postes)',
     '{"checks": ["positive_qty", "workstations_exist"]}', 'blocking'),
    ('R-P2-03', 'P2', 'forecast_validity',
     'Validité prévisionnelle (SO open, due_date)',
     '{"checks": ["so_open", "due_date_future"]}', 'high'),
    ('R-P2-04', 'P2', 'bottleneck_capacity',
     'Charge goulot vs capacité',
     '{"thresholds": ["p2_capacity_risk_ratio", "p2_capacity_block_ratio"]}', 'high'),
    ('R-P2-05', 'P2', 'components_projectable',
     'Composants achetés projetables (stocks/achats)',
     '{"checks": ["all_purchased_have_supply"]}', 'normal');

-- Seed des 4 regles standard de la porte P3 (cadrage §17).
INSERT OR IGNORE INTO decision_rules
    (rule_id, gate, criterion, label, expression_json, severity)
VALUES
    ('R-P3-01', 'P3', 'contract_coherence',
     'Cohérence individuelle du contrat',
     '{"checks": ["current_version_is_coherent"]}', 'blocking'),
    ('R-P3-02', 'P3', 'candidates_negociable',
     'Tous candidates en zone négociable',
     '{"checks": ["all_candidates_in_negociable_zone"]}', 'blocking'),
    ('R-P3-03', 'P3', 'no_open_risk_debts',
     'Aucune risk_debt ouverte sur les candidates',
     '{"checks": ["no_open_debt_for_each_candidate"]}', 'blocking'),
    ('R-P3-04', 'P3', 'no_overlapping_freeze',
     'Pas de tranche gelée existante chevauchante',
     '{"checks": ["no_existing_freeze_batch_overlap"]}', 'high');

CREATE TABLE IF NOT EXISTS gate_evaluations (
    eval_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    gate            TEXT NOT NULL,
    subject_type    TEXT NOT NULL,              -- 'candidate_order'
    subject_id      TEXT NOT NULL,
    cycle_id        TEXT REFERENCES gate_cycles(cycle_id),
    rule_id         TEXT NOT NULL,
    rule_version    INTEGER NOT NULL,
    criterion       TEXT NOT NULL,
    outcome         TEXT NOT NULL,              -- PASS | RISK | RECALCULATE | BLOCK
    score           REAL,
    explanation     TEXT,
    evaluated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gate_eval_subject
    ON gate_evaluations (subject_type, subject_id, evaluated_at);
CREATE INDEX IF NOT EXISTS idx_gate_eval_gate_outcome
    ON gate_evaluations (gate, outcome);

CREATE TABLE IF NOT EXISTS gate_decisions_v1 (
    decision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    gate            TEXT NOT NULL,
    subject_type    TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    cycle_id        TEXT REFERENCES gate_cycles(cycle_id),
    decision        TEXT NOT NULL,              -- PASS | PASS_WITH_RISK | RECALCULATE | BLOCK | FREEZE
    risk_count      INTEGER NOT NULL DEFAULT 0,
    explanation     TEXT,
    evaluated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gate_decisions_v1_subject
    ON gate_decisions_v1 (subject_type, subject_id, evaluated_at);

CREATE TABLE IF NOT EXISTS risk_debt_register (
    risk_debt_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    TEXT NOT NULL REFERENCES candidate_orders(candidate_id),
    criterion       TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    score           REAL NOT NULL,              -- score_risque ∈ [0,1]
    deadline        TEXT NOT NULL,              -- ISO date limite extinction (≤ entree P3)
    status          TEXT NOT NULL DEFAULT 'open',  -- open | extinct | expired
    explanation     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    extinguished_at TEXT,
    extinction_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_risk_debt_candidate
    ON risk_debt_register (candidate_id, status);

-- ---------------------------------------------------------------------
-- V1 : aplatissement BOM et pegging multi-niveau
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS flattened_bom_lines (
    flat_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    root_article        TEXT NOT NULL REFERENCES articles(article_id),
    component_article   TEXT NOT NULL REFERENCES articles(article_id),
    cumulative_quantity REAL NOT NULL,
    depth_level         INTEGER NOT NULL,
    is_leaf             INTEGER NOT NULL DEFAULT 0,    -- 1 si composant achete
    path                TEXT NOT NULL,                  -- /ART-A/SEMI-1/COMP-X
    computed_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (root_article, path)
);

CREATE INDEX IF NOT EXISTS idx_flattened_bom_root
    ON flattened_bom_lines (root_article);
CREATE INDEX IF NOT EXISTS idx_flattened_bom_component
    ON flattened_bom_lines (component_article);

CREATE TABLE IF NOT EXISTS pegging_links (
    pegging_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL,   -- 'sales_order' | 'candidate_order' | 'manufacturing_order'
    source_id       TEXT NOT NULL,
    target_type     TEXT NOT NULL,   -- 'candidate_order' | 'manufacturing_order' | 'component'
    target_id       TEXT NOT NULL,
    article_id      TEXT REFERENCES articles(article_id),
    quantity        REAL NOT NULL,
    depth           INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pegging_source
    ON pegging_links (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_pegging_target
    ON pegging_links (target_type, target_id);

-- ---------------------------------------------------------------------
-- Metadata du run de simulation
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS run_metadata (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
