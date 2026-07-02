@echo off
REM ============================================================
REM  run_all_studies.bat — enchaîne les 3 études étendues
REM  Sortie : 3 CSVs dans docs\ + logs horodatés
REM  Durée estimée : ~2h30 sur i7-8750H (8 workers, 32 Go RAM)
REM ============================================================

setlocal
set SCRIPT_DIR=%~dp0
set REPO_ROOT=%SCRIPT_DIR%..
cd /d "%REPO_ROOT%"

REM Timestamp lisible pour les logs
for /f "tokens=1-4 delims=/ " %%a in ('date /t') do set DATE_STR=%%c-%%b-%%a
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set TIME_STR=%%a-%%b
set STAMP=%DATE_STR%_%TIME_STR%

set LOG_DIR=docs\logs
if not exist %LOG_DIR% mkdir %LOG_DIR%

echo.
echo ================================================================
echo   Master v2 etendu - 3 etudes sequentielles
echo   Debut : %DATE% %TIME%
echo ================================================================
echo.

REM ============================================================
REM  ETUDE 1 - Base + CP-SAT reactif (Ext-l)
REM  4 configs x 6 stress x 4 chocs x 24 seeds = 2304 runs
REM  Duree ~1h30
REM ============================================================
echo [1/3] Etude BASE + CP-SAT reactif...
python docs\build_master_v2_extended.py ^
    --seeds 24 ^
    --workers 8 ^
    --include-reactive-cpsat ^
    --out docs\master_v2_ext_base.csv ^
    > %LOG_DIR%\etude1_base_cpsat_%STAMP%.log 2>&1
if errorlevel 1 (
    echo [ERROR] Etude 1 en echec - voir log
    goto :end
)
echo [ok] Etude 1 terminee

REM ============================================================
REM  ETUDE 2 - Cascade correlee "tempete" (Ext-g)
REM  3 configs x 6 stress x 4 chocs x 24 seeds = 1728 runs
REM  Duree ~50 min
REM ============================================================
echo.
echo [2/3] Etude CASCADE tempete...
python docs\build_master_v2_extended.py ^
    --seeds 24 ^
    --workers 8 ^
    --cascade tempete ^
    --out docs\master_v2_ext_cascade.csv ^
    > %LOG_DIR%\etude2_cascade_%STAMP%.log 2>&1
if errorlevel 1 (
    echo [ERROR] Etude 2 en echec - voir log
    goto :end
)
echo [ok] Etude 2 terminee

REM ============================================================
REM  ETUDE 3 - Facteur humain (Ext-h)
REM  3 configs x 6 stress x 4 chocs x 24 seeds = 1728 runs
REM  Duree ~40 min
REM ============================================================
echo.
echo [3/3] Etude BOUNDED RATIONALITY...
python docs\build_master_v2_extended.py ^
    --seeds 24 ^
    --workers 8 ^
    --bounded-rationality ^
    --out docs\master_v2_ext_human.csv ^
    > %LOG_DIR%\etude3_human_%STAMP%.log 2>&1
if errorlevel 1 (
    echo [ERROR] Etude 3 en echec - voir log
    goto :end
)
echo [ok] Etude 3 terminee

REM ============================================================
REM  ANALYSE STATISTIQUE POST-HOC sur les 3 CSV
REM ============================================================
echo.
echo Analyse statistique post-hoc...
python docs\statistical_analysis.py ^
    --csv docs\master_v2_ext_base.csv ^
    --out-md docs\stat_ext_base.md ^
    --out-json docs\stat_ext_base.json ^
    >> %LOG_DIR%\stat_%STAMP%.log 2>&1
python docs\statistical_analysis.py ^
    --csv docs\master_v2_ext_cascade.csv ^
    --out-md docs\stat_ext_cascade.md ^
    --out-json docs\stat_ext_cascade.json ^
    >> %LOG_DIR%\stat_%STAMP%.log 2>&1
python docs\statistical_analysis.py ^
    --csv docs\master_v2_ext_human.csv ^
    --out-md docs\stat_ext_human.md ^
    --out-json docs\stat_ext_human.json ^
    >> %LOG_DIR%\stat_%STAMP%.log 2>&1

:end
echo.
echo ================================================================
echo   FIN : %DATE% %TIME%
echo   Logs : %LOG_DIR%
echo   CSVs : docs\master_v2_ext_*.csv
echo   Stats : docs\stat_ext_*.md
echo ================================================================
endlocal
pause
