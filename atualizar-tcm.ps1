# Atualiza o TCM-BA a partir deste computador (o TCM bloqueia acessos de fora
# do Brasil, por isso o robô do GitHub não consegue). Rode uma vez por semana:
#   botão direito neste arquivo > "Executar com o PowerShell"
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $env:LOCALAPPDATA "bl-venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Ambiente do robo nao encontrado em $py" -ForegroundColor Red; Read-Host "Enter para sair"; exit 1 }
$env:PYTHONIOENCODING = "utf-8"

Write-Host "1/3 Baixando as novidades do GitHub..." -ForegroundColor Cyan
git pull --rebase --quiet

Write-Host "2/3 Lendo o TCM-BA (pode levar alguns minutos)..." -ForegroundColor Cyan
& $py -m robo --so-tcm --sem-alerta

Write-Host "3/3 Enviando para o site..." -ForegroundColor Cyan
git add dados site
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit --quiet -m "TCM-BA: atualização de $(Get-Date -Format yyyy-MM-dd)"
    git push --quiet
    Write-Host "Pronto! O site atualiza em uns 2 minutos." -ForegroundColor Green
} else {
    Write-Host "Nenhuma novidade no TCM-BA desta vez." -ForegroundColor Green
}
Read-Host "Enter para fechar"
