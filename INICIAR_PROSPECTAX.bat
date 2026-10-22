@echo off
cd /d "%~dp0"
title ProspectaX - servidor local
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  "%LocalAppData%\Programs\Python\Python312\python.exe" "iniciar_local.py"
) else (
  python "iniciar_local.py"
)
if errorlevel 1 (
  echo Nao foi possivel iniciar. Confira a mensagem acima.
  echo Se o sistema ja estiver aberto, acesse http://127.0.0.1:8765 no navegador.
  pause
)
