# Salva a chave da API do Claude no arquivo .env (só neste computador).
Write-Host ""
Write-Host "Cole a chave da API (botao direito ou Ctrl+V) e aperte Enter."
Write-Host "Nada vai aparecer enquanto voce cola. Isso e normal." -ForegroundColor DarkGray
$s = Read-Host "Chave" -AsSecureString
$k = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)).Trim()
if (-not $k.StartsWith("sk-ant-")) {
    Write-Host "Essa chave nao comeca com sk-ant-. Nada foi salvo. Rode de novo." -ForegroundColor Red
    exit 1
}
Set-Content -Path (Join-Path $PSScriptRoot ".env") -Value "ANTHROPIC_API_KEY=$k" -Encoding UTF8
Write-Host "Chave salva. Pode voltar para a conversa." -ForegroundColor Green
