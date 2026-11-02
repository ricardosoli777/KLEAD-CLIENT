<#
.SYNOPSIS
  Verifica se o computador Windows tem as ferramentas para rodar o KLEAD-CLIENT
  localmente ou para iniciar a instalação em VPS. Opcionalmente instala o que
  falta, sempre pedindo confirmação item a item.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\verificar-pc.ps1
  powershell -ExecutionPolicy Bypass -File scripts\verificar-pc.ps1 -Modo vps
  powershell -ExecutionPolicy Bypass -File scripts\verificar-pc.ps1 -Instalar

  -Modo     local | vps | ambos (padrão: ambos)
  -Instalar oferece instalar cada item ausente via winget; nada é instalado sem "s".

  Sem -Instalar o script é somente leitura: não altera nada no computador.
#>
param(
    [ValidateSet("local", "vps", "ambos")]
    [string]$Modo = "ambos",
    [switch]$Instalar
)

$ErrorActionPreference = "Continue"
$Raiz = Split-Path -Parent $PSScriptRoot
$Faltando = New-Object System.Collections.ArrayList
$Avisos = New-Object System.Collections.ArrayList
$dockerAtivo = $false

function Ok($msg)    { Write-Host "  [OK]    $msg" -ForegroundColor Green }
function Falta($msg) { Write-Host "  [FALTA] $msg" -ForegroundColor Red }
function Aviso($msg) { Write-Host "  [AVISO] $msg" -ForegroundColor Yellow; [void]$Avisos.Add($msg) }
function Info($msg)  { Write-Host "  [INFO]  $msg" -ForegroundColor Cyan }
function Titulo($msg){ Write-Host ""; Write-Host "== $msg ==" -ForegroundColor White }

