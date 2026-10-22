@echo off
chcp 65001 >nul
title ProspectaX - Atualizar Base Receita Federal
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  ProspectaX - ATUALIZAR BASE DA RECEITA FEDERAL
echo ============================================================
echo.
echo Este processo vai:
echo   1. Baixar a base publica mais recente do CNPJ/RFB
echo   2. Converter os arquivos para Parquet
echo   3. Gerar dados_parquet\prospectax_index.duckdb
echo   4. Trocar a pasta dados_parquet somente se tudo der certo
echo.
echo IMPORTANTE:
echo   - Feche o ProspectaX antes de continuar.
echo   - A primeira carga pode levar varias horas ou mais de um dia.
echo   - Reserve preferencialmente 80 GB livres ou mais em SSD.
echo   - Nao desligue o computador durante download e conversao.
echo   - A base antiga sera renomeada para backup.
echo.
pause

set "PYTHON_CMD="
for %%V in (314 313 312 311) do (
  if not defined PYTHON_CMD if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python%%V\python.exe"
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if "%PYTHON_CMD%"=="" (
  where py >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py"
)

if "%PYTHON_CMD%"=="" (
  echo ERRO: Python nao foi encontrado no PATH.
  echo Instale Python 3.11+ para usar o atualizador.
  pause
  exit /b 1
)

echo Usando: %PYTHON_CMD%
echo.
"%PYTHON_CMD%" "_ATUALIZADOR_RECEITA\atualizar_base_receita.py" --replace
if errorlevel 1 (
  echo.
  echo ERRO: a atualizacao nao foi concluida.
  pause
  exit /b 1
)

echo.
echo Base atualizada com sucesso.
echo Abra o ProspectaX novamente para usar os dados novos.
pause
