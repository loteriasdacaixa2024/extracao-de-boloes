@echo off
chcp 65001 >nul
setlocal EnableExtensions

echo Abrindo os terminais de servidores...
echo Extrator: CHECKPOINT-RESUME-v3.1  (script\baixar_boloes-API.py)
echo Login automatico Caixa: ATIVADO neste atalho (LOGIN_CAIXA_AUTO=1)

:: Pasta deste projeto (onde esta o .bat) e pasta pai (venv + app.py)
set "PASTA_BOLOES=%~dp0"
if "%PASTA_BOLOES:~-1%"=="\" set "PASTA_BOLOES=%PASTA_BOLOES:~0,-1%"
for %%i in ("%PASTA_BOLOES%\..") do set "RAIZ=%%~fi"
for %%i in ("%PASTA_BOLOES%") do set "NOME_PASTA=%%~nxi"

:: Credenciais: config.local.json dentro de %NOME_PASTA% (NAO versionar no GitHub)
:: Ativa o login automatizado no mesmo Edge do extrator
set "LOGIN_CAIXA_AUTO=1"

:: Abre o Windows Terminal dividido em dois (Split-Pane Horizontal)
"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe" ^
  -d "%RAIZ%" powershell -NoExit -Command ".\.venv\Scripts\activate\; clear\; Write-Host -ForegroundColor Green '========================================'\; Write-Host -ForegroundColor Green ' SERVIDOR PRINCIPAL (app.py)'\; Write-Host -ForegroundColor Green '========================================'\; Write-Host 'Pressione ENTER para ligar...'\; Read-Host\; python app.py" ^
  ; split-pane -H -d "%RAIZ%" powershell -NoExit -Command "$env:LOGIN_CAIXA_AUTO='1'\; .\.venv\Scripts\activate\; clear\; Write-Host -ForegroundColor Cyan '========================================'\; Write-Host -ForegroundColor Cyan ' EXTRATOR DE BOLOES - Caixa (API)'\; Write-Host -ForegroundColor Yellow ' VERSAO: CHECKPOINT-RESUME-v3.1'\; Write-Host -ForegroundColor Cyan '========================================'\; Write-Host -ForegroundColor Green ' LOGIN AUTOMATICO: ligado (mesmo Edge)'\; Write-Host '  - Edge abre -> termos/CPF/codigo/senha'\; Write-Host '  - Digite o codigo do e-mail e clique MANUAL em Enviar'\; Write-Host '  - Depois: ENTER / SIM no terminal como antes'\; Write-Host '[1] modalidade + concurso -> Edge+LOGIN -> PAUSA -> digite SIM'\; Write-Host '[2] manual: ENTER a cada pagina'\; Write-Host -ForegroundColor Yellow 'Credenciais: %NOME_PASTA%\config.local.json (gitignore)'\; Write-Host -ForegroundColor Yellow 'IMPORTANTE: se NAO aparecer v3.1, feche ESTE terminal e abra de novo'\; Write-Host 'Pressione ENTER para rodar...'\; Read-Host\; python -u %NOME_PASTA%\script\baixar_boloes-API.py"

endlocal
