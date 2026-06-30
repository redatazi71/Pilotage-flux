"""Goldilocks #3 — dynamicité complète des 3 zones (2 signaux par zone).

Vérifie :
  - DEFAULTS contient les seuils + facteurs + hystérèse attendus
  - _decide_zone : contraction, extension par nervosité faible,
    extension par horizon insuffisant, status quo
  - _measure_horizon_insufficient renvoie un ratio borné [0, 1]
  - compute_zone_adjustments : pas de mouvement sur base vide
  - apply_zone_adjustments : pose une nouvelle version paramètre
  - is_dynamics_enabled : flag global lu correctement
  - Plancher 1 j + plafond 2× appliqués (clamp)
"""

from __future__ import annotations

from pilotage_flux.cybernetic.zone_dynamics import (
    DEFAULTS,
    ZoneAdjustments,
    _decide_zone,
    _measure_horizon_insufficient,
    apply_zone_adjustments,
    compute_zone_adjustments,
    is_dynamics_enabled,
)
from pilotage_flux.cybernetic.zone_periods import (
    DEFAULT_GELEE_DAYS,
    DEFAULT_LIBRE_DAYS,
    DEFAULT_NEGOCIABLE_DAYS,
    ZonePeriods,
    get_zone_periods,
)
from pilotage_flux.db import db_session


def test_defaults_contains_six_thresholds_and_hysteresis() -> None:
    # 6 seuils (3 zones × contract + extend), 1 horizon, 2 facteurs,
    # 3 hystérèse
    for key in (
        "theta_gel_contract", "theta_gel_extend",
        "theta_neg_contract", "theta_neg_extend",
        "theta_libre_contract", "theta_libre_extend",
        "theta_horizon_insufficient",
        "contraction_factor", "extension_factor",
        "n_gel_hysteresis", "n_neg_hysteresis", "n_libre_hysteresis",
    ):
        assert key in DEFAULTS, f"DEFAULTS manque la clé {key}"
    # Facteurs canoniques (cadrage §3.9.3)
    assert DEFAULTS["contraction_factor"] == 0.5
    assert DEFAULTS["extension_factor"] == 1.5
    # Hystérèse cadrage v1.3 §3.9.2 : 4/2/3
    assert DEFAULTS["n_gel_hysteresis"] == 4
    assert DEFAULTS["n_neg_hysteresis"] == 2
    assert DEFAULTS["n_libre_hysteresis"] == 3


def test_decide_zone_contract_when_signal_above_threshold() -> None:
    new, reason = _decide_zone(
        current=14, base_default=14,
        contract_signal=0.40, contract_threshold=0.30,
        extend_signal=0.40, extend_threshold=0.10,
        horizon_insufficient_signal=0.0,
        horizon_threshold=0.85,
        contraction_factor=0.5, extension_factor=1.5,
    )
    assert reason == "contract"
    assert new == 7  # 14 × 0.5


def test_decide_zone_extend_when_signal_below_threshold() -> None:
    new, reason = _decide_zone(
        current=14, base_default=14,
        contract_signal=0.05, contract_threshold=0.30,
        extend_signal=0.05, extend_threshold=0.10,
        horizon_insufficient_signal=0.0,
        horizon_threshold=0.85,
        contraction_factor=0.5, extension_factor=1.5,
    )
    assert reason == "extend"
    assert new == 21  # 14 × 1.5


def test_decide_zone_extend_when_horizon_insufficient() -> None:
    """Précision utilisateur : extension aussi déclenchée par horizon
    insuffisant — pour donner plus de solutions au système."""
    new, reason = _decide_zone(
        current=70, base_default=70,
        contract_signal=0.15, contract_threshold=0.20,
        extend_signal=0.15, extend_threshold=0.08,  # ni contract ni extend
        horizon_insufficient_signal=0.90,            # mais horizon plein
        horizon_threshold=0.85,
        contraction_factor=0.5, extension_factor=1.5,
    )
    assert reason == "extend"
    assert new == 105  # 70 × 1.5


