@echo off
REM ============================================================
REM  Planifier_Sauvegarde_Hebdo.bat
REM  Crée une tâche Windows : sauvegarde hebdo vendredi 16h00
REM  (à lancer une fois, éventuellement en administrateur)
REM ============================================================
setlocal
cd /d "%~dp0"

set "TASK_NAME=Estimation Elec - Backup hebdo BDD"
set "PYTHON_EXE=%~dp0python\python.exe"
set "SCRIPT=%~dp0scripts\backup_db.py"

if not exist "%PYTHON_EXE%" (
    echo [ERREUR] Python embeddable introuvable : %PYTHON_EXE%
    pause
    exit /b 1
)

schtasks /Create /F /TN "%TASK_NAME%" /TR "\"%PYTHON_EXE%\" \"%SCRIPT%\" --weekly" /SC WEEKLY /D FRI /ST 16:00 /RL LIMITED

if errorlevel 1 (
    echo.
    echo [ERREUR] Impossible de créer la tâche planifiée.
    echo          Relancez ce fichier en clic droit ^> Exécuter en tant qu'administrateur.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Tâche planifiée créée : "%TASK_NAME%"
echo      Vendredi 16:00 — scripts\backup_db.py --weekly
echo.
pause
exit /b 0
