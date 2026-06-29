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
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

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
-- Metadata du run de simulation
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS run_metadata (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
