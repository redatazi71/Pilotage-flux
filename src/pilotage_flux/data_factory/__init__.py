"""Générateur de fixtures et scénarios aléatoires (L10 phase A).

Permet de produire des sets référentiels paramétrables et reproductibles
pour stresser la doctrine sur des configurations industrielles variées
(nombre d'articles, profondeur BOM, multi-goulots, etc.) sans avoir à
écrire chaque CSV à la main.
"""

from pilotage_flux.data_factory.random_fixtures import (
    DEFAULT_SPEC,
    FixtureSpec,
    generate_random_fixtures,
    seed_random_routing_alternatives,
)

__all__ = [
    "DEFAULT_SPEC",
    "FixtureSpec",
    "generate_random_fixtures",
    "seed_random_routing_alternatives",
]
