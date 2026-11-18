@echo off
chcp 65001 >nul
echo Actualizando Receptor AIS a la v8 dentro de WSL...
wsl.exe -- bash -l /mnt/c/Users/USER/sistema_ais_v8/instalar_v8.sh
echo.
pause
