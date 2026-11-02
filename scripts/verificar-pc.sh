#!/usr/bin/env bash
# Verifica se um computador Linux/macOS tem as ferramentas para rodar o
# KLEAD-CLIENT localmente ou para iniciar a instalação em VPS.
#
# Uso:
#   bash scripts/verificar-pc.sh                 # somente leitura, modo ambos
#   bash scripts/verificar-pc.sh --modo local    # local | vps | ambos
#   bash scripts/verificar-pc.sh --instalar      # oferece instalar o que falta, item a item
#
# Sem --instalar nada é alterado. Com --instalar, cada item só é instalado
# depois de você responder "s".

set -u

MODO="ambos"
INSTALAR=0
while [ $# -gt 0 ]; do
  case "$1" in
    --modo) MODO="${2:-ambos}"; shift 2 ;;
    --instalar) INSTALAR=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Opção desconhecida: $1"; exit 2 ;;
  esac
done
case "$MODO" in local|vps|ambos) ;; *) echo "--modo deve ser local, vps ou ambos"; exit 2 ;; esac

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; C='\033[0;36m'; N='\033[0m'
ok()    { printf "  ${G}[OK]${N}    %s\n" "$1"; }
falta() { printf "  ${R}[FALTA]${N} %s\n" "$1"; }
aviso() { printf "  ${Y}[AVISO]${N} %s\n" "$1"; AVISOS=$((AVISOS+1)); }
info()  { printf "  ${C}[INFO]${N}  %s\n" "$1"; }
titulo(){ printf "\n== %s ==\n" "$1"; }
tem()   { command -v "$1" >/dev/null 2>&1; }

AVISOS=0
FALTANDO=()   # itens no formato "Nome|pacote-apt|pacote-brew|instrução manual"
registrar() { FALTANDO+=("$1|$2|$3|${4:-}"); }

# Compara versões: versao_ge 3.12.1 3.12 -> verdadeiro
versao_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }
extrai_versao() { grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1; }

porta_ocupada() {
  if tem ss; then ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"
  elif tem lsof; then lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else return 1; fi
}

SO="$(uname -s)"
GERENCIADOR=""
if [ "$SO" = "Darwin" ] && tem brew; then GERENCIADOR="brew"
elif tem apt-get; then GERENCIADOR="apt"
elif tem dnf; then GERENCIADOR="dnf"; fi

echo "KLEAD-CLIENT - verificação do computador (modo: $MODO)"
echo "Pasta do projeto: $RAIZ"

titulo "Sistema"
info "$SO $(uname -r) ($(uname -m))"
if [ "$SO" = "Darwin" ]; then
  info "RAM: $(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 )) GB"
elif [ -r /proc/meminfo ]; then
  info "RAM: $(awk '/MemTotal/ {printf "%.1f GB", $2/1024/1024}' /proc/meminfo)"
fi
LIVRE_GB=$(df -Pk "$RAIZ" | awk 'NR==2 {printf "%d", $4/1024/1024}')
if [ "${LIVRE_GB:-0}" -lt 10 ]; then aviso "Disco com ${LIVRE_GB} GB livres; reserve pelo menos 10 GB."; else ok "Disco: ${LIVRE_GB} GB livres"; fi
[ -n "$GERENCIADOR" ] && ok "Gerenciador de pacotes: $GERENCIADOR" || aviso "Nenhum gerenciador suportado (apt, dnf ou brew) para --instalar"

titulo "Ferramentas comuns"
if tem git; then ok "$(git --version)"; else falta "Git"; registrar "Git" "git" "git"; fi
PY=""
for c in python3 python; do tem "$c" && PY="$c" && break; done
if [ -n "$PY" ]; then
  PYV=$($PY --version 2>&1 | extrai_versao)
  if versao_ge "$PYV" 3.12; then ok "Python $PYV ($PY)"
  elif [ "$MODO" != "vps" ]; then falta "Python $PYV; o backend precisa de 3.12 ou mais novo"; registrar "Python 3.12+" "python3" "python@3.12" "Em Ubuntu 22.04 use o PPA deadsnakes ou pyenv para obter 3.12."
  else ok "Python $PYV - suficiente para o launcher VPS"; fi
  if [ "$MODO" != "vps" ] && ! $PY -m venv --help >/dev/null 2>&1; then falta "módulo venv do Python"; registrar "python3-venv" "python3-venv" ""; fi
else
  falta "Python 3"; registrar "Python 3" "python3 python3-venv" "python@3.12"
fi

