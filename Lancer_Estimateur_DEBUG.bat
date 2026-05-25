@echo off
REM ============================================================
REM  Lancer_Estimateur_DEBUG.bat
REM  Flask dans CETTE fenetre (toutes les traces / stack traces).
REM  Meme detection Python que Lancer_Estimateur.bat.
REM
REM  ESTIMATION_DEBUG_LOG : chemin NDJSON pour sessions debug Cursor
REM ============================================================
setlocal
title Estimation Elec — DEBUG

cd /d "%~dp0"

set "ESTIMATION_DEBUG_LOG=%~dp0debug-b2b456.log"

echo.
echo  ============================================
echo   Estimation Elec  ^|  MODE DEBUG
echo   Flask dans cette fenetre ^(app.py debug=True^)
echo   ESTIMATION_DEBUG_LOG=%ESTIMATION_DEBUG_LOG%
echo  ============================================
echo.

if not exist "logs"     mkdir logs
if not exist "exports"  mkdir exports
if not exist "uploads"  mkdir uploads

set "PYTHON_EXE="
if exist ".\python\python.exe" (
    set "PYTHON_EXE=.\python\python.exe"
    echo [OK] Python embeddable : .\python\
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=python"
        echo [OK] Python systeme detecte
    ) else (
        echo [ERREUR] Aucun Python trouve.
        pause & exit /b 1
    )
)

netstat -ano 2>nul | findstr /C:":5000 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port 5000 deja en ecoute.
    start "" http://localhost:5000/matching
    pause
    exit /b 0
)

REM Navigateur ~6 s apres le lancement (laisse Flask demarrer)
start "" cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:5000/matching"

echo [OK] Demarrage Flask ici — Ctrl+C pour arreter.
echo     Navigateur /matching vers 6 s si le serveur est pret.
echo.
"%PYTHON_EXE%" app.py

echo.
echo [INFO] Serveur arrete.
pause
exit /b 0
