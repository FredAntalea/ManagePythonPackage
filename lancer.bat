@echo off
chcp 65001 >nul
title Gestionnaire de Packages Python

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo Python n'est pas installe ou pas dans le PATH.
    pause
    exit /b 1
)

python app.py
