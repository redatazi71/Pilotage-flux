"""Ext-o — Pilotage fédéré multi-site.

Modélise la coordination de plusieurs sites industriels qui échangent
des flux amont/aval **sans centralisation**. Chaque site pilote
localement sa production ; les événements de synchronisation (livraison
de composant, capacité disponible, urgence remontée) circulent entre
sites via un **event bus fédéré**.

Le paper §5 démontre que le gain FLUX+EVENT est **amplifié** en
multi-site parce que le contrat de flux devient l'interface d'échange
inter-site (typée, versionnée, contestable), là où une architecture OF
classique doit centraliser via l'ERP mère.

Architecture minimaliste :

    Site A (autonome)  ──── FederatedEvent ────▶  Site B (autonome)
       │                        (bus)                    │
       │                                                 │
       ▼                                                 ▼
    Local KPIs                                        Local KPIs
       └─────────────── Cross-site KPIs ───────────────┘

Chaque site conserve sa propre DB, son propre runner. Le bus est un
médiateur en mémoire qui **ordonne** et **route** les événements ;
la latence de propagation `latency_days` simule le temps entre
émission (site source) et effet (site destinataire).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


# Types d'événements inter-site
FED_SHIP_COMPONENT = "ship_component"
"""Site A a expédié un composant vers Site B (arrivée après latency_days)."""
FED_CAPACITY_OFFER = "capacity_offer"
"""Site A offre de la capacité disponible à Site B (débordement)."""
FED_URGENT_ESCALATION = "urgent_escalation"
"""Site A remonte une urgence à un site donneur d'ordre B."""
FED_SUPPLY_SHORTAGE = "supply_shortage"
"""Site A signale une rupture composant qui affectera Site B."""

FED_EVENT_TYPES = (
    FED_SHIP_COMPONENT, FED_CAPACITY_OFFER,
    FED_URGENT_ESCALATION, FED_SUPPLY_SHORTAGE,
)


@dataclass
class FederatedEvent:
    """Un événement inter-site.

    - `origin_site` : identifiant du site émetteur.
    - `target_site` : site destinataire ; `None` = broadcast à tous
      les autres sites du bus.
    - `emitted_day` : jour d'émission côté source.
    - `received_day` : jour d'arrivée côté cible (emitted + latency).
    - `kind` : un de FED_EVENT_TYPES.
    - `payload` : contenu libre (référence article, quantité, etc.).
    """
    origin_site: str
    target_site: str | None
    emitted_day: int
    received_day: int
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class CrossSiteKpis:
    """KPIs cross-site agrégés."""
    n_events_emitted: int = 0
    n_events_received: int = 0
    n_supply_shortages: int = 0
    n_capacity_offers: int = 0
    n_urgent_escalations: int = 0
    mean_latency_days: float = 0.0
    max_latency_days: int = 0
    events_by_pair: dict[str, int] = field(default_factory=dict)


class FederatedEventBus:
    """Bus fédéré : ordonne les événements par `received_day` et
    permet à chaque site de consommer ce qui lui est adressé au jour J.

    Le bus **ne prend aucune décision doctrinale** — c'est un canal
    d'échange typé. La sémantique de chaque event est portée par le
    runner qui le consomme (event sourcing local).
    """

    def __init__(self, default_latency_days: int = 1) -> None:
        self.default_latency = default_latency_days
        self._queue: deque[FederatedEvent] = deque()
        self._delivered: list[FederatedEvent] = []
        self._sites: set[str] = set()

    def register_site(self, site_id: str) -> None:
        self._sites.add(site_id)

    def publish(
        self,
        origin_site: str,
        kind: str,
        emitted_day: int,
        payload: dict[str, Any] | None = None,
        target_site: str | None = None,
        latency_days: int | None = None,
    ) -> FederatedEvent:
        """Émet un événement inter-site.

        - `target_site=None` = broadcast (le bus dupliquera pour chaque
          site autre que l'origine).
        """
        latency = latency_days if latency_days is not None else self.default_latency
        event = FederatedEvent(
            origin_site=origin_site,
            target_site=target_site,
            emitted_day=emitted_day,
            received_day=emitted_day + latency,
            kind=kind,
            payload=payload or {},
        )
        if target_site is None:
            for site in self._sites:
                if site == origin_site:
                    continue
                self._queue.append(
                    FederatedEvent(
                        origin_site=origin_site,
                        target_site=site,
                        emitted_day=emitted_day,
                        received_day=emitted_day + latency,
                        kind=kind,
                        payload=dict(payload or {}),
                    )
                )
        else:
            self._queue.append(event)
        return event

    def poll(self, site_id: str, day: int) -> list[FederatedEvent]:
        """Récupère les événements adressés à `site_id` reçus <= `day`."""
        ready: list[FederatedEvent] = []
        remaining: deque[FederatedEvent] = deque()
        for ev in self._queue:
            if ev.target_site == site_id and ev.received_day <= day:
                ready.append(ev)
                self._delivered.append(ev)
            else:
                remaining.append(ev)
        self._queue = remaining
        return ready

    def compute_cross_site_kpis(self) -> CrossSiteKpis:
        kpis = CrossSiteKpis()
        latencies: list[int] = []
        for ev in self._delivered:
            kpis.n_events_received += 1
            latencies.append(ev.received_day - ev.emitted_day)
            if ev.kind == FED_SUPPLY_SHORTAGE:
                kpis.n_supply_shortages += 1
            elif ev.kind == FED_CAPACITY_OFFER:
                kpis.n_capacity_offers += 1
            elif ev.kind == FED_URGENT_ESCALATION:
                kpis.n_urgent_escalations += 1
            pair = f"{ev.origin_site}->{ev.target_site}"
            kpis.events_by_pair[pair] = (
                kpis.events_by_pair.get(pair, 0) + 1
            )
        # Emitted = delivered + queue résiduelle
        kpis.n_events_emitted = kpis.n_events_received + len(self._queue)
        if latencies:
            kpis.mean_latency_days = round(sum(latencies) / len(latencies), 2)
            kpis.max_latency_days = max(latencies)
        return kpis


def orchestrate_federated_runs(
    sites: dict[str, Any],
    horizon_days: int,
    bus: FederatedEventBus | None = None,
) -> tuple[dict[str, Any], CrossSiteKpis]:
    """Orchestrateur simple : chaque jour, chaque site avance d'un pas
    et échange ses événements via le bus. Utilisé par les scripts
    docs/build_federated_study.py.

    `sites` : dict `{site_id: SiteRunHandle}` où `SiteRunHandle` a une
    méthode `advance_day(day, incoming_events, bus)` et retourne son
    RunResult / KpiSet à la fin.

    Ce module fournit l'infra ; l'intégration réelle est laissée aux
    scripts d'étude qui peuvent brancher runners doctrinaux existants
    sur `advance_day`.
    """
    if bus is None:
        bus = FederatedEventBus()
    for site_id in sites:
        bus.register_site(site_id)
    for day in range(horizon_days + 1):
        for site_id, handle in sites.items():
            incoming = bus.poll(site_id, day)
            if hasattr(handle, "advance_day"):
                handle.advance_day(day=day, incoming_events=incoming, bus=bus)
    return sites, bus.compute_cross_site_kpis()