def test_decide_zone_unchanged_when_in_band() -> None:
    new, reason = _decide_zone(
        current=70, base_default=70,
        contract_signal=0.15, contract_threshold=0.20,
        extend_signal=0.15, extend_threshold=0.08,
        horizon_insufficient_signal=0.50,
        horizon_threshold=0.85,
        contraction_factor=0.5, extension_factor=1.5,
    )
    assert reason == "unchanged"
    assert new == 70


def test_decide_zone_contract_takes_precedence_over_horizon() -> None:
    """Si nervosité haute ET horizon insuffisant → contraction prime
    (cadrage : la réactivité est prioritaire sur les degrés de
    liberté)."""
    new, reason = _decide_zone(
        current=14, base_default=14,
        contract_signal=0.50, contract_threshold=0.30,
        extend_signal=0.50, extend_threshold=0.10,
        horizon_insufficient_signal=0.99,
        horizon_threshold=0.85,
        contraction_factor=0.5, extension_factor=1.5,
    )
    assert reason == "contract"
    assert new == 7


def test_decide_zone_clamp_floor() -> None:
    """Plancher 1 j même si contraction agressive."""
    new, _ = _decide_zone(
        current=2, base_default=14,
        contract_signal=0.50, contract_threshold=0.30,
        extend_signal=0.50, extend_threshold=0.10,
        horizon_insufficient_signal=0.0,
        horizon_threshold=0.85,
        contraction_factor=0.1, extension_factor=1.5,
    )
    assert new == 1


def test_decide_zone_clamp_ceiling() -> None:
    """Plafond 2 × base_default même si extension agressive."""
    new, _ = _decide_zone(
        current=14, base_default=14,
        contract_signal=0.05, contract_threshold=0.30,
        extend_signal=0.05, extend_threshold=0.10,
        horizon_insufficient_signal=0.0,
        horizon_threshold=0.85,
        contraction_factor=0.5, extension_factor=5.0,
    )
    assert new == 28  # 14 × 2


