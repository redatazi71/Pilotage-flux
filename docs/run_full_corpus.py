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
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# Sous-ensemble « rapide » : ce qu'il faut MINIMUM pour boucler l'item 2
# (réécrire §28.16/§28.17 avec les vrais €/unité). Omet les études XXL
# (resilience ~800 runs, validity ~600). Activer avec --fast.
FAST_SUBSET: set[str] = {
    "build_qcds_matrix_5_doctrines.py",
    "build_qcds_realistic_capacity.py",
    "build_qcds_v13_1.py",
    "build_v12_6_comparative.py",
    "build_v12_7_comparative.py",
    "build_v12_8_comparative.py",
    "diagnose_flux_vs_of_event_full_cyber.py",
    "diagnose_v13_0_matrix.py",
    "build_cadrage_v4_docx.py",
    "build_paper_hal_docx.py",
}


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
    if args.fast:
        plan = [(ph, s) for ph, s in plan if s in FAST_SUBSET]
    if args.skip_diagnostics:
        plan = [(ph, s) for ph, s in plan if ph != "diagnostics"]
    if args.only:
        plan = [(ph, s) for ph, s in plan if args.only in s]
    if args.skip:
        plan = [(ph, s) for ph, s in plan if args.skip not in s]
    return plan


def _run_one(
    script: str, env: dict[str, str], *, stream: bool,
) -> tuple[str, float, str]:
    """Exécute un script. Renvoie (status, durée_s, message_court).

    stream=True  : la sortie de l'étude est diffusée EN DIRECT dans la
                   console (visuel de progression). stderr est capturé
                   pour le diagnostic d'erreur.
    stream=False : sortie capturée, une ligne de résumé (mode --quiet).
    """
    path = HERE / script
    if not path.exists():
        return ("ABSENT", 0.0, "fichier introuvable")
    t0 = time.time()
    try:
        if stream:
            # stdout hérité (live), stderr capturé pour l'erreur
            proc = subprocess.run(
                [sys.executable, "-u", str(path)],
                cwd=str(REPO_ROOT), env=env,
                stdout=None, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            stdout_txt = ""
            stderr_txt = proc.stderr or ""
        else:
            proc = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(REPO_ROOT), env=env,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            )
            stdout_txt = proc.stdout or ""
            stderr_txt = proc.stderr or ""
    except Exception as exc:  # pragma: no cover - garde-fou
        return ("ERREUR", time.time() - t0, f"exception lancement : {exc}")
    dur = time.time() - t0
    if proc.returncode == 0:
        tail = ""
        for line in reversed(stdout_txt.splitlines()):
            if line.strip():
                tail = line.strip()[:80]
                break
        return ("OK", dur, tail)
    err_tail = ""
    for line in reversed(stderr_txt.splitlines()):
        if line.strip():
            err_tail = line.strip()[:120]
            break
    return ("ÉCHEC", dur, err_tail)


def _fmt_eta(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f} min"


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
    parser.add_argument("--quiet", action="store_true",
                        help="ne pas streamer la sortie des études "
                             "(une ligne de résumé par script)")
    parser.add_argument("--jobs", "-j", type=int, default=1,
                        help="parallélise N scripts simultanément "
                             "(défaut 1 = séquentiel). Force --quiet si > 1. "
                             "Les phases restent séquentielles entre elles, "
                             "seuls les scripts d'une même phase sont parallèles.")
    parser.add_argument("--fast", action="store_true",
                        help="sous-ensemble léger (10 scripts) suffisant pour "
                             "régénérer les tables QCDS + DOCX. Omet les études "
                             "XXL (resilience, validity).")
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

    jobs = max(1, args.jobs)
    # Parallèle ⇒ on force quiet (le streaming entremêlé est illisible)
    stream = (not args.quiet) and jobs == 1
    total_n = len(plan)
    mode_lbl = "streaming live" if stream else (
        f"parallèle x{jobs}" if jobs > 1 else "quiet (résumé)"
    )
    print(f"\nMode  : {mode_lbl}  — {total_n} scripts à exécuter")
    if args.fast:
        print("        --fast : sous-ensemble léger (item 2 du plan)")

    # Regroupe par phase (préservant l'ordre) ; chaque phase est lancée
    # avec son propre pool ; on attend la fin de la phase avant la suivante.
    by_phase: list[tuple[str, list[str]]] = []
    for ph, script in plan:
        if by_phase and by_phase[-1][0] == ph:
            by_phase[-1][1].append(script)
        else:
            by_phase.append((ph, [script]))

    results: list[tuple[str, str, str, float, str]] = []
    t_start = time.time()
    completed = 0
    stop_flag = False

    for ph, scripts in by_phase:
        if stop_flag:
            break
        print(f"\n{'━' * 78}\n  PHASE : {ph}  ({len(scripts)} scripts)\n"
              f"{'━' * 78}")
        if jobs > 1:
            # Exécution parallèle au sein d'une phase
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futs = {pool.submit(_run_one, s, env, stream=False): s
                        for s in scripts}
                for fut in as_completed(futs):
                    script = futs[fut]
                    status, dur, msg = fut.result()
                    completed += 1
                    elapsed = _fmt_eta(time.time() - t_start)
                    symbol = {"OK": "✓", "ÉCHEC": "✗", "ABSENT": "?",
                              "ERREUR": "✗"}.get(status, "?")
                    print(f"  [{completed}/{total_n}] {symbol} {status:7} "
                          f"{dur:6.1f}s  {script:42} (écoulé {elapsed})")
                    if status != "OK":
                        print(f"      ⚠ {msg}")
                    results.append((ph, script, status, dur, msg))
                    if status in ("ÉCHEC", "ERREUR") and args.stop_on_error:
                        stop_flag = True
        else:
            for script in scripts:
                completed += 1
                done = [r[3] for r in results if r[2] == "OK"]
                eta_txt = ""
                if done:
                    avg = sum(done) / len(done)
                    eta_txt = (f" | ETA ~"
                               f"{_fmt_eta(avg * (total_n - completed + 1))}")
                elapsed_txt = _fmt_eta(time.time() - t_start)
                header = (f"\n[{completed}/{total_n}] ▶ {script}  "
                          f"(écoulé {elapsed_txt}{eta_txt})")
                if stream:
                    print(header)
                    print(f"  {'·' * 40} sortie live {'·' * 21}")
                else:
                    print(f"{header:70} ", end="", flush=True)
                status, dur, msg = _run_one(script, env, stream=stream)
                symbol = {"OK": "✓", "ÉCHEC": "✗", "ABSENT": "?",
                          "ERREUR": "✗"}.get(status, "?")
                if stream:
                    print(f"  └─ {symbol} {status} en {dur:.1f}s"
                          + (f"  ⚠ {msg}" if status != "OK" else ""))
                else:
                    print(f"{symbol} {status:7} {dur:7.1f}s  {msg}")
                results.append((ph, script, status, dur, msg))
                if status in ("ÉCHEC", "ERREUR") and args.stop_on_error:
                    print("\n⏹  Arrêt sur erreur (--stop-on-error).")
                    stop_flag = True
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
