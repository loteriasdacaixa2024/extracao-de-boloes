@echo off
echo Abrindo os terminais de servidores...

:: Pasta do servidor principal
set "SERVIDOR=D:\Loterias\AnalisePorPosicao-DiaDeSorte-Only"

:: Pasta do extrator de boloes
set "RAIZ=C:\Users\Marcio Fernando Maia\Meu Drive (loteriasdacaixa2024@gmail.com)\extracao-de-boloes"

:: Abre o Windows Terminal dividido em dois (Split-Pane Horizontal)
"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe" -d "%SERVIDOR%" powershell -NoExit -Command ".\.venv\Scripts\activate\; clear\; Write-Host -ForegroundColor Green '========================================'\; Write-Host -ForegroundColor Green '      SERVIDOR PRINCIPAL (app.py)'\; Write-Host -ForegroundColor Green '========================================'\; Write-Host 'Pressione ENTER para ligar...'\; Read-Host\; python app.py" ; split-pane -H -d "%RAIZ%" powershell -NoExit -Command ".\.venv\Scripts\activate\; clear\; Write-Host -ForegroundColor Cyan '========================================'\; Write-Host -ForegroundColor Cyan '   EXTRATOR DE BOLOES - Caixa (API)'\; Write-Host -ForegroundColor Cyan '========================================'\; Write-Host '[1] site: login + modalidade + filtros + ENTER'\; Write-Host 'Modalidade: detectada automaticamente (ou digite M1-M9/QSJ/DSP)'\; Write-Host 'Concurso: digite o numero (ex: 3020) ou ENTER para auto'\; Write-Host 'Arquivo: boloes_{concurso}_{modalidade}.json'\; Write-Host 'Salvamento: INCREMENTAL (cada bolao salvo na hora)'\; Write-Host 'Pressione ENTER para rodar...'\; Read-Host\; python -u script\baixar_boloes-API.py"