def test_measure_horizon_insufficient_empty_db_is_zero(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        for zone in ("gelee", "negociable", "libre"):
            assert _measure_horizon_insufficient(conn, zone) == 0.0


def test_measure_horizon_insufficient_grows_with_load(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('A', 'a')"
        )
        # 5 OFs launched → ratio 0.5 pour zone gelée (capa = 10)
        for i in range(5):
            conn.execute(
                "INSERT INTO manufacturing_orders "
                "(of_id, article_id, quantity, status) "
                "VALUES (?, 'A', 10, 'launched')", (f"OF-{i}",),
            )
        # 10 candidats → ratio 0.5 pour zone négociable (capa = 20)
        for i in range(10):
            conn.execute(
                "INSERT INTO candidate_orders "
                "(candidate_id, article_id, quantity, status, zone) "
                "VALUES (?, 'A', 5, 'candidate', 'libre')",
                (f"C-{i}",),
            )
        # 15 SOs → ratio 0.5 pour zone libre (capa = 30)
        for i in range(15):
            conn.execute(
                "INSERT INTO sales_orders "
                "(sales_order_id, article_id, quantity, due_date) "
                "VALUES (?, 'A', 5, '2026-12-01')", (f"SO-{i}",),
            )
        assert 0.45 < _measure_horizon_insufficient(conn, "gelee") < 0.55
        assert 0.45 < _measure_horizon_insufficient(conn, "negociable") < 0.55
        assert 0.45 < _measure_horizon_insufficient(conn, "libre") < 0.55


def test_compute_zone_adjustments_empty_db_status_quo(tmp_db) -> None:
    """Sans gate_decisions ni candidates ni OFs, aucun signal → aucun
    ajustement."""
    with db_session(tmp_db) as conn:
        adj = compute_zone_adjustments(conn)
        # Nervosité = 0 → < seuil extend → extend pour zone gelée ?
        # Non : extend_threshold = 0.10, nervosité = 0 < 0.10 → étend.
        # Donc zone gelée doit être étendue de 14 → 21.
        assert adj.gelee_reason == "extend"
        assert adj.gelee_new == 21


def test_compute_zone_adjustments_contracts_under_nervosity(tmp_db) -> None:
    """Nervosité forte (> theta_gel_contract) → zone gelée contracte."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO run_metadata (key, value) "
            "VALUES ('horizon_start', '2026-06-29')"
        )
        # 50 REPLAN sur 1 jour → nervosité ≈ 50, largement > 0.30
        for i in range(50):
            conn.execute(
                "INSERT INTO gate_decisions "
                "(gate, subject_type, subject_id, decision) "
                "VALUES ('P3', 'of', ?, 'REPLAN')", (f"OF-{i}",),
            )
        adj = compute_zone_adjustments(conn)
        assert adj.gelee_reason == "contract"
        assert adj.gelee_new == 7  # 14 × 0.5


def test_compute_zone_adjustments_with_custom_current(tmp_db) -> None:
    """Le caller peut passer un ZonePeriods custom (pas lu en base)."""
    with db_session(tmp_db) as conn:
        custom = ZonePeriods(gelee_days=21, negociable_days=100, libre_days=300)
        adj = compute_zone_adjustments(conn, current=custom)
        # Avec base vide, nervosité = 0 → extend ; on étend depuis 21
        # vers min(2×14, 21×1.5) = min(28, 32) = 28
        assert adj.gelee_reason == "extend"
        assert adj.gelee_new == 28  # plafond 2 × 14


def test_apply_zone_adjustments_writes_new_version(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        # Pose version initiale
        conn.execute(
            "INSERT INTO parameters "
            "(scope, scope_ref, name, value_num, version) "
            "VALUES ('global', NULL, 'zone_gelee_period_days', 14.0, 1)"
        )
        adj = ZoneAdjustments(
            gelee_new=7, gelee_reason="contract",
            negociable_new=70, negociable_reason="unchanged",
            libre_new=270, libre_reason="unchanged",
        )
        n_changed = apply_zone_adjustments(conn, adj)
        assert n_changed == 1
        # Version initiale doit être close
        rows = conn.execute(
            "SELECT version, value_num, valid_to FROM parameters "
            "WHERE name='zone_gelee_period_days' ORDER BY version"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["version"] == 1
        assert rows[0]["valid_to"] is not None
        assert rows[1]["version"] == 2
        assert rows[1]["value_num"] == 7.0
        assert rows[1]["valid_to"] is None
        # get_zone_periods doit relire la nouvelle valeur
        p = get_zone_periods(conn)
        assert p.gelee_days == 7


def test_apply_zone_adjustments_skips_unchanged(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        adj = ZoneAdjustments(
            gelee_new=14, gelee_reason="unchanged",
            negociable_new=70, negociable_reason="unchanged",
            libre_new=270, libre_reason="unchanged",
        )
        assert apply_zone_adjustments(conn, adj) == 0


def test_any_change_property() -> None:
    static = ZoneAdjustments(
        gelee_new=14, gelee_reason="unchanged",
        negociable_new=70, negociable_reason="unchanged",
        libre_new=270, libre_reason="unchanged",
    )
    moving = ZoneAdjustments(
        gelee_new=14, gelee_reason="unchanged",
        negociable_new=105, negociable_reason="extend",
        libre_new=270, libre_reason="unchanged",
    )
    assert static.any_change is False
    assert moving.any_change is True


def test_is_dynamics_enabled_default_false(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert is_dynamics_enabled(conn) is False


def test_is_dynamics_enabled_true_when_param_set(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'zone_dynamics_enabled', 1.0)"
        )
        assert is_dynamics_enabled(conn) is True


def test_default_zone_periods_align_with_dynamics_bases() -> None:
    """Les bases de clamp dans dynamics doivent être alignées avec les
    valeurs canoniques de zone_periods."""
    assert DEFAULT_GELEE_DAYS == 14
    assert DEFAULT_NEGOCIABLE_DAYS == 70
    assert DEFAULT_LIBRE_DAYS == 270