function Tem($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Versao($texto) {
    if ($texto -match '(\d+)\.(\d+)(\.(\d+))?') { return [version]("{0}.{1}.{2}" -f $Matches[1], $Matches[2], ($(if ($Matches[4]) { $Matches[4] } else { 0 }))) }
    return $null
}

# Registra um item ausente com o pacote winget que o resolve (ou instrução manual).
function Registrar($nome, $wingetId, $manual) {
    [void]$Faltando.Add([pscustomobject]@{ Nome = $nome; Winget = $wingetId; Manual = $manual })
}

function PortaOcupada($porta) {
    try { return [bool](Get-NetTCPConnection -State Listen -LocalPort $porta -ErrorAction Stop) } catch { return $false }
}

$precisaLocal = $Modo -in @("local", "ambos")
$precisaVps   = $Modo -in @("vps", "ambos")

Write-Host "KLEAD-CLIENT - verificacao do computador (modo: $Modo)" -ForegroundColor White
Write-Host "Pasta do projeto: $Raiz"

# ---------------------------------------------------------------- sistema
Titulo "Sistema"
$os = Get-CimInstance Win32_OperatingSystem
Info ("{0} build {1} ({2})" -f $os.Caption, $os.BuildNumber, $env:PROCESSOR_ARCHITECTURE)
$ramGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
$livreGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
Info "RAM: $ramGB GB total, $livreGB GB livre"
if ($precisaLocal -and $ramGB -lt 8) { Aviso "Menos de 8 GB de RAM: Docker Desktop + build do Next.js podem ficar lentos." }
$letra = (Split-Path -Qualifier $Raiz).TrimEnd(":")
$disco = Get-PSDrive -Name $letra -ErrorAction SilentlyContinue
if ($disco) {
    $discoGB = [math]::Round($disco.Free / 1GB, 1)
    if ($discoGB -lt 10) { Aviso "Disco ${letra}: com $discoGB GB livres; reserve pelo menos 10 GB." } else { Ok "Disco ${letra}: $discoGB GB livres" }
}
if (Tem "winget") { Ok "winget disponivel (usado para instalar o que faltar)" } else { Aviso "winget ausente: instale o 'App Installer' pela Microsoft Store para usar -Instalar." }

# ---------------------------------------------------------------- comuns
Titulo "Ferramentas comuns"
if (Tem "git") { Ok ("Git: " + (git --version)) } else { Falta "Git"; Registrar "Git" "Git.Git" "" }

$pyCmd = $null
foreach ($c in @("python", "py")) {
    if (Tem $c) {
        $saida = & $c --version 2>&1 | Out-String
        if ($saida -match "Python \d") { $pyCmd = $c; break }
    }
}
if ($pyCmd) {
    $pyVer = Versao (& $pyCmd --version 2>&1 | Out-String)
    if ($pyVer -ge [version]"3.12.0") { Ok "Python $pyVer ($pyCmd)" }
    elseif ($precisaLocal) { Falta "Python $pyVer encontrado; o backend precisa de 3.12 ou mais novo"; Registrar "Python 3.12" "Python.Python.3.12" "" }
    else { Ok "Python $pyVer ($pyCmd) - suficiente para o launcher VPS" }
} else {
    Falta "Python 3 (o atalho da Microsoft Store nao conta)"; Registrar "Python 3.12" "Python.Python.3.12" ""
}

# ---------------------------------------------------------------- local
if ($precisaLocal) {
    Titulo "Execucao local"
    if (Tem "node") {
        $nodeVer = Versao (node --version)
        if ($nodeVer -ge [version]"20.9.0") { Ok "Node.js $nodeVer" } else { Falta "Node.js $nodeVer; Next.js 16 precisa de 20.9 ou mais novo"; Registrar "Node.js LTS" "OpenJS.NodeJS.LTS" "" }
    } else { Falta "Node.js"; Registrar "Node.js LTS" "OpenJS.NodeJS.LTS" "" }
    if (Tem "npm") { Ok ("npm " + (npm --version 2>$null)) } elseif (Tem "node") { Falta "npm (reinstale o Node.js)" }

    if (Tem "docker") {
        Ok ("Docker CLI: " + (docker --version))
        docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
        $dockerAtivo = ($LASTEXITCODE -eq 0)
        if ($dockerAtivo) {
            $osType = docker info --format "{{.OSType}}" 2>$null
            if ($osType -eq "linux") { Ok "Docker Engine em execucao (containers Linux)" } else { Falta "Docker esta em modo '$osType'; mude para containers Linux" }
        } else {
            Falta "Docker instalado, mas o Docker Desktop nao esta em execucao. Abra o Docker Desktop e rode de novo."
        }
        docker compose version 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok ("Docker Compose: " + (docker compose version --short 2>$null)) } else { Aviso "docker compose indisponivel" }
    } else {
        Falta "Docker Desktop (usado para o PostgreSQL local)"; Registrar "Docker Desktop" "Docker.DockerDesktop" "Requer WSL 2 e pode pedir reinicio do Windows."
    }

    if (Tem "wsl") {
        wsl --status 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "WSL disponivel" } else { Aviso "WSL nao configurado; o Docker Desktop precisa do WSL 2 (wsl --install, como administrador)." }
    }

    foreach ($p in @(3000, 8000, 5432)) {
        if (PortaOcupada $p) { Aviso "Porta $p ja esta em uso; pare o programa que a usa ou ajuste a configuracao." } else { Ok "Porta $p livre" }
    }

    if (Tem "cloudflared") { Ok "cloudflared (opcional, para webhooks da Meta no PC)" } else { Info "cloudflared ausente (opcional: so para receber webhooks externos no PC)" }

    Titulo "Estado do projeto local"
    $be = Join-Path $Raiz "dashboard\backend"
    $fe = Join-Path $Raiz "dashboard\frontend"
    if (Test-Path (Join-Path $be ".venv")) { Ok "Ambiente Python criado (dashboard\backend\.venv)" } else { Info "dashboard\backend\.venv ainda nao criado (passo 3 do guia local)" }
    if (Test-Path (Join-Path $be ".env")) { Ok "Configuracao do backend existe (dashboard\backend\.env)" } else { Info "dashboard\backend\.env ainda nao criado (passo 2 do guia local)" }
    if (Test-Path (Join-Path $fe "node_modules")) { Ok "Dependencias do frontend instaladas" } else { Info "dashboard\frontend\node_modules ainda nao instalado (passo 4 do guia local)" }
    if (Test-Path (Join-Path $fe ".env.local")) { Ok "Configuracao do frontend existe (dashboard\frontend\.env.local)" } else { Info "dashboard\frontend\.env.local ainda nao criado (passo 4 do guia local)" }
    if ($dockerAtivo) {
        $pg = docker ps -a --filter "name=^klead-postgres$" --format "{{.Status}}" 2>$null
        if ($pg) { Info "Container klead-postgres: $pg" } else { Info "Container klead-postgres ainda nao criado (passo 1 do guia local)" }
    }
}

