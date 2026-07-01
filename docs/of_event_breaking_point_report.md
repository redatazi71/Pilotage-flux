# Étude « breaking point » OF+EVENT

Gradient de stress sur 7 niveaux, 5 seeds par niveau, doctrine OF+EVENT uniquement.

## Tableau consolidé QCDS + WIP + rupture + recovery

| Niveau | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j | Diagnostic |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|---|
| faible | 0.927 | 0.927 | 1.000 | 100.97 | 2.82 | 4.56 | 0.040 | 0.0% | 7.8 | TENU |
| moyen | 0.948 | 0.948 | 1.000 | 109.84 | 4.86 | 7.91 | 0.040 | 0.0% | 12.6 | TENU |
| fort | 0.937 | 0.937 | 1.000 | 108.96 | 5.22 | 9.57 | 0.030 | 0.0% | 15.8 | TENU |
| extrême | 0.947 | 0.947 | 1.000 | 116.14 | 7.36 | 15.57 | 0.017 | 0.0% | 20.0 | TENU |
| extrême+ | 0.947 | 0.947 | 1.000 | 116.59 | 8.48 | 18.23 | 0.013 | 0.0% | 20.0 | TENU |
| rupture | 0.918 | 0.940 | 0.976 | 123.09 | 9.61 | 21.35 | 0.011 | 2.4% | 20.0 | TENU |
| rupture++ | 0.924 | 0.945 | 0.978 | 124.13 | 12.27 | 27.64 | 0.008 | 2.2% | 20.0 | TENU |

## Seuils de casse

Le système est considéré cassé si l'un des critères est franchi :

- OTIF < 0.90
- Rupture > 5%
- Recovery > 30 jours

## Progression stress → dégradation

| Niveau | ΔOTIF vs faible | Δrupture vs faible | Δrecovery vs faible |
|---|:-:|:-:|:-:|
| faible | +0.000 | +0.0% | +0.0j |
| moyen | +0.021 | +0.0% | +4.8j |
| fort | +0.009 | +0.0% | +8.0j |
| extrême | +0.020 | +0.0% | +12.2j |
| extrême+ | +0.020 | +0.0% | +12.2j |
| rupture | -0.009 | +2.4% | +12.2j |
| rupture++ | -0.003 | +2.2% | +12.2j |

## Conclusion

- Niveaux TENUS : faible, moyen, fort, extrême, extrême+, rupture, rupture++