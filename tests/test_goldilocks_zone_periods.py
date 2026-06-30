"""Goldilocks #2 — périodes territoriales par défaut 2/10/9."""

from __future__ import annotations

from pilotage_flux.cybernetic.zone_periods import (
    DEFAULT_PERIODS,
    DEFAULT_GELEE_DAYS,
    DEFAULT_LIBRE_DAYS,
    DEFAULT_NEGOCIABLE_DAYS,
    ZonePeriods,
    get_zone_periods,
    seed_default_zone_periods,
)
from pilotage_flux.db import db_session


def test_default_canonical_values() -> None:
    """Cadrage v1.3 §3.9.1 : 2 sem / 10 sem / 9 mois en jours."""
    assert DEFAULT_GELEE_DAYS == 14
    assert DEFAULT_NEGOCIABLE_DAYS == 70
    assert DEFAULT_LIBRE_DAYS == 270
    assert DEFAULT_PERIODS.total_days == 354  # ~12 mois alignement annuel
    assert DEFAULT_PERIODS.freeze_end_day == 14
    assert DEFAULT_PERIODS.negociable_end_day == 84  # 14 + 70


def test_get_periods_returns_defaults_when_db_empty(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        p = get_zone_periods(conn)
        assert p == DEFAULT_PERIODS


def test_get_periods_reads_parameters(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        for name, val in (
            ("zone_gelee_period_days", 7),
            ("zone_negociable_period_days", 35),
            ("zone_libre_period_days", 180),
        ):
            conn.execute(
                "INSERT INTO parameters (scope, scope_ref, name, value_num) "
                "VALUES ('global', NULL, ?, ?)",
                (name, float(val)),
            )
        p = get_zone_periods(conn)
        assert p.gelee_days == 7
        assert p.negociable_days == 35
        assert p.libre_days == 180


def test_seed_inserts_3_defaults(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        n = seed_default_zone_periods(conn)
        assert n == 3
        rows = conn.execute(
            "SELECT name, value_num FROM parameters WHERE scope='global' "
            "AND name LIKE 'zone_%_period_days' ORDER BY name"
        ).fetchall()
        d = {r["name"]: int(r["value_num"]) for r in rows}
        assert d == {
            "zone_gelee_period_days": 14,
            "zone_negociable_period_days": 70,
            "zone_libre_period_days": 270,
        }


def test_seed_is_idempotent(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        first = seed_default_zone_periods(conn)
        second = seed_default_zone_periods(conn)
        assert first == 3
        assert second == 0


def test_seed_does_not_overwrite_existing(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'zone_gelee_period_days', 5)"
        )
        seed_default_zone_periods(conn)
        p = get_zone_periods(conn)
        assert p.gelee_days == 5  # inchangé
        assert p.negociable_days == 70  # seedé
        assert p.libre_days == 270


def test_resolve_negotiable_zone_uses_default_periods(tmp_db) -> None:
    """Sans freeze_window_days/horizon_forecast_days passés, le resolver
    lit get_zone_periods() au lieu des anciens defaults 5/28."""
    from pilotage_flux.cybernetic.optimization.zone_resolver import (
        resolve_negotiable_zone,
    )
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO run_metadata (key, value) "
            "VALUES ('horizon_start', '2026-07-01')"
        )
        z = resolve_negotiable_zone(conn, reference_day=0)
        assert z.freeze_window_days == 14  # gelée doctrine
        assert z.horizon_forecast_days == 84  # gelée + négociable
