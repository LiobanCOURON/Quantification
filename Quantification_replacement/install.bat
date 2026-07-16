@echo off
setlocal enableextensions enabledelayedexpansion
chcp 65001 >nul 2>&1
title Installation - Quantification

REM ---------------------------------------------------------------
REM Anchor the working directory to THIS script's own folder so the
REM venv is always created inside Quantification_replacement/ no
REM matter where the wrapper (root Install.bat) is launched from.
REM ---------------------------------------------------------------
cd /d "%~dp0"

echo ============================================================
echo   Installation de l'environnement Quantification
echo ============================================================
echo.

REM ---------------------------------------------------------------
REM 1) Detect Python 3.12 (python, then py launcher)
REM ---------------------------------------------------------------
set "PYEXE="
set "PYVER="

REM Try plain "python" first (most common on a dev machine).
for /f "delims=" %%v in ('python --version 2^>nul') do set "PYVER=%%v"
if defined PYVER (
    set "PYEXE=python"
    echo [OK] Python trouve dans le PATH : !PYVER!
    goto :have_python
)

REM Fallback to the Windows py launcher, asking explicitly for 3.12.
set "PYVER="
for /f "delims=" %%v in ('py -3.12 --version 2^>nul') do set "PYVER=%%v"
if defined PYVER (
    set "PYEXE=py -3.12"
    echo [OK] Python 3.12 trouve via le lanceur py : !PYVER!
    goto :have_python
)

echo [INFO] Python n'est pas installe ou absent du PATH.
echo        Tentative d'installation automatique de Python 3.12...

