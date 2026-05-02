@echo off
REM ============================================================
REM  Lancer_Estimateur_DEBUG.bat
REM  Version DEBUG : lance l'app AVEC fenetre console visible,
REM  pour diagnostiquer les erreurs en cas de probleme.
REM  Utilise Python embeddable local (./python/python.exe).
REM ============================================================

title Estimation Elec - DEBUG

echo.
echo  ============================================
echo   Estimation Elec  ^|  MODE DEBUG
echo   (fenetre console visible - logs en direct)
echo  ============================================
echo.

REM --- Aller dans le dossier du script ---------------------------
cd /d "%~dp0"

REM --- Verification Python embeddable ----------------------------
if not exist ".\python\python.exe" (
    echo [ERREUR] Python embeddable introuvable dans .\python\
    echo Lancez d'abord le setup initial.
    pause
    exit /b 1
)

REM --- Creation dossiers manquants -------------------------------
if not exist "logs"    mkdir logs
if not exist "exports" mkdir exports
if not exist "uploads" mkdir uploads

REM --- Verifier si le port 5000 est deja occupe ------------------
netstat -ano 2>nul | findstr ":5000 " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Port 5000 deja occupe - serveur probablement deja actif
    echo Ouverture du navigateur...
    start "" http://localhost:5000
    pause
    exit /b 0
)

REM --- Ouverture du navigateur dans 3 secondes ------------------
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:5000"

REM --- Lancement du serveur Flask (console visible) --------------
echo [OK] Lancement de Flask... (Ctrl+C pour arreter)
echo.
.\python\python.exe app.py

REM --- Si on arrive ici, le serveur s'est arrete -----------------
echo.
echo [INFO] Serveur arrete.
pause
exit /b 0
