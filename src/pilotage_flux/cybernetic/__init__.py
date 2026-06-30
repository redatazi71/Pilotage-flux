"""Architecture cybernétique étendue V12 — extension doctrinale.

V12 ajoute par-dessus la doctrine V0-V11 une couche cybernétique
de contrôle avec validation humaine. Elle introduit :

  - V12.1 (à venir) : forecasting zone libre (linéaire + non linéaire)
  - V12.2 (à venir) : optimization zone négociable (CP-SAT dynamique)
  - V12.3 (LIVRÉ)   : Delta engine 4 niveaux d'autonomie
  - V12.4 (à venir) : human loop (approval workflow + audit)
  - V12.5 (à venir) : matrice d'orchestration

V12.3 — Delta engine 4 niveaux :

  L1 — autonome sans ajustement (écart absorbé par tampon)
  L2 — ajustement sans humain (V3 actionnel, correct_local)
  L3 — replanification locale + validation humaine
  L4 — replanification totale + validation humaine

Cette couche n'invalide pas la doctrine V0-V11 — elle l'augmente
d'une dimension d'autonomie graduée et auditable.
"""

__version__ = "0.1.0"