REM ---------------------------------------------------------------
REM 2) Auto-install Python 3.12 (winget preferred on Windows 11)
REM ---------------------------------------------------------------
where winget >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [INFO] Installation via winget...
    winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
    if !ERRORLEVEL! NEQ 0 (
        echo [ERREUR] winget a echoue. Veuillez installer Python 3.12 manuellement
        echo          depuis https://www.python.org/downloads/ puis relancer ce script.
        pause
        exit /b 1
    )
    REM winget installs into a versioned folder; refresh PATH for this session
    REM by re-reading the Machine + User environment variables.
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul`) do set "MACH_PATH=%%B"
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKCU\Environment" /v Path 2^>nul`) do set "USER_PATH=%%B"
    set "PATH=!MACH_PATH!;!USER_PATH!"
) else (
    REM Fallback: download the official CPython installer and run it silently.
    echo [INFO] winget indisponible. Telechargement de l'installateur officiel...
    set "INSTALLER=%TEMP%\python3.12-installer.exe"
    set "URL=https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    where curl >nul 2>&1
    if !ERRORLEVEL!==0 (
        curl -L -o "!INSTALLER!" "!URL!"
    ) else (
        echo [ERREUR] curl est requis mais absent. Installez Python 3.12 manuellement
        echo          depuis https://www.python.org/downloads/ puis relancez ce script.
        pause
        exit /b 1
    )
    if not exist "!INSTALLER!" (
        echo [ERREUR] Le telechargement a echoue. Installez Python 3.12 manuellement.
        pause
        exit /b 1
    )
    echo [INFO] Installation silencieuse de Python 3.12...
    "!INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
    del "!INSTALLER!" >nul 2>&1
    REM Refresh PATH for this session.
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKCU\Environment" /v Path 2^>nul`) do set "USER_PATH=%%B"
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul`) do set "MACH_PATH=%%B"
    set "PATH=!MACH_PATH!;!USER_PATH!"
)

REM Re-detect after install.
set "PYVER="
for /f "delims=" %%v in ('py -3.12 --version 2^>nul') do set "PYVER=%%v"
if defined PYVER (
    set "PYEXE=py -3.12"
    goto :have_python
)
set "PYVER="
for /f "delims=" %%v in ('python --version 2^>nul') do set "PYVER=%%v"
if defined PYVER (
    set "PYEXE=python"
    goto :have_python
)

echo [ERREUR] Python reste introuvable apres l'installation.
echo          Ouvrez une NOUVELLE console puis relancez install.bat
echo          (le PATH doit etre rafraichi par l'explorateur Windows).
pause
exit /b 1

:have_python
echo.

REM ---------------------------------------------------------------
REM 3) Create the virtual environment (.venv)
REM ---------------------------------------------------------------
if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Le venv .venv existe deja - reutilisation.
) else (
    echo [INFO] Creation du venv .venv...
    !PYEXE! -m venv .venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ERREUR] La creation du venv a echoue.
        pause
        exit /b 1
    )
)

REM ---------------------------------------------------------------
REM 4) Activate + upgrade pip + install dependencies
REM ---------------------------------------------------------------
call ".venv\Scripts\activate.bat"
if !ERRORLEVEL! NEQ 0 (
    echo [ERREUR] Impossible d'activer le venv.
    pause
    exit /b 1
)

echo [INFO] Mise a jour de pip...
python -m pip install --upgrade pip

echo.
echo [INFO] Installation des dependances...
python -m pip install numpy Pillow matplotlib nibabel scikit-image "aicsimageio[czi]" "aicspylibczi>=3.1.1"
if !ERRORLEVEL! NEQ 0 (
    echo [ERREUR] L'installation d'une ou plusieurs dependances a echoue.
    echo          Verifiez les messages ci-dessus.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------
REM 5) Atlas rat WHS_SD_rat_v4 : telechargement + extraction + images
REM    (necessite le python du venv : on est apres l'activation)
REM ---------------------------------------------------------------
set "ATLASDIR=%~dp0Rat atlas"
if not exist "%ATLASDIR%" mkdir "%ATLASDIR%"

echo.
echo ============================================================
echo   Preparation de l'atlas rat (WHS_SD_rat_v4)
echo ============================================================

call :download "https://www.nitrc.org/frs/download.php/12260/WHS_SD_rat_atlas_v4.nii.gz" "%ATLASDIR%\WHS_SD_rat_atlas_v4.nii.gz" "atlas v4 (.nii.gz)" "%ATLASDIR%\WHS_SD_rat_atlas_v4.nii"
call :download "https://www.nitrc.org/frs/download.php/12261/WHS_SD_rat_atlas_v4.label" "%ATLASDIR%\WHS_SD_rat_atlas_v4.label" "atlas v4 (.label)"
call :download "https://www.nitrc.org/frs/downloadlink.php/9423" "%ATLASDIR%\WHS_SD_rat_T2star_v1.01.nii.gz" "T2star v1.01 (.nii.gz)" "%ATLASDIR%\WHS_SD_rat_T2star_v1.01.nii"

REM Extraction des .nii.gz via le python du venv (aucun outil externe requis).
set "GZ1=%ATLASDIR%\WHS_SD_rat_atlas_v4.nii.gz"
set "NI1=%ATLASDIR%\WHS_SD_rat_atlas_v4.nii"
if not exist "%NI1%" (
    if exist "%GZ1%" (
        echo [ATLAS] Extraction de WHS_SD_rat_atlas_v4.nii.gz ...
        python -c "import gzip,shutil; shutil.copyfileobj(gzip.open(r'%GZ1%','rb'), open(r'%NI1%','wb'))"
        if exist "%NI1%" del "%GZ1%"
    )
) else (
    echo [ATLAS] WHS_SD_rat_atlas_v4.nii deja present.
)

set "GZ2=%ATLASDIR%\WHS_SD_rat_T2star_v1.01.nii.gz"
set "NI2=%ATLASDIR%\WHS_SD_rat_T2star_v1.01.nii"
if not exist "%NI2%" (
    if exist "%GZ2%" (
        echo [ATLAS] Extraction de WHS_SD_rat_T2star_v1.01.nii.gz ...
        python -c "import gzip,shutil; shutil.copyfileobj(gzip.open(r'%GZ2%','rb'), open(r'%NI2%','wb'))"
        if exist "%NI2%" del "%GZ2%"
    )
) else (
    echo [ATLAS] WHS_SD_rat_T2star_v1.01.nii deja present.
)

echo.
echo [ATLAS] Generation des images 512x512 (AtlasImgs/)...
python scripts/generate_atlas_sequence.py
echo [ATLAS] Generation terminee.

echo.
echo ============================================================
echo   Installation terminee avec succes !
echo   Pour lancer l'application : double-cliquez sur lunch.bat
echo ============================================================
pause
goto :eof

REM ---------------------------------------------------------------
REM :download <URL> <OUT> <LABEL>
REM   Telecharge OUT depuis URL avec une barre de progression (#).
REM   Saute le telechargement si OUT existe deja (idempotent).
REM   -#  => barre de progression type "##########" sur stderr.
REM   -L  => suit les redirections (nitrc downloadlink notamment).
REM ---------------------------------------------------------------
:download
set "URL=%~1"
set "OUT=%~2"
set "LABEL=%~3"
set "DONE=%~4"
REM Saute si le fichier cible OU le fichier deja extrait existe deja
REM (re-joue sur un venv recree : les .nii extraits survivent a la suppression du venv).
if exist "%OUT%" (
    echo [ATLAS] %LABEL% deja present - telechargement saute.
    goto :eof
)
if defined DONE if exist "%DONE%" (
    echo [ATLAS] %LABEL% deja extrait - telechargement saute.
    goto :eof
)
echo.
echo [ATLAS] Telechargement %LABEL% ...
where curl >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [ERREUR] curl est requis pour telecharger l'atlas mais il est absent.
    pause
    exit /b 1
)
curl -# -L -o "%OUT%" "%URL%"
if not exist "%OUT%" (
    echo [ERREUR] Echec du telechargement de %LABEL% (verifiez la connexion).
    pause
    exit /b 1
)
echo [ATLAS] %LABEL% telecharge.
goto :eof

endlocal