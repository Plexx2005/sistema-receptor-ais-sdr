@echo off
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 (
  echo No se encontro Python en Windows. Instala Python 3 desde python.org y marca "Add python.exe to PATH".
  pause
  exit /b 1
)
py -3 -c "import webview" >nul 2>&1
if errorlevel 1 (
  echo Instalando pywebview, solo la primera vez...
  py -3 -m pip install pywebview
  if errorlevel 1 (
    echo No se pudo instalar pywebview.
    pause
    exit /b 1
  )
)
start "" pyw -3 app_receptor_ais.pyw %*
