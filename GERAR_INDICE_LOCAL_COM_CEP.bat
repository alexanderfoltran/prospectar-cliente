@echo off
cd /d "%~dp0"
echo Gerando indice local com CEP, sem baixar dados da Receita...
echo Log: logs\gerar_indice_local_com_cep.log
"%LocalAppData%\Programs\Python\Python312\python.exe" "_ATUALIZADOR_RECEITA\gerar_indice_local_com_cep.py"
pause
