@echo off
REM ============================================================
REM  Arreter_Estimateur.bat
REM  Arrete proprement le serveur Flask
REM  Port : variable PORT (defaut 8080)
REM  A utiliser seulement si le serveur ne se ferme pas tout seul
REM ============================================================

title Arret Estimation Elec

cd /d "%~dp0"
if not defined PORT set "PORT=8080"

echo.
echo  ============================================
echo   Arret du serveur Estimation Elec
echo   Port : %PORT%
echo  ============================================
echo.

REM --- Tentative 1 : arret propre via l'API /api/shutdown -----
echo [1/2] Envoi du signal d'arret via HTTP...
powershell -Command "try { Invoke-WebRequest -Uri 'http://localhost:%PORT%/api/shutdown' -Method POST -TimeoutSec 2 | Out-Null; Write-Host '       OK - signal envoye' } catch { Write-Host '       (pas de serveur actif)' }"

REM --- Attente 2s pour laisser le temps au serveur de se fermer -
timeout /t 2 /nobreak >nul

REM --- Tentative 2 : kill brutal si encore en vie ---------------
echo [2/2] Verification / kill de secours...
netstat -ano 2>nul | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo        Serveur encore actif - kill force
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    echo        OK - processus termine
) else (
    echo        Serveur deja arrete
)

echo.
echo  ============================================
echo   Arret termine
echo  ============================================
echo.
timeout /t 2 /nobreak >nul
exit /b 0
