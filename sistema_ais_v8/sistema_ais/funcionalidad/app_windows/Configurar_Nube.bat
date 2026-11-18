@echo off
chcp 65001 >nul
title Configurar envio a la nube
echo Configuracion del envio a la nube (EC2). Enter conserva el valor entre corchetes.
echo.
wsl.exe -- bash -lc "cd ~/sistema_ais/funcionalidad && source ~/ais_env/bin/activate && python nube.py --configurar && echo && python nube.py --probar; python nube.py --estado"
echo.
pause
