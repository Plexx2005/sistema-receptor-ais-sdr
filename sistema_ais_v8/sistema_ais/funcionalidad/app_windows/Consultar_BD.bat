@echo off
chcp 65001 >nul
title Base de datos AIS
mode con: cols=170 lines=50
wsl.exe -- bash -lc "cd ~/sistema_ais/funcionalidad && source ~/ais_env/bin/activate && python consultar_db.py --n 30 --csv /mnt/c/Users/USER/Receptor_AIS/csv_exportado"
echo.
echo Las tablas tambien quedaron en C:\Users\USER\Receptor_AIS\csv_exportado (se abren con Excel).
pause
