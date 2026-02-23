@echo off
title DiskAnalyzer - Compilation en .exe
echo.
echo  ========================================
echo    Compilation DiskAnalyzer en .exe
echo  ========================================
echo.

:: Verification PyInstaller
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo  Installation de PyInstaller...
    pip install pyinstaller
)

echo  Compilation en cours...
echo.

pyinstaller --onefile --windowed --name "DiskAnalyzer" --clean "%~dp0DiskAnalyzer.py"

echo.
if exist "dist\DiskAnalyzer.exe" (
    echo  [OK] Compilation reussie !
    echo  Fichier : dist\DiskAnalyzer.exe
    echo.
    echo  Taille : 
    for %%A in ("dist\DiskAnalyzer.exe") do echo    %%~zA octets
) else (
    echo  [ERREUR] Compilation echouee.
)

echo.
pause
