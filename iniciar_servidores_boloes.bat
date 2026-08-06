@echo off
chcp 65001 >nul
setlocal EnableExtensions

echo ========================================
echo  Iniciar servidores + extrator + login
echo ========================================

:: Pasta do extrator = pasta deste .bat
set "PASTA_BOLOES=%~dp0"
if "%PASTA_BOLOES:~-1%"=="\" set "PASTA_BOLOES=%PASTA_BOLOES:~0,-1%"

set "RAIZ="
set "SERVIDOR_PY="
set "PYTHON_EXE="

:: Ordem de busca da RAIZ do servidor Flask
call :resolver_raiz "%PASTA_BOLOES%\.."
if "%RAIZ%"=="" call :resolver_raiz "D:\Loterias\AnalisePorPosicao-DiaDeSorte-Only"
if "%RAIZ%"=="" call :resolver_raiz "%PASTA_BOLOES%\..\LoteriasBoloesDaSorte"
if "%RAIZ%"=="" call :resolver_raiz "I:\Meu Drive\LoteriasBoloesDaSorte"
if "%RAIZ%"=="" call :resolver_raiz "D:\Loterias\LoteriasBoloesDaSorte"

if "%RAIZ%"=="" (
  echo.
  echo [ERRO] Nao encontrei servidor ^(app.py ou servidor.py^) com venv.
  echo.
  pause
  exit /b 1
)

if not exist "%PASTA_BOLOES%\script\baixar_boloes-API.py" (
  echo [ERRO] Extrator nao encontrado:
  echo   %PASTA_BOLOES%\script\baixar_boloes-API.py
  pause
  exit /b 1
)

echo.
echo [OK] Servidor : %RAIZ%\%SERVIDOR_PY%
echo [OK] Python   : %PYTHON_EXE%
echo [OK] Extrator : %PASTA_BOLOES%\script\baixar_boloes-API.py
echo [OK] Login auto: LIGADO ^(LOGIN_CAIXA_AUTO=1^)
echo.
echo Abrindo Windows Terminal...
echo.

set "LOGIN_CAIXA_AUTO=1"

:: Painel 1 = servidor (liga direto) | Painel 2 = extrator + login (inicia direto)
"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe" ^
  -d "%RAIZ%" cmd /k "title SERVIDOR & echo ======================================== & echo  SERVIDOR PRINCIPAL ^(%SERVIDOR_PY%^) & echo ======================================== & echo Pasta: %RAIZ% & echo. & echo Ligando servidor... & \"%PYTHON_EXE%\" \"%RAIZ%\%SERVIDOR_PY%\"" ^
  ; split-pane -H -d "%PASTA_BOLOES%" cmd /k "title EXTRATOR & set LOGIN_CAIXA_AUTO=1 & echo ======================================== & echo  EXTRATOR DE BOLOES - Caixa ^(API^) & echo  VERSAO: CHECKPOINT-RESUME-v3.1 & echo ======================================== & echo LOGIN AUTOMATICO: ligado ^(mesmo Edge^) & echo   1^) Digite o codigo do e-mail no Edge & echo   2^) Clique MANUAL em Enviar & echo   3^) Depois ENTER/SIM no terminal & echo Credenciais: config.local.json ^(gitignore^) & echo. & echo Iniciando extrator... & \"%PYTHON_EXE%\" -u \"%PASTA_BOLOES%\script\baixar_boloes-API.py\""

endlocal
exit /b 0


:resolver_raiz
:: %1 = candidato a pasta raiz do servidor
set "CAND=%~f1"
if not exist "%CAND%" goto :eof

set "SRV="
if exist "%CAND%\app.py" set "SRV=app.py"
if exist "%CAND%\servidor.py" set "SRV=servidor.py"
if "%SRV%"=="" goto :eof

set "PY="
if exist "%CAND%\.venv\Scripts\python.exe" set "PY=%CAND%\.venv\Scripts\python.exe"
if "%PY%"=="" if exist "%CAND%\venv\Scripts\python.exe" set "PY=%CAND%\venv\Scripts\python.exe"
if "%PY%"=="" goto :eof

set "RAIZ=%CAND%"
set "SERVIDOR_PY=%SRV%"
set "PYTHON_EXE=%PY%"
goto :eof
