@echo off
chcp 65001 >nul
title Suivi de Projets

REM Mapper le lecteur réseau si nécessaire (décommente et adapte la ligne ci-dessous)
REM net use P: "\\serveur\partage\suivi-projets" /persistent:no 2>nul
REM P:

cd /d "%~dp0"

REM Créer l'environnement virtuel s'il n'existe pas
if not exist "%USERPROFILE%\suivi-projets-venv" (
    echo 🔧 Création de l'environnement virtuel...
    python -m venv "%USERPROFILE%\suivi-projets-venv"
    if errorlevel 1 (
        echo ❌ Erreur : Python n'est pas installé ou pas dans le PATH.
        pause
        exit /b 1
    )
)

REM Activer l'environnement
call "%USERPROFILE%\suivi-projets-venv\Scripts\activate.bat"

REM Installer Flask si nécessaire
pip show flask >nul 2>&1
if errorlevel 1 (
    echo 📦 Installation de Flask...
    pip install --no-index --find-links=./packages flask
    if errorlevel 1 (
        echo ❌ Erreur lors de l'installation de Flask.
        pause
        exit /b 1
    )
)

echo.
echo ✅ Lancement de l'application...
echo    Ouvrez http://localhost:5000 dans votre navigateur
echo    Pour arrêter : fermez cette fenêtre ou Ctrl+C
echo.

REM Ouvrir le navigateur automatiquement
start http://localhost:5000

REM Lancer l'application
python app.py

pause
