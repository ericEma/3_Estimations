@echo off
REM ============================================================
REM  Lancer_Estimateur.bat  —  Estimation Elec
REM  Lance Flask + ouvre le navigateur automatiquement.
REM ============================================================
setlocal
title Estimation Elec — Lancement

cd /d "%~dp0"

echo.
echo  ============================================
echo   Estimation Elec  ^|  Egis Branche Sud
echo  ============================================
echo.

REM --- Dossiers requis -------------------------------------------
if not exist "logs"    mkdir logs
if not exist "exports" mkdir exports
if not exist "uploads" mkdir uploads

REM --- Selection Python ------------------------------------------
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

REM --- Port 5000 deja actif ? ------------------------------------
netstat -ano 2>nul | findstr /C:":5000 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Serveur deja actif — ouverture du navigateur.
    start "" http://localhost:5000
    exit /b 0
)

REM --- Lancement du serveur Flask --------------------------------
echo [OK] Demarrage du serveur Flask...
start "Estimation Elec — Serveur" /D "%~dp0" cmd /k "%PYTHON_EXE% app.py 2>&1"

REM --- Attente : on interroge le port jusqu'a 20 secondes --------
echo [..] En attente du serveur (max 20 s)...
set TRIES=0

:WAIT
timeout /t 1 /nobreak >nul
set /a TRIES+=1
netstat -ano 2>nul | findstr /C:":5000 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 goto READY
if %TRIES% geq 20 goto TIMEOUT
goto WAIT

:READY
echo [OK] Serveur pret en %TRIES% s — ouverture du navigateur...
echo.
start "" http://localhost:5000
echo  ============================================
echo   http://localhost:5000
echo   (Fermez la fenetre Serveur pour arreter)
echo  ============================================
echo.
timeout /t 3 /nobreak >nul
exit /b 0

:TIMEOUT
echo.
echo [ERREUR] Le serveur n'a pas demarre en 20 s.
echo          Consultez la fenetre "Estimation Elec — Serveur"
echo          pour voir le message d'erreur.
echo.
pause
exit /b 1