if [ "$MODO" != "vps" ]; then
  titulo "Execução local"
  if tem node; then
    NV=$(node --version | extrai_versao)
    if versao_ge "$NV" 20.9; then ok "Node.js $NV"; else falta "Node.js $NV; Next.js 16 precisa de 20.9+"; registrar "Node.js 20+" "" "node" "Linux: instale a versão LTS pelo site oficial nodejs.org ou pelo nvm."; fi
  else falta "Node.js"; registrar "Node.js 20+" "" "node" "Linux: instale a versão LTS pelo site oficial nodejs.org ou pelo nvm."; fi
  tem npm && ok "npm $(npm --version 2>/dev/null)" || { tem node && falta "npm"; }

  if tem docker; then
    ok "$(docker --version)"
    if docker info >/dev/null 2>&1; then ok "Docker Engine em execução"
    else falta "Docker instalado mas inacessível (serviço parado ou usuário fora do grupo docker)"; fi
    docker compose version >/dev/null 2>&1 && ok "Docker Compose $(docker compose version --short 2>/dev/null)" || aviso "docker compose indisponível"
  else
    falta "Docker (usado para o PostgreSQL local)"
    registrar "Docker" "docker.io" "" "macOS: instale o Docker Desktop. Linux: o pacote docker.io ou o repositório oficial docs.docker.com/engine/install."
  fi

  for p in 3000 8000 5432; do
    if porta_ocupada "$p"; then aviso "Porta $p já está em uso"; else ok "Porta $p livre"; fi
  done
  tem cloudflared && ok "cloudflared (opcional)" || info "cloudflared ausente (opcional: só para receber webhooks externos no PC)"

  titulo "Estado do projeto local"
  [ -d "$RAIZ/dashboard/backend/.venv" ] && ok "dashboard/backend/.venv criado" || info "dashboard/backend/.venv ainda não criado (passo 3 do guia local)"
  [ -f "$RAIZ/dashboard/backend/.env" ] && ok "dashboard/backend/.env existe" || info "dashboard/backend/.env ainda não criado (passo 2 do guia local)"
  [ -d "$RAIZ/dashboard/frontend/node_modules" ] && ok "Dependências do frontend instaladas" || info "dashboard/frontend/node_modules ainda não instalado (passo 4)"
  [ -f "$RAIZ/dashboard/frontend/.env.local" ] && ok "dashboard/frontend/.env.local existe" || info "dashboard/frontend/.env.local ainda não criado (passo 4)"
  if tem docker && docker info >/dev/null 2>&1; then
    ST=$(docker ps -a --filter "name=^klead-postgres$" --format '{{.Status}}')
    [ -n "$ST" ] && info "Container klead-postgres: $ST" || info "Container klead-postgres ainda não criado (passo 1)"
  fi
fi

if [ "$MODO" != "local" ]; then
  titulo "Iniciar instalação em VPS"
  if tem ssh && tem scp; then ok "OpenSSH: $(ssh -V 2>&1)"; else falta "Cliente OpenSSH (ssh e scp)"; registrar "OpenSSH" "openssh-client" "" "macOS já inclui ssh/scp."; fi
  if ls "$HOME"/.ssh/*.pub >/dev/null 2>&1; then ok "Chave(s) pública(s): $(cd "$HOME/.ssh" && ls *.pub | tr '\n' ' ')"
  else aviso "Nenhuma chave pública em ~/.ssh. Crie com: ssh-keygen -t ed25519 -f ~/.ssh/klead_vps"; fi
  for f in install.py install.sh docker-stack.yml; do [ -f "$RAIZ/$f" ] && ok "$f presente" || falta "$f não encontrado"; done
fi

titulo "Resumo"
if [ ${#FALTANDO[@]} -eq 0 ]; then
  printf "  ${G}Nenhuma ferramenta obrigatória faltando.${N}\n"
else
  printf "  ${R}Falta instalar:${N}\n"
  for item in "${FALTANDO[@]}"; do
    IFS='|' read -r nome apt brew manual <<<"$item"
    printf "   - %s %s\n" "$nome" "${manual:+($manual)}"
  done
fi
[ "$AVISOS" -gt 0 ] && printf "  ${Y}Avisos: %s (veja acima)${N}\n" "$AVISOS"

if [ "$INSTALAR" -eq 0 ]; then
  [ ${#FALTANDO[@]} -gt 0 ] && printf "\n  Para instalar com confirmação item a item: bash scripts/verificar-pc.sh --instalar\n"
  [ ${#FALTANDO[@]} -eq 0 ]; exit $?
fi
[ ${#FALTANDO[@]} -eq 0 ] && exit 0

titulo "Instalação guiada"
echo "  Cada item só será instalado se você responder 's'."
for item in "${FALTANDO[@]}"; do
  IFS='|' read -r nome apt brew manual <<<"$item"
  case "$GERENCIADOR" in
    apt) pacote="$apt"; cmd="sudo apt-get install -y $apt" ;;
    dnf) pacote="$apt"; cmd="sudo dnf install -y $apt" ;;
    brew) pacote="$brew"; cmd="brew install $brew" ;;
    *) pacote="" ;;
  esac
  if [ -z "$pacote" ]; then printf "  ${Y}- %s: instalação manual. %s${N}\n" "$nome" "$manual"; continue; fi
  [ -n "$manual" ] && echo "  Observação: $manual"
  read -r -p "  Instalar $nome agora com '$cmd'? [s/N] " resp
  case "$resp" in
    s|S|sim|y|yes)
      [ "$GERENCIADOR" = "apt" ] && sudo apt-get update -qq
      if eval "$cmd"; then ok "$nome instalado"; else falta "$nome: falha na instalação"; fi ;;
    *) info "$nome ignorado" ;;
  esac
done
echo
echo "  Rode a verificação de novo para confirmar."
