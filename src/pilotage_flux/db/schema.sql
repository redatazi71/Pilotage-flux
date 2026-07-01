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
    consuming_operation_idx INTEGER,
        -- V13.1 : sequence_idx de l'op de routing du parent qui consomme
        -- ce composant. NULL = legacy (consommé à l'op 1, comportement
        -- historique). Non-null = l'op N peut démarrer dès que ce
        -- composant est prêt, sans attendre les autres.
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
    rejected_at     TEXT,
        -- Point 2 paper : timestamp si la SO a été rejetée par le client
        -- (livraison > due_date + late_threshold_days)
    rejection_reason TEXT,
        -- 'late_beyond_threshold' | 'unfeasible' | 'manual'
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

CREATE TABLE IF NOT EXISTS root_cause_rules (
    rule_id         TEXT NOT NULL,
    cause           TEXT NOT NULL,
        -- production_breakdown | quality_defect | supply_shortage |
        -- logistic_delay | demand_change | bottleneck_overload | other
    label           TEXT NOT NULL,
    weight          REAL NOT NULL DEFAULT 0.5,  -- prior 0..1
    confidence      REAL NOT NULL DEFAULT 0.5,  -- mise à jour bayésienne
    domain          TEXT,                       -- production | quality | supply | logistic | demand
    applies_to_kind TEXT,                       -- time_delta | quantity_delta | ...
    version         INTEGER NOT NULL DEFAULT 1,
    valid_from      TEXT NOT NULL DEFAULT (datetime('now')),
    valid_to        TEXT,
    PRIMARY KEY (rule_id, version)
);

-- Seed des 6 causes racines standard (§18 du cadrage)
INSERT OR IGNORE INTO root_cause_rules
    (rule_id, cause, label, weight, confidence, domain, applies_to_kind)
VALUES
    ('R-RC-01', 'production_breakdown', 'Panne ou arrêt poste',
     0.6, 0.5, 'production', 'time_delta'),
    ('R-RC-02', 'quality_defect', 'Défaut qualité bloquant',
     0.4, 0.5, 'quality', 'qty_scrap_excess'),
    ('R-RC-03', 'supply_shortage', 'Rupture composant achaté',
     0.7, 0.5, 'supply', 'missing_actual'),
    ('R-RC-04', 'logistic_delay', 'Retard logistique / file',
     0.5, 0.5, 'logistic', 'time_delta'),
    ('R-RC-05', 'demand_change', 'Modification demande client',
     0.3, 0.5, 'demand', 'quantity_delta'),
    ('R-RC-06', 'bottleneck_overload', 'Surcharge goulot dynamique',
     0.5, 0.5, 'production', 'time_delta');

