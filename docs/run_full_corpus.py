"""Re-run de TOUT le corpus historique d'un seul bloc — Windows-compatible.

Orchestrateur unique qui ré-exécute toutes les études comparatives, les
figures, (optionnellement) les diagnostics, puis régénère les documents
(DOCX). Conçu pour tourner sur un PC Windows en une seule commande :

    python docs/run_full_corpus.py

Options :
    --skip-diagnostics   N'exécute pas les scripts diagnose_*.py
    --only <motif>       N'exécute que les scripts dont le nom contient <motif>
    --skip <motif>       Saute les scripts dont le nom contient <motif>
    --list               Affiche le plan d'exécution sans rien lancer
    --continue-on-error  (défaut) continue même si une étude échoue
    --stop-on-error      S'arrête à la première erreur

Robustesse Windows :
  - utilise sys.executable (pas "python"/"python3")
  - force MPLBACKEND=Agg (pas de fenêtre matplotlib requise)
  - force PYTHONUTF8=1 + PYTHONIOENCODING=utf-8 (accents français)
  - cwd = racine du dépôt (les scripts lisent data/fixtures_extended en
    chemin relatif)
  - chaque script est isolé dans un sous-processus ; un échec n'interrompt
    pas le bloc (sauf --stop-on-error)

Sortie : un tableau récapitulatif (étude → statut → durée) + un code de
sortie non nul si au moins une étude a échoué.

ATTENTION : le corpus complet représente plusieurs milliers de runs de
simulation. Comptez de quelques minutes à plusieurs dizaines de minutes
selon la machine. Les diagnostics ajoutent du temps ; --skip-diagnostics
pour la version courte.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


# Racine du dépôt = parent du dossier docs/
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent


# Plan d'exécution ordonné. Chaque entrée : (phase, nom_fichier).
# Les études lourdes d'abord, les figures ensuite, les diagnostics
# (optionnels), puis les documents DOCX en DERNIER (ils consomment les
# .md régénérés par les études).
PLAN: list[tuple[str, str]] = [
    # --- Phase 1 : études comparatives & validité (lourdes) ---
    ("etudes", "build_validity_studies.py"),
    ("etudes", "build_resilience_analysis.py"),
    ("etudes", "build_resilience_extension.py"),
    ("etudes", "build_qcds_study.py"),
    ("etudes", "build_otif_first_study.py"),
    ("etudes", "build_point2_real_disponibility.py"),
    ("etudes", "build_point3_direct_sensitivity.py"),
    ("etudes", "build_v12_6_comparative.py"),
    ("etudes", "build_v12_7_comparative.py"),
    ("etudes", "build_v12_8_comparative.py"),
    ("etudes", "build_qcds_matrix_5_doctrines.py"),
    ("etudes", "build_qcds_realistic_capacity.py"),
    ("etudes", "build_qcds_v13_1.py"),
    # --- Phase 2 : graphiques & exports ---
    ("figures", "build_charts.py"),
    ("figures", "build_excel_kpis.py"),
    ("figures", "build_paper_figures.py"),
    ("figures", "build_paper_fig4_forecasting.py"),
    ("figures", "build_paper_fig5_zones.py"),
    ("figures", "build_paper_fig6_v12_complete.py"),
    # --- Phase 3 : diagnostics (optionnels) ---
    ("diagnostics", "diagnose_v12_7.py"),
    ("diagnostics", "diagnose_v12_7_fix.py"),
    ("diagnostics", "diagnose_v12_8.py"),
    ("diagnostics", "diagnose_event_vs_flux.py"),
    ("diagnostics", "diagnose_v13_0.py"),
    ("diagnostics", "diagnose_v13_0_matrix.py"),
    ("diagnostics", "diagnose_v13_1.py"),
    ("diagnostics", "diagnose_v13_a.py"),
    ("diagnostics", "diagnose_flux_vs_of_event_full_cyber.py"),
    # --- Phase 4 : documents (DOCX) — TOUJOURS en dernier ---
    ("documents", "build_cadrage_v4_docx.py"),
    ("documents", "build_paper_hal_docx.py"),
]


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
    env["MPLBACKEND"] = "Agg"          # pas de display requis
    env["PYTHONUTF8"] = "1"            # Windows : force UTF-8
    env["PYTHONIOENCODING"] = "utf-8"  # accents français en sortie
    # Injecte src/ dans PYTHONPATH pour que `import pilotage_flux`
    # fonctionne même SANS `pip install -e .` (layout src/).
    src_dir = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        src_dir + (os.pathsep + existing if existing else "")
    )
    return env


def _preflight_dependencies() -> list[str]:
    """Vérifie les dépendances tierces des études. Renvoie la liste des
    paquets manquants (vide si tout est présent).

    `pilotage_flux` n'est pas testé ici : il est résolu via PYTHONPATH=src
    dans les sous-processus (cf. _build_env).
    """
    missing: list[str] = []
    for pkg in ("matplotlib", "numpy", "openpyxl"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def _select(args) -> list[tuple[str, str]]:
    plan = list(PLAN)
    if args.skip_diagnostics:
        plan = [(ph, s) for ph, s in plan if ph != "diagnostics"]
    if args.only:
        plan = [(ph, s) for ph, s in plan if args.only in s]
    if args.skip:
        plan = [(ph, s) for ph, s in plan if args.skip not in s]
    return plan


def _run_one(script: str, env: dict[str, str]) -> tuple[str, float, str]:
    """Exécute un script. Renvoie (status, durée_s, message_court)."""
    path = HERE / script
    if not path.exists():
        return ("ABSENT", 0.0, "fichier introuvable")
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:  # pragma: no cover - garde-fou
        return ("ERREUR", time.time() - t0, f"exception lancement : {exc}")
    dur = time.time() - t0
    if proc.returncode == 0:
        # Dernière ligne non vide de stdout comme indicateur
        tail = ""
        for line in reversed((proc.stdout or "").splitlines()):
            if line.strip():
                tail = line.strip()[:80]
                break
        return ("OK", dur, tail)
    # Échec : on remonte la dernière ligne de stderr
    err_tail = ""
    for line in reversed((proc.stderr or "").splitlines()):
        if line.strip():
            err_tail = line.strip()[:120]
            break
    return ("ÉCHEC", dur, err_tail)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-run du corpus historique complet (un bloc).",
    )
    parser.add_argument("--skip-diagnostics", action="store_true")
    parser.add_argument("--only", default=None,
                        help="n'exécuter que les scripts contenant ce motif")
    parser.add_argument("--skip", default=None,
                        help="sauter les scripts contenant ce motif")
    parser.add_argument("--list", action="store_true",
                        help="afficher le plan sans rien exécuter")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()

    plan = _select(args)

    if args.list:
        print(f"Plan d'exécution ({len(plan)} scripts) :\n")
        cur = None
        for ph, s in plan:
            if ph != cur:
                print(f"\n[{ph}]")
                cur = ph
            print(f"  - {s}")
        return 0

    env = _build_env()
    print("=" * 78)
    print(f"Re-run corpus complet — {len(plan)} scripts")
    print(f"Python   : {sys.executable}")
    print(f"Racine   : {REPO_ROOT}")
    print(f"Backend  : Agg (matplotlib headless)")
    print("=" * 78)

    # Pré-check des dépendances tierces (matplotlib/numpy/openpyxl)
    missing = _preflight_dependencies()
    if missing:
        print("\n⚠  Dépendances manquantes dans cet interpréteur Python :")
        print(f"     {', '.join(missing)}")
        print("   Les études correspondantes échoueront. Pour les installer :")
        print(f'     "{sys.executable}" -m pip install {" ".join(missing)} ortools')
        print("   (ortools n'est requis que pour la doctrine OF_MILP.)")
        print("   On poursuit quand même — les scripts sans ces deps tourneront.\n")

    results: list[tuple[str, str, str, float, str]] = []
    t_start = time.time()
    cur_phase = None
    for ph, script in plan:
        if ph != cur_phase:
            print(f"\n──── Phase : {ph} ────")
            cur_phase = ph
        print(f"  ▶ {script:42} ", end="", flush=True)
        status, dur, msg = _run_one(script, env)
        symbol = {"OK": "✓", "ÉCHEC": "✗", "ABSENT": "?",
                  "ERREUR": "✗"}.get(status, "?")
        print(f"{symbol} {status:7} {dur:7.1f}s  {msg}")
        results.append((ph, script, status, dur, msg))
        if status in ("ÉCHEC", "ERREUR") and args.stop_on_error:
            print("\n⏹  Arrêt sur erreur (--stop-on-error).")
            break

    total = time.time() - t_start

    # Récapitulatif
    print("\n" + "=" * 78)
    print("RÉCAPITULATIF")
    print("=" * 78)
    n_ok = sum(1 for r in results if r[2] == "OK")
    n_fail = sum(1 for r in results if r[2] in ("ÉCHEC", "ERREUR"))
    n_abs = sum(1 for r in results if r[2] == "ABSENT")
    for ph, script, status, dur, msg in results:
        if status != "OK":
            print(f"  {status:7} {script:42} {dur:6.1f}s  {msg}")
    print(f"\n  Total : {len(results)} scripts | "
          f"{n_ok} OK | {n_fail} échecs | {n_abs} absents")
    print(f"  Durée totale : {total/60:.1f} min ({total:.0f} s)")
    print("=" * 78)

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
