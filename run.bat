@echo off
title Delaware Basin Evaluator
cd /d "%~dp0"
.venv\Scripts\streamlit.exe run delaware_basin_eval\app.py
if errorlevel 1 pause
