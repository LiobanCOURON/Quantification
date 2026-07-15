@echo off
setlocal enableextensions
chcp 65001 >nul 2>&1
title Quantification

REM ---------------------------------------------------------------
REM Launch the Quantification Tkinter app inside the .venv created
REM by install.bat.
REM ---------------------------------------------------------------

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERREUR] Le venv .venv est introuvable.
    echo          Lancez d'abord install.bat pour creer l'environnement.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
if %ERRORLEVEL% NEQ 0 (
    echo [ERREUR] Impossible d'activer le venv .venv.
    pause
    exit /b 1
)

echo [INFO] Lancement de ui.py...
python ui.py

REM Keep the window open so any exception/traceback stays visible.
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERREUR] ui.py s'est arrete avec le code %ERRORLEVEL%.
)
pause
endlocal