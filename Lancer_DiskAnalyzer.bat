@echo off
title DiskAnalyzer - Installation et lancement
echo.
echo  ========================================
echo    DiskAnalyzer - Analyseur d'espace
echo  ========================================
echo.

:: Verification Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERREUR] Python n'est pas installe ou pas dans le PATH.
    echo  Telechargez Python : https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Installation des dependances
echo  Installation des dependances...
pip install customtkinter -q
echo.

:: Lancement
echo  Lancement de DiskAnalyzer...
echo.
python "%~dp0DiskAnalyzer.py" %*

if errorlevel 1 (
    echo.
    echo  [ERREUR] Probleme au lancement.
    pause
)