# ---------------------------------------------------------------- vps
if ($precisaVps) {
    Titulo "Iniciar instalacao em VPS"
    $sshOk = (Tem "ssh") -and (Tem "scp")
    if ($sshOk) { Ok ("Cliente OpenSSH: " + (cmd /c "ssh -V 2>&1" | Out-String).Trim()) }
    else { Falta "Cliente OpenSSH (ssh e scp)"; Registrar "Cliente OpenSSH" "" "Configuracoes > Sistema > Recursos opcionais > Cliente OpenSSH, ou (admin): Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0" }
    $chaves = Get-ChildItem -Path (Join-Path $env:USERPROFILE ".ssh") -Filter "*.pub" -ErrorAction SilentlyContinue
    if ($chaves) { Ok ("Chave(s) SSH publica(s) em ~/.ssh: " + (($chaves | ForEach-Object { $_.Name }) -join ", ")) }
    else { Aviso "Nenhuma chave publica em ~/.ssh. Crie uma com: ssh-keygen -t ed25519 -f `$HOME\.ssh\klead_vps" }
    foreach ($f in @("install.py", "install.sh", "docker-stack.yml")) {
        if (Test-Path (Join-Path $Raiz $f)) { Ok "$f presente" } else { Falta "$f nao encontrado na pasta do projeto" }
    }
}

# ---------------------------------------------------------------- resumo
Titulo "Resumo"
if ($Faltando.Count -eq 0) {
    Write-Host "  Nenhuma ferramenta obrigatoria faltando." -ForegroundColor Green
} else {
    Write-Host "  Falta instalar:" -ForegroundColor Red
    foreach ($i in $Faltando) {
        $como = if ($i.Winget) { "winget install --id $($i.Winget) -e" } else { $i.Manual }
        Write-Host "   - $($i.Nome): $como"
    }
}
if ($Avisos.Count -gt 0) { Write-Host "  Avisos: $($Avisos.Count) (veja acima)" -ForegroundColor Yellow }

if (-not $Instalar) {
    if ($Faltando.Count -gt 0) { Write-Host ""; Write-Host "  Para instalar com confirmacao item a item, rode de novo com -Instalar." }
    exit ([int]($Faltando.Count -gt 0))
}

# ---------------------------------------------------------------- instalação guiada
if ($Faltando.Count -eq 0) { exit 0 }
Titulo "Instalacao guiada"
if (-not (Tem "winget")) { Write-Host "  winget indisponivel; instale manualmente os itens acima." -ForegroundColor Yellow; exit 1 }
Write-Host "  Cada item sera instalado somente se voce responder 's'."
foreach ($i in $Faltando) {
    if (-not $i.Winget) { Write-Host "  - $($i.Nome): instalacao manual -> $($i.Manual)" -ForegroundColor Yellow; continue }
    if ($i.Manual) { Write-Host "  Observacao: $($i.Manual)" -ForegroundColor Yellow }
    $resp = Read-Host "  Instalar $($i.Nome) agora com 'winget install --id $($i.Winget) -e'? [s/N]"
    if ($resp -match '^(s|sim|y|yes)$') {
        winget install --id $i.Winget -e --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { Ok "$($i.Nome) instalado" } else { Falta "$($i.Nome): o winget retornou codigo $LASTEXITCODE" }
    } else {
        Info "$($i.Nome) ignorado"
    }
}
Write-Host ""
Write-Host "  Feche e abra o terminal (para atualizar o PATH) e rode a verificacao de novo." -ForegroundColor Cyan
