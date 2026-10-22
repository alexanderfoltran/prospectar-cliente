@echo off
cd /d "%~dp0"
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  "%LocalAppData%\Programs\Python\Python312\python.exe" "importar_divida_ativa.py"
) else (
  python "importar_divida_ativa.py"
)
if errorlevel 1 echo Verifique a mensagem acima. A base anterior foi preservada.
pause