CREATE TABLE IF NOT EXISTS event_deviation_causes (
    attach_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    deviation_id        INTEGER NOT NULL REFERENCES event_deviations(deviation_id),
    rule_id             TEXT NOT NULL,
    rule_version        INTEGER NOT NULL,
    score               REAL NOT NULL,          -- weight * confidence à l'instant de l'attache
    posterior           REAL,                   -- mise à jour bayésienne après évidence
    explanation         TEXT,
    confirmed           INTEGER NOT NULL DEFAULT 0,  -- 1 si validé manuellement
    attached_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_event_deviation_causes_dev
    ON event_deviation_causes (deviation_id);
CREATE INDEX IF NOT EXISTS idx_event_deviation_causes_rule
    ON event_deviation_causes (rule_id);

-- ---------------------------------------------------------------------
-- V3 : filtre dual de tolerances (run-time) - §7 bis.4
-- ---------------------------------------------------------------------
-- Le filtre dual combine score (magnitude) × frequence (recurrence sur
-- fenetre) avec latence avant declenchement. Decision proportionnee.

CREATE TABLE IF NOT EXISTS tolerance_filter_decisions (
    decision_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deviation_id        INTEGER NOT NULL REFERENCES event_deviations(deviation_id),
    candidate_id        TEXT REFERENCES candidate_orders(candidate_id),
    score_magnitude     REAL NOT NULL,
    frequency_in_window INTEGER NOT NULL,        -- nb deviations similaires dans la fenetre
    score_combined      REAL NOT NULL,           -- magnitude × ponderation_frequence
    action_level        TEXT NOT NULL,
        -- inform | watch | correct_local | replan_local | escalate | replan_global
    latency_minutes     INTEGER NOT NULL DEFAULT 0,
    triggered_at        TEXT,                    -- NULL = en attente (latence)
    decided_at          TEXT NOT NULL DEFAULT (datetime('now')),
    source              TEXT NOT NULL DEFAULT 'tolerance'
        -- 'tolerance' (chemin normal filtre dual) | 'memory_shortcut'
        --   (V13.C : recette apprise court-circuite l'analyse)
);

CREATE INDEX IF NOT EXISTS idx_tolerance_filter_dev
    ON tolerance_filter_decisions (deviation_id);
CREATE INDEX IF NOT EXISTS idx_tolerance_filter_action
    ON tolerance_filter_decisions (action_level, triggered_at);

-- ---------------------------------------------------------------------
-- V3 : filtre dual de memoire P4 (apprentissage) - §7 bis.5
-- ---------------------------------------------------------------------
-- À la cloture P4, on capture la "recette" (combinaison ecart - cause -
-- decision - resultat) et un score decide si elle est :
--   (a) journalisee seulement (pour audit)
--   (b) retenue pour apprentissage : enrichit l'historique et permet la
--       mise a jour des seuils/regles.

CREATE TABLE IF NOT EXISTS memory_recipes (
    recipe_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    of_id               TEXT REFERENCES manufacturing_orders(of_id),
    candidate_id        TEXT REFERENCES candidate_orders(candidate_id),
    deviation_signature TEXT NOT NULL,          -- ex: 'time_delta|R-RC-04|escalate'
    deviation_kind      TEXT,
    cause_rule_id       TEXT,
    action_level        TEXT,
    outcome             TEXT,                   -- success | failure | partial
    score_significance  REAL,                   -- 0..1 significativite statistique
    score_recurrence    REAL,                   -- 0..1 frequence dans l'historique
    score_combined      REAL,
    is_retained         INTEGER NOT NULL DEFAULT 0,  -- 1 si retenue pour apprentissage
    retention_reason    TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_recipes_signature
    ON memory_recipes (deviation_signature, is_retained);
CREATE INDEX IF NOT EXISTS idx_memory_recipes_of
    ON memory_recipes (of_id);

CREATE TABLE IF NOT EXISTS memory_filter_decisions (
    decision_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id           INTEGER NOT NULL REFERENCES memory_recipes(recipe_id),
    decision            TEXT NOT NULL,          -- log_only | retain | update_rule
    target_rule_id      TEXT,                   -- regle ajustee si update_rule
    parameter_updated   TEXT,                   -- nom parametre modifie
    old_value           REAL,
    new_value           REAL,
    explanation         TEXT,
    decided_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_filter_recipe
    ON memory_filter_decisions (recipe_id);

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

-- ---------------------------------------------------------------------
-- V12.3 — Architecture cybernétique : approval queue
-- ---------------------------------------------------------------------
-- Toute décision de niveau L3 ou L4 est enqueue ici en statut
-- 'pending' jusqu'à approbation humaine (ou auto_timeout en simulation).
-- Les niveaux L1 et L2 ne passent JAMAIS par cette table (autonomes).

CREATE TABLE IF NOT EXISTS approval_queue (
    queue_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     INTEGER NOT NULL
                      REFERENCES tolerance_filter_decisions(decision_id),
    autonomy_level  TEXT NOT NULL,
        -- L1_absorbed | L2_auto_adjust | L3_local_replan_approval | L4_global_replan_approval
        -- En pratique seuls L3 et L4 apparaissent ici (L1/L2 = autonomes)
    status          TEXT NOT NULL DEFAULT 'pending',
        -- pending | approved | rejected | auto_timeout
    submitted_at    TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at     TEXT,
    approved_by     TEXT,
        -- 'human:<user>' ou 'auto:timeout' ou 'auto:simulation:<lag_min>'
    approval_lag_min REAL,
        -- temps réel écoulé entre submitted_at et approved_at
    notes           TEXT,
    UNIQUE(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_approval_queue_status
    ON approval_queue (status);
CREATE INDEX IF NOT EXISTS idx_approval_queue_level
    ON approval_queue (autonomy_level);

-- ---------------------------------------------------------------------
-- V12.4 — Workflow humain : rôles, audit trail, notifications
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_roles (
    user_id        TEXT PRIMARY KEY,
    role           TEXT NOT NULL,
        -- operator | supervisor | admin
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    modified_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approval_audit_log (
    log_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id       INTEGER REFERENCES approval_queue(queue_id),
    event_type     TEXT NOT NULL,
        -- submitted | approved | rejected | escalated |
        -- auto_timeout | role_changed | note_added
    actor          TEXT NOT NULL,
        -- 'human:<user>' ou 'auto:<source>'
    details        TEXT,
        -- JSON sérialisé (libre)
    occurred_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_queue
    ON approval_audit_log (queue_id);
CREATE INDEX IF NOT EXISTS idx_audit_event
    ON approval_audit_log (event_type);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    target          TEXT NOT NULL,
        -- 'role:operator' | 'role:supervisor' | 'user:<user_id>'
    kind            TEXT NOT NULL,
        -- pending_approval | overdue | escalated | rejected_with_note
    queue_id        INTEGER REFERENCES approval_queue(queue_id),
    message         TEXT NOT NULL,
    read_at         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notif_target_unread
    ON notifications (target, read_at);

-- ---------------------------------------------------------------------
-- Goldilocks #4 : Contrat de Production PC=(T, Ep, Er, C, O) au grain
-- opération (cadrage v1.3). Chaque order_operation porte un PC qui
-- engage le système sur 5 dimensions :
--   T  : Temps cible (cycle minutes de l'op)
--   Ep : Engagement procédural (rendement qualité, conformité)
--   Er : Engagement de résultats (quantité bonne livrée)
--   C  : Coûts cible (€ par op = main d'œuvre + matière)
--   O  : Origine (SO/candidate/flux_contract dont l'op découle)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS production_contracts (
    pc_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    of_id              TEXT NOT NULL REFERENCES manufacturing_orders(of_id),
    of_op_id           INTEGER NOT NULL REFERENCES order_operations(of_op_id),
    -- T : Temps cible
    target_time_min    REAL NOT NULL,
    actual_time_min    REAL,
    tolerance_pct_time REAL NOT NULL DEFAULT 0.10,
    -- Ep : Engagement procédural (rendement qualité attendu)
    target_quality_rate     REAL NOT NULL DEFAULT 1.0,
    actual_quality_rate     REAL,
    tolerance_pct_quality   REAL NOT NULL DEFAULT 0.05,
    -- Er : Engagement de résultats (quantité bonne)
    target_qty_good         REAL NOT NULL,
    actual_qty_good         REAL,
    tolerance_pct_quantity  REAL NOT NULL DEFAULT 0.03,
    -- C : Coûts cible (€)
    target_cost             REAL NOT NULL DEFAULT 0,
    actual_cost             REAL,
    tolerance_pct_cost      REAL NOT NULL DEFAULT 0.10,
    -- O : Origine (référence remontée jusqu'à la source de demande)
    origin_kind             TEXT NOT NULL,
        -- 'sales_order' | 'candidate' | 'flux_contract'
    origin_ref              TEXT NOT NULL,
    -- Statut PC
    status                  TEXT NOT NULL DEFAULT 'open',
        -- 'open' | 'fulfilled' | 'breached'
    breach_dimensions       TEXT,
        -- liste CSV ('T,Er') des dimensions hors tolérance
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at               TEXT,
    UNIQUE (of_op_id)
);

CREATE INDEX IF NOT EXISTS idx_pc_of
    ON production_contracts (of_id);
CREATE INDEX IF NOT EXISTS idx_pc_origin
    ON production_contracts (origin_kind, origin_ref);
CREATE INDEX IF NOT EXISTS idx_pc_status
    ON production_contracts (status);

-- ---------------------------------------------------------------------
-- MACRS Couche 1 — Matrice d'incidence causale enrichie
--   46 racines × 7 catégories Δ = 175 cellules d'incidence binaire.
--   Référence : matrice_incidence_causale.md (couche 1).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macrs_categories (
    categorie_code  TEXT PRIMARY KEY,    -- Mat | Cap | Op | Qual | Temp | Info | Sync
    label           TEXT NOT NULL,
    definition      TEXT NOT NULL,
    ordre           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS macrs_racines (
    racine_id           TEXT PRIMARY KEY,    -- R001..R046, identifiant stable
    domaine             TEXT NOT NULL,
        -- demande | approvisionnement | logistique | production | qualite
    sous_domaine        TEXT NOT NULL,
    label               TEXT NOT NULL,
    predictibilite      TEXT NOT NULL,
        -- 'forte' | 'moyenne' | 'faible'
    c1_precurseur       TEXT NOT NULL,    -- 'O' | 'N'
    c2_cumulative       TEXT NOT NULL,    -- 'O' | 'N' | 'P' (partiel)
    c3_aleatoire        TEXT NOT NULL,    -- 'non' | 'partiel' | 'dominant'
    mecanisme           TEXT,
    observabilite       TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_macrs_racines_domaine
    ON macrs_racines (domaine, sous_domaine);

-- Incidence binaire : 1 ligne par couple actif (175 attendus).
CREATE TABLE IF NOT EXISTS macrs_incidence (
    racine_id       TEXT NOT NULL REFERENCES macrs_racines(racine_id),
    categorie_code  TEXT NOT NULL REFERENCES macrs_categories(categorie_code),
    PRIMARY KEY (racine_id, categorie_code)
);

CREATE INDEX IF NOT EXISTS idx_macrs_incidence_cat
    ON macrs_incidence (categorie_code);

-- ---------------------------------------------------------------------
-- MACRS Couche 2 — Matrice opérationnelle dynamique (cellules)
--
-- Une cellule existe pour chaque couple (racine, catégorie) actif
-- en Couche 1. Cycle de vie :
--
--   INACTIVE  (jamais matérialisée — implicite via incidence = 0)
--   INCOMING  (créée au seed, en attente du 1er événement)
--   OBSERVING (1+ événement observé, sous-domaine n'a pas atteint K)
--   ACTIVE    (sous-domaine a atteint K et la cellule a 1+ événement)
--
-- Transition unidirectionnelle dans une simulation. Le seuil K
-- s'applique au **sous-domaine entier** : toutes les cellules
-- d'un même sous-domaine basculent en ACTIVE simultanément
-- (cf. matrice_operationnelle_specification.md §3.3).
--
-- Les agrégats temporels W_courte/W_longue/bins/cumul sont ajoutés
-- dans la migration A.3 (compteurs simples ici en A.2).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS causal_cells (
    cell_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    racine_id               TEXT NOT NULL REFERENCES macrs_racines(racine_id),
    categorie_code          TEXT NOT NULL REFERENCES macrs_categories(categorie_code),
    status                  TEXT NOT NULL DEFAULT 'INCOMING',
        -- 'INCOMING' | 'OBSERVING' | 'ACTIVE'
    n_events_total          INTEGER NOT NULL DEFAULT 0,
    first_event_at          TEXT,
    last_event_at           TEXT,
    transitioned_observing_at  TEXT,
    transitioned_active_at  TEXT,
    -- A.3 : histogramme cumul des délais (8 bins, cf. spec §2.4)
    bin_cumul_b0_1h         INTEGER NOT NULL DEFAULT 0,
    bin_cumul_b1_4h         INTEGER NOT NULL DEFAULT 0,
    bin_cumul_b4_24h        INTEGER NOT NULL DEFAULT 0,
    bin_cumul_b1_3j         INTEGER NOT NULL DEFAULT 0,
    bin_cumul_b3_7j         INTEGER NOT NULL DEFAULT 0,
    bin_cumul_b7_14j        INTEGER NOT NULL DEFAULT 0,
    bin_cumul_b14_30j       INTEGER NOT NULL DEFAULT 0,
    bin_cumul_b30_90j       INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (racine_id, categorie_code)
);

CREATE INDEX IF NOT EXISTS idx_causal_cells_status
    ON causal_cells (status);
CREATE INDEX IF NOT EXISTS idx_causal_cells_racine
    ON causal_cells (racine_id);

-- ---------------------------------------------------------------------
-- MACRS Couche 2 — A.3 : files événementielles + agrégats temporels
--
-- causal_events : liste atomique d'événements par cellule. Sert de
-- file glissante : les agrégats W_courte (30j) et W_longue (90j)
-- sont calculés à la demande par filtrage `occurred_at >= now - W`.
-- Les événements expirés restent en base (cf. §4.3 de la spec :
-- conservation pour traçabilité, modification rétroactive
-- éventuelle des fenêtres, audit complet).
--
-- causal_cells gagne 8 colonnes bin pour le **cumul** de
-- l'histogramme des délais (cumul ne s'expire pas).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS causal_events (
    cell_event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id         INTEGER NOT NULL REFERENCES causal_cells(cell_id),
    occurred_at     TEXT NOT NULL,    -- ISO datetime simulé
    delay_bin       TEXT,             -- b0_1h | b1_4h | b4_24h | b1_3j
                                       -- | b3_7j | b7_14j | b14_30j | b30_90j
    delay_hours     REAL,             -- valeur brute (peut être NULL)
    impact_score    REAL,             -- score pondéré optionnel
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_causal_events_cell_time
    ON causal_events (cell_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_causal_events_time
    ON causal_events (occurred_at);

-- ---------------------------------------------------------------------
-- MACRS Couche 2 — A.4 : snapshots hebdo + versioning des poids
--
-- weight_versions : jeu de coefficients de pondération utilisés par
-- les indicateurs dérivés (Option B cause/racine, Option D info/
-- correction). Versionnage explicite cf. spec §5. Une seule version
-- active à tout instant (status = 'active'), les autres restent
-- disponibles ('experimental' ou 'archived') pour comparaison.
--
-- causal_cell_snapshots : photographies hebdomadaires immuables des
-- cellules ACTIVE (cf. spec §3.5). Permettent de reconstituer
-- l'évolution de la matrice. Référencent éventuellement la
-- weight_version active au moment de la prise. Ne sont JAMAIS
-- utilisées par le Pareto courant.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weight_versions (
    weight_version_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    label               TEXT NOT NULL UNIQUE,
    description         TEXT NOT NULL,
    coefficients_json   TEXT NOT NULL,
        -- JSON sérialisé : {indicateur_key: coefficient_float, ...}
    status              TEXT NOT NULL DEFAULT 'experimental',
        -- 'active' | 'archived' | 'experimental'
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    activated_at        TEXT,
    archived_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_weight_versions_status
    ON weight_versions (status);

CREATE TABLE IF NOT EXISTS causal_cell_snapshots (
    snapshot_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id                  INTEGER NOT NULL REFERENCES causal_cells(cell_id),
    racine_id                TEXT NOT NULL,
    categorie_code           TEXT NOT NULL,
    status                   TEXT NOT NULL,
    snapshot_at              TEXT NOT NULL,
    n_w_courte               INTEGER NOT NULL,
    n_w_longue               INTEGER NOT NULL,
    n_cumul                  INTEGER NOT NULL,
    ratio_emergence          REAL,
    histogram_w_courte_json  TEXT NOT NULL,
    histogram_w_longue_json  TEXT NOT NULL,
    histogram_cumul_json     TEXT NOT NULL,
    weight_version_id        INTEGER REFERENCES weight_versions(weight_version_id),
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_snapshots_cell
    ON causal_cell_snapshots (cell_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_time
    ON causal_cell_snapshots (snapshot_at);

-- ---------------------------------------------------------------------
-- Moteur Delta unifié (B.1)
--
-- Vocabulaire d'action à 6 niveaux du CDC §11, mappé sur les 4 niveaux
-- du cadrage v1.3 §3.11 (N1 absorption / N2 ajust. auto / N3 replan
-- locale / N4 replan complète). Le flag requires_human marque les
-- niveaux soumis à la subsidiarité humaine (cadrage : N3/N4).
--
--   L1 informer            → N1 (passive, scope=none)
--   L2 surveiller          → N1 (passive, scope=none)
--   L3 corriger_local      → N2 (automatique, scope=local)
--   L4 replanifier_local   → N3 (humain, scope=local)
--   L5 escalader           → N3 (humain, transition)
--   L6 replanifier_global  → N4 (humain, scope=global)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS delta_action_levels (
    niveau_code     TEXT PRIMARY KEY,    -- 'L1'..'L6'
    label           TEXT NOT NULL,
    cadrage_level   INTEGER NOT NULL,    -- 1..4 (mapping cadrage v1.3)
    requires_human  INTEGER NOT NULL,    -- 0|1
    scope           TEXT NOT NULL,       -- 'none'|'local'|'global'
    description     TEXT NOT NULL,
    ordre           INTEGER NOT NULL UNIQUE  -- 1..6, ordre d'escalade
);

-- Décisions Delta : une par déviation, lien optionnel vers approval_queue.
CREATE TABLE IF NOT EXISTS delta_decisions (
    delta_decision_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    deviation_id        INTEGER REFERENCES event_deviations(deviation_id),
    niveau_code         TEXT NOT NULL REFERENCES delta_action_levels(niveau_code),
    racine_id           TEXT REFERENCES macrs_racines(racine_id),
    categorie_code      TEXT REFERENCES macrs_categories(categorie_code),
    score_magnitude     REAL,
    frequency           REAL,
    decided_at          TEXT NOT NULL,
    executed_at         TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
        -- 'pending' | 'executed' | 'rejected' | 'expired'
    approval_queue_id   INTEGER REFERENCES approval_queue(queue_id),
    explanation         TEXT,
    actor               TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_delta_decisions_deviation
    ON delta_decisions (deviation_id);
CREATE INDEX IF NOT EXISTS idx_delta_decisions_niveau
    ON delta_decisions (niveau_code);
CREATE INDEX IF NOT EXISTS idx_delta_decisions_status
    ON delta_decisions (status);
CREATE INDEX IF NOT EXISTS idx_delta_decisions_racine
    ON delta_decisions (racine_id, categorie_code);

-- ---------------------------------------------------------------------
-- V13.H — Contrats de production (zone négociable enrichie)
-- ---------------------------------------------------------------------
-- Un contrat de production est le lien entre une demande client (SO)
-- et sa promesse de fabrication. Il enrichit le simple candidate_order
-- avec les cibles doctrinales (takt, WIP, buffers), le dossier de
-- faisabilité (charges/capa par WS, goulot identifié, ρ), et l'état
-- des 5 flux (physique, info, décisionnel, documentaire, qualité).
--
-- Le contrat est créé lors de la promotion candidate → OF (P3 freeze).
-- Il persiste après cette étape pour tracer les cibles et permettre
-- l'audit doctrinal (takt réellement tenu vs cible, WIP écart, etc.).
-- Note : la table `production_contracts` existante porte les PCs
-- atomiques doctrine Goldilocks (T, Ep, Er, C, O) par of_op_id. La
-- table `demand_contracts` ci-dessous est un niveau au-dessus : un
-- contrat par SO/demande enrichi avec les cibles zone négociable
-- (takt, WIP, bottleneck, appro, 5 flux). Différents concepts,
-- articulés via candidate_id / of_id.
CREATE TABLE IF NOT EXISTS demand_contracts (
    contract_id         TEXT PRIMARY KEY,
    sales_order_id      TEXT NOT NULL
        REFERENCES sales_orders(sales_order_id),
    candidate_id        TEXT REFERENCES candidate_orders(candidate_id),
    article_id          TEXT NOT NULL REFERENCES articles(article_id),
    quantity            REAL NOT NULL,
    delivery_deadline   TEXT NOT NULL,
    -- Cibles doctrinales (issues de V13.E feasibility)
    takt_target_min     REAL,       -- min/unité (débit goulot cible)
    wip_target          REAL,       -- unités (Little : throughput × cycle)
    bottleneck_ws       TEXT,
    buffer_days         INTEGER DEFAULT 2,
    -- Dossier de faisabilité
    charge_total_min    REAL,       -- charge totale toutes ops
    charge_bottleneck_min REAL,     -- charge sur le goulot
    capa_needed_min     REAL,       -- capa min requise sur le goulot
    wip_predicted       REAL,       -- WIP prévu (Little)
    rho_bottleneck      REAL,       -- saturation goulot du run (∈ [0,1])
    feasible            INTEGER NOT NULL DEFAULT 0,
        -- 0 = infeasible (charge > capa), 1 = ok
    -- Approvisionnement (à date de création du contrat)
    appro_status        TEXT,       -- 'ok' | 'partial' | 'missing'
    -- État des 5 flux (jumeau numérique)
    flux_physical_status    TEXT,   -- 'planned' | 'active' | 'closed'
    flux_info_ready         INTEGER DEFAULT 0,
        -- 1 = event sourcing prêt (dual_tolerance seuils calibrés)
    flux_decision_status    TEXT,   -- 'auto' | 'human_review'
    flux_doc_status         TEXT,   -- 'draft' | 'signed' | 'archived'
    flux_quality_status     TEXT,   -- 'planned' | 'in_control' | 'nc'
    -- Planning
    scheduled_start_day INTEGER,
    scheduled_end_day   INTEGER,
    -- Traçabilité
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    signed_at           TEXT,       -- moment où le contrat est freezé
    closed_at           TEXT
);

CREATE INDEX IF NOT EXISTS idx_demand_contracts_so
    ON demand_contracts (sales_order_id);
CREATE INDEX IF NOT EXISTS idx_demand_contracts_candidate
    ON demand_contracts (candidate_id);
CREATE INDEX IF NOT EXISTS idx_demand_contracts_deadline
    ON demand_contracts (delivery_deadline);
CREATE INDEX IF NOT EXISTS idx_demand_contracts_feasible
    ON demand_contracts (feasible);

-- ---------------------------------------------------------------------
-- V13.I — Contrats de flux hebdomadaires (agrégation demand_contracts)
-- ---------------------------------------------------------------------
-- Un contrat de flux hebdomadaire agrège les demand_contracts dont la
-- livraison tombe dans la semaine ISO (year-week). Il porte les cibles
-- doctrinales AGRÉGÉES : takt hebdo = Σ_qty / capa_goulot_semaine,
-- WIP cible = Little agrégée, goulot du mix hebdo (recalculé).
--
-- La granularité hebdomadaire est le standard industriel (ISA-95 Level 4
-- planning tactique = jour ↔ semaine). Sur horizon 60j = ~9 semaines,
-- horizon 120j = ~17 semaines.
CREATE TABLE IF NOT EXISTS weekly_flux_contracts (
    weekly_id           TEXT PRIMARY KEY,       -- WFC-YYYYWW-xxx
    year_iso            INTEGER NOT NULL,
    week_iso            INTEGER NOT NULL,
    week_start_date     TEXT NOT NULL,          -- lundi ISO YYYY-MM-DD
    -- Cibles doctrinales AGRÉGÉES du mix hebdo
    total_quantity      REAL NOT NULL,           -- Σ demand_contracts.qty
    n_contracts         INTEGER NOT NULL,        -- nb demand_contracts
    bottleneck_ws       TEXT,                    -- goulot dyn du mix hebdo
    takt_target_min     REAL,                    -- Σ_capa / Σ_qty
    wip_target          REAL,                    -- Little agrégée
    rho_bottleneck      REAL,                    -- charge / capa goulot
    feasible            INTEGER NOT NULL DEFAULT 0,
    -- Capacité disponible sur le goulot semaine (min)
    capa_goulot_week    REAL,                    -- daily_min × 5j × capa
    -- Charge cumulée sur le goulot (min) pour ce mix hebdo
    charge_goulot_week  REAL,
    -- Statut du contrat hebdo (jumeau numérique 5 flux aggregé)
    status              TEXT NOT NULL DEFAULT 'draft',
        -- 'draft' | 'signed' | 'active' | 'closed' | 'renegotiated'
    -- Traçabilité
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    signed_at           TEXT,
    closed_at           TEXT
);

-- Lien N-N entre weekly_flux_contracts et demand_contracts.
CREATE TABLE IF NOT EXISTS weekly_flux_contract_lines (
    weekly_id           TEXT NOT NULL
        REFERENCES weekly_flux_contracts(weekly_id),
    contract_id         TEXT NOT NULL
        REFERENCES demand_contracts(contract_id),
    PRIMARY KEY (weekly_id, contract_id)
);

CREATE INDEX IF NOT EXISTS idx_weekly_flux_year_week
    ON weekly_flux_contracts (year_iso, week_iso);
CREATE INDEX IF NOT EXISTS idx_weekly_flux_lines_contract
    ON weekly_flux_contract_lines (contract_id);

-- ---------------------------------------------------------------------
-- V13.J — Jumeau numérique 5 flux (snapshot par contrat hebdo × jour)
-- ---------------------------------------------------------------------
-- État capturé à un instant t (jour de simulation) pour un contrat de
-- flux hebdomadaire. Persiste l'état des 5 flux au sens VSM :
--   1. Physique      = WIP réel + OFs en cours + livrés cumulés
--   2. Informationnel = event sourcing (deviations, actions, causes)
--   3. Décisionnel   = décisions replan / escalades humaines
--   4. Documentaire  = état contractuel (draft/signed/closed)
--   5. Qualité       = scrap cumulé, NCs, yield moyen
--
-- Persistance : 1 ligne par (weekly_id, snapshot_day). Permet :
--   - Audit post-mortem (comment le contrat s'est comporté jour par jour)
--   - Détection de dérives précoces (écart vs cible)
--   - Replay / analyse contre-factuelle
CREATE TABLE IF NOT EXISTS flux_twin_states (
    twin_state_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    weekly_id               TEXT NOT NULL
        REFERENCES weekly_flux_contracts(weekly_id),
    snapshot_day            INTEGER NOT NULL,       -- jour horizon (0..H)
    snapshot_date           TEXT NOT NULL,          -- ISO YYYY-MM-DD
    -- Flux 1 — Physique
    physical_wip_actual         REAL DEFAULT 0,
    physical_ofs_running        INTEGER DEFAULT 0,
    physical_ofs_closed         INTEGER DEFAULT 0,
    physical_units_delivered    REAL DEFAULT 0,
    -- Flux 2 — Informationnel (event sourcing)
    info_deviations_detected    INTEGER DEFAULT 0,
    info_actions_triggered      INTEGER DEFAULT 0,
    info_causes_attached        INTEGER DEFAULT 0,
    -- Flux 3 — Décisionnel
    decision_correct_local      INTEGER DEFAULT 0,
    decision_replan_local       INTEGER DEFAULT 0,
    decision_replan_global      INTEGER DEFAULT 0,
    decision_escalate_human     INTEGER DEFAULT 0,
    -- Flux 4 — Documentaire
    doc_contracts_draft         INTEGER DEFAULT 0,
    doc_contracts_signed        INTEGER DEFAULT 0,
    doc_contracts_closed        INTEGER DEFAULT 0,
    -- Flux 5 — Qualité
    quality_scrap_cumul         REAL DEFAULT 0,
    quality_nc_count            INTEGER DEFAULT 0,
    quality_yield_rate          REAL,               -- ratio qty_good/total
    -- Métadata
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (weekly_id, snapshot_day)
);

CREATE INDEX IF NOT EXISTS idx_flux_twin_weekly
    ON flux_twin_states (weekly_id);
CREATE INDEX IF NOT EXISTS idx_flux_twin_day
    ON flux_twin_states (snapshot_day);

-- ---------------------------------------------------------------------
-- V13.E — Dossier de faisabilité TOC persisté par candidate
-- ---------------------------------------------------------------------
-- Table renseignée par compute_smoothing() quand smoothing_toc_aware=1.
-- Lue par wire_zone_negociable_after_promotion() pour enrichir les
-- demand_contracts avec les cibles doctrinales.
CREATE TABLE IF NOT EXISTS flux_candidate_feasibility (
    candidate_id            TEXT PRIMARY KEY
        REFERENCES candidate_orders(candidate_id),
    bottleneck_ws           TEXT,
    goulot_load_min         REAL,
    goulot_slot_day         INTEGER,
    launch_day              INTEGER,
    buffer_days             INTEGER,
    charge_total_min        REAL,
    takt_min_per_unit_target REAL,
    wip_predicted           REAL,
    rho_bottleneck_run      REAL,
    feasible                INTEGER NOT NULL DEFAULT 1,
    computed_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
