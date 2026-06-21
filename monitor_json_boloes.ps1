# Monitora a pasta json-boloes em tempo real e exibe novos arquivos sendo populados
$pasta = "C:\Users\Marcio Fernando Maia\Meu Drive (loteriasdacaixa2024@gmail.com)\extracao-de-boloes\json-boloes"
$processados = @{}

Write-Host "========================================" -ForegroundColor Green
Write-Host "  MONITORANDO PASTA json-boloes" -ForegroundColor Green
Write-Host "  Pressione CTRL+C para parar" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

while ($true) {
    $arquivos = Get-ChildItem $pasta -Filter "*.json" -ErrorAction SilentlyContinue
    foreach ($arq in $arquivos) {
        $tamanho = $arq.Length
        $nome = $arq.Name
        $ultimaMod = $arq.LastWriteTime.ToString("HH:mm:ss")

        if (-not $processados.ContainsKey($nome)) {
            # Arquivo novo detectado
            $processados[$nome] = $tamanho
            Write-Host "[$ultimaMod] + $nome ($tamanho bytes)" -ForegroundColor Cyan
        } elseif ($processados[$nome] -ne $tamanho) {
            # Arquivo existente sendo populado (tamanho mudou)
            $diff = $tamanho - $processados[$nome]
            $processados[$nome] = $tamanho
            Write-Host "[$ultimaMod] * $nome ($tamanho bytes, +$diff)" -ForegroundColor Yellow
        }
    }
    Start-Sleep -Milliseconds 500
}
