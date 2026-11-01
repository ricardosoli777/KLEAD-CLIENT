#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# KLEAD — Instalação/wizard para nova VPS ou VPS com infra parcial
# Ubuntu 22.04 / Ubuntu 24.04 / Debian 12
# Arquiteturas: amd64, arm64 (Oracle Cloud Ampere)
#
# Rode como root, de DENTRO do repositório clonado:
#   git clone https://github.com/ricardosoli777/KLEAD-CLIENT.git
#   cd KLEAD-CLIENT
#   chmod +x install.sh && sudo ./install.sh
#
# Regra principal: o script sempre detecta o que já está rodando na VPS
# (Traefik, Portainer, PostgreSQL, Evolution GO) e reaproveita — nunca
# reinstala/sobrescreve infraestrutura existente. Só pergunta o que for
# realmente novo.
#
# Configuração privada: /etc/klead/klead.env, fora do repositório.
# Um .env antigo pode ser migrado; reexecuções preservam todas as variáveis.
# ─────────────────────────────────────────────────────────────────────────────

set -e
umask 077

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

log()   { echo -e "${GREEN}[KLEAD-CLIENT]${NC} $1"; }
info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[AVISO]${NC} $1"; }
die()   { echo -e "${RED}[ERRO]${NC} $1"; exit 1; }

cd "$(dirname "$(readlink -f "$0")")"
[ -f "docker-stack.yml" ] || die "Arquivos do instalador incompletos."

if [ "$EUID" -ne 0 ]; then
  die "Execute como root: sudo ./install.sh"
fi

# Dependências do wizard; Python/FastAPI e Node/Next.js do app ficam nas imagens.
OS_ID=$(grep '^ID=' /etc/os-release | cut -d= -f2 | tr -d '"')
OS_VER=$(grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '"')
case "${OS_ID}:${OS_VER}" in
  ubuntu:22.04|ubuntu:24.04|debian:12) ;;
  *) die "Sistema suportado: Ubuntu 22.04/24.04 ou Debian 12." ;;
esac
if ! command -v python3 >/dev/null || ! command -v git >/dev/null || ! command -v curl >/dev/null || ! command -v openssl >/dev/null; then
  apt-get update -q
  apt-get install -y python3 git curl openssl ca-certificates
fi
KLEAD_CONFIG_FILE="${KLEAD_CONFIG_FILE:-/etc/klead/klead.env}"
[[ "$KLEAD_CONFIG_FILE" = /* ]] || die "KLEAD_CONFIG_FILE precisa ser absoluto."
case "$(realpath -m "$KLEAD_CONFIG_FILE")" in
  /etc/klead/*.env) ;;
  *) die "Guarde a configuração privada em /etc/klead/*.env." ;;
esac
export KLEAD_CONFIG_FILE
mkdir -p "$(dirname "$KLEAD_CONFIG_FILE")"
chmod 700 "$(dirname "$KLEAD_CONFIG_FILE")"
if [ ! -f "$KLEAD_CONFIG_FILE" ] && [ -f .env ]; then
  info "Migrando configuração legada para arquivo privado fora do repositório."
  cp .env "$KLEAD_CONFIG_FILE"
  chmod 600 "$KLEAD_CONFIG_FILE"
fi
if [ -f "$KLEAD_CONFIG_FILE" ]; then
  CONFIG_EXPORTS=$(python3 scripts/install-env.py export "$KLEAD_CONFIG_FILE") || die "Configuração privada inválida."
  eval "$CONFIG_EXPORTS"
  unset CONFIG_EXPORTS
fi

# ─────────────────────────────────────────────────────────────────────────────
# Helpers de prompt
# ─────────────────────────────────────────────────────────────────────────────

# ask VAR "pergunta" ["default"]
ask() {
  local varname="$1" message="$2" default="${3:-}" input
  local current="${!varname}"
  if [ -n "$current" ]; then
    info "${message}: usando valor já definido (${varname})"
    return
  fi
  if [ -n "$default" ]; then
    read -r -p "$(echo -e "${BLUE}?${NC} ${message} [${default}]: ")" input
    input="${input:-$default}"
  else
    read -r -p "$(echo -e "${BLUE}?${NC} ${message}: ")" input
    while [ -z "$input" ]; do
      read -r -p "  (obrigatório) ${message}: " input
    done
  fi
  export "$varname"="$input"
}

# ask_secret VAR "pergunta" — Enter gera valor aleatório automaticamente
ask_secret() {
  local varname="$1" message="$2" length="${3:-32}" input
  local current="${!varname}"
  if [ -n "$current" ]; then
    info "${message}: usando segredo já definido (${varname})"
    return
  fi
  read -r -s -p "$(echo -e "${BLUE}?${NC} ${message} (Enter para gerar automaticamente): ")" input
  echo ""
  if [ -z "$input" ]; then
    input=$(openssl rand -hex "$length")
    info "Valor gerado automaticamente para ${varname}."
  fi
  export "$varname"="$input"
}

# ask_secret_required VAR "pergunta" — segredo EXISTENTE: não gera automaticamente,
# obriga o usuário a informar (usado ao reutilizar infra que já tem senha).
ask_secret_required() {
  local varname="$1" message="$2" input
  local current="${!varname}"
  if [ -n "$current" ]; then
    info "${message}: usando segredo já definido (${varname})"
    return
  fi
  read -r -s -p "$(echo -e "${BLUE}?${NC} ${message}: ")" input
  echo ""
  while [ -z "$input" ]; do
    read -r -s -p "  (obrigatório) ${message}: " input
    echo ""
  done
  export "$varname"="$input"
}

# ask_optional VAR "pergunta" — Enter pula (deixa vazio, configurável depois na UI)
ask_optional() {
  local varname="$1" message="$2" input
  local current="${!varname}"
  if [ -n "$current" ]; then
    info "${message}: usando valor já definido (${varname})"
    return
  fi
  read -r -p "$(echo -e "${BLUE}?${NC} ${message} (Enter para pular): ")" input
  export "$varname"="$input"
}

ask_optional_secret() {
  local varname="$1" message="$2" input
  [ -n "${!varname}" ] && return 0
  read -r -s -p "${message} (Enter para configurar depois): " input
  echo ""
  export "$varname"="$input"
}

ask_domain() {
  local varname="$1" message="$2" default="${3:-}" domain
  while :; do
    ask "$varname" "$message" "$default"
    domain="${!varname}"
    if python3 -c 'import re,sys; s=sys.argv[1]; sys.exit(0 if len(s)<=253 and "." in s and all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?",p) for p in s.split(".")) else 1)' "$domain"; then
      return 0
    fi
    warn "Informe somente um domínio válido, sem https://, caminho ou porta."
    unset "$varname"
  done
}

ask_yes_no() {
  local input
  read -r -p "$1 [s/N]: " input
  [[ "$input" =~ ^([sS]|[yY])$ ]]
}

echo ""
echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  KLEAD-CLIENT — Instalação${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# FASE 0 — Detectar infraestrutura existente na VPS (nunca reinstalar por cima)
# ─────────────────────────────────────────────────────────────────────────────

log "Detectando infraestrutura existente..."

SKIP_TRAEFIK=false
SKIP_PORTAINER=false
SKIP_POSTGRES=false
SKIP_EVO=false
DETECTED_NET=""

# Detecta serviço por nome OU imagem (regex) — evita falsos positivos do grep
# na saída inteira do `docker service ls` (ex.: Evolution API v1 casando com
# "evolution", ou um serviço qualquer cuja imagem contenha a palavra).
_svc_matches() {
  docker service ls --format '{{.Name}} {{.Image}}' 2>/dev/null | grep -qiE "$1"
}

_detect_traefik_network() {
  local svc net_id net_name
  svc=$(docker service ls --format '{{.Name}} {{.Image}}' 2>/dev/null | awk 'tolower($0) ~ /traefik/ {print $1; exit}')
  [ -z "$svc" ] && return 0
  for net_id in $(docker service inspect "$svc" --format '{{range .Spec.Networks}}{{.Target}} {{end}}' 2>/dev/null); do
    net_name=$(docker network inspect "$net_id" --format '{{.Name}}' 2>/dev/null || echo "")
    [ -z "$net_name" ] && continue
    [ "$net_name" = "ingress" ] && continue
    echo "$net_name"
    return 0
  done
  return 0
}

if _svc_matches 'traefik'; then
  SKIP_TRAEFIK=true
  DETECTED_NET=$(_detect_traefik_network)
  info "Traefik detectado — será reutilizado ✓"
  [ -n "$DETECTED_NET" ] && info "Rede do Traefik detectada: ${DETECTED_NET}"
else
  info "Traefik não encontrado — será instalado"
fi

if _svc_matches 'portainer'; then
  SKIP_PORTAINER=true
  info "Portainer detectado — será reutilizado ✓"
else
  info "Portainer não encontrado — será instalado"
fi

if _svc_matches 'postgres'; then
  SKIP_POSTGRES=true
  info "PostgreSQL detectado — será reutilizado ✓"
else
  info "PostgreSQL não encontrado — será instalado"
fi

# "evolution-go"/"evolution_go" especificamente — Evolution API v1 é outro produto
if _svc_matches 'evolution[-_]go'; then
  SKIP_EVO=true
  info "Evolution GO detectado — será reutilizado ✓"
else
  info "Evolution GO não encontrado — será instalado"
fi

MULTI_NET_OPTIONS=""
if [ -z "$DETECTED_NET" ]; then
  OVERLAY_NETS=$(docker network ls --filter driver=overlay --format '{{.Name}}' 2>/dev/null | grep -v "^ingress$" || true)
  NET_COUNT=$(echo "$OVERLAY_NETS" | grep -c '\S' || true)
  if [ "$NET_COUNT" -eq 1 ]; then
    DETECTED_NET=$(echo "$OVERLAY_NETS" | tr -d '[:space:]')
    info "Rede overlay existente detectada: ${DETECTED_NET}"
  elif [ "$NET_COUNT" -gt 1 ]; then
    warn "Múltiplas redes overlay encontradas:"
    echo "$OVERLAY_NETS" | while read -r n; do echo "    - $n"; done
    MULTI_NET_OPTIONS="$OVERLAY_NETS"
  fi
fi

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# FASE 1 — OS e arquitetura
# ─────────────────────────────────────────────────────────────────────────────

OS_ID=$(grep "^ID=" /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')
OS_VER=$(grep "^VERSION_ID=" /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')
ARCH=$(uname -m)

log "Sistema: ${OS_ID} ${OS_VER} (${ARCH})"

case "$ARCH" in
  x86_64|aarch64|arm64) ;;
  *) die "Arquitetura não suportada: $ARCH" ;;
esac
info "Evolution GO será compilado na própria VPS para ${ARCH}."

# Firewall restritivo (Oracle Cloud vem com REJECT por padrão) — vale para
# qualquer Ubuntu/Debian, não só 24.04.
if iptables -L INPUT -n 2>/dev/null | grep -q "REJECT\|DROP"; then
  log "Firewall restritivo detectado — permitindo HTTP/HTTPS sem alterar o acesso SSH..."
  iptables -I INPUT -p tcp --dport 80  -j ACCEPT 2>/dev/null || true
  iptables -I INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
  if command -v netfilter-persistent &>/dev/null; then
    netfilter-persistent save 2>/dev/null || true
  elif command -v iptables-save &>/dev/null; then
    mkdir -p /etc/iptables
    iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    if [ ! -f /etc/network/if-pre-up.d/iptables ]; then
      printf '#!/bin/sh\niptables-restore < /etc/iptables/rules.v4\n' > /etc/network/if-pre-up.d/iptables
      chmod +x /etc/network/if-pre-up.d/iptables
    fi
  fi
  warn "IMPORTANTE: Se estiver na Oracle Cloud, abra também as portas 80 e 443 nas Security Lists."
fi

# ─────────────────────────────────────────────────────────────────────────────
# FASE 2 — Docker + Swarm (sempre necessário, idempotente)
# ─────────────────────────────────────────────────────────────────────────────

echo ""
log "Verificando dependências básicas..."
if ! command -v curl &>/dev/null || ! command -v openssl &>/dev/null; then
  apt-get update -q 2>/dev/null || true
  command -v curl    &>/dev/null || apt-get install -y -q curl
  command -v openssl &>/dev/null || apt-get install -y -q openssl
fi

log "Verificando Docker / Swarm..."

if ! command -v docker &>/dev/null; then
  log "Instalando Docker..."
  docker_installer=$(mktemp)
  curl -fsSL https://get.docker.com -o "$docker_installer"
  sh "$docker_installer"
  rm -f -- "$docker_installer"
  systemctl enable docker
  systemctl start docker
else
  log "Docker já instalado ✓"
fi

if ! docker info 2>/dev/null | grep -q "Swarm: active"; then
  log "Inicializando Docker Swarm..."
  docker swarm init
else
  log "Docker Swarm já ativo ✓"
fi
docker info --format '{{.Swarm.ControlAvailable}}' | grep -qx true || die "Execute o instalador em um manager do Swarm."

# ─────────────────────────────────────────────────────────────────────────────
# FASE 3 — Wizard: perguntar só o que for novo
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}── Configuração ──────────────────────────────────────${NC}"
echo ""

# Rede overlay
if [ -n "$DETECTED_NET" ]; then
  TRAEFIK_NETWORK="$DETECTED_NET"
  info "Rede overlay: usando detectada automaticamente (${TRAEFIK_NETWORK})"
elif [ -n "$MULTI_NET_OPTIONS" ]; then
  FIRST_NET=$(echo "$MULTI_NET_OPTIONS" | head -1 | tr -d '[:space:]')
  # Valida contra a lista detectada — um typo aqui criaria uma rede nova
  # silenciosamente na fase de rede, isolando o KLEAD do Traefik existente.
  while :; do
    ask TRAEFIK_NETWORK "Qual rede overlay usar (dentre as detectadas acima)" "${TRAEFIK_NETWORK:-$FIRST_NET}"
    if echo "$MULTI_NET_OPTIONS" | grep -qx "$TRAEFIK_NETWORK"; then
      break
    fi
    warn "Rede '${TRAEFIK_NETWORK}' não está entre as detectadas — escolha uma da lista."
    unset TRAEFIK_NETWORK
  done
else
  ask TRAEFIK_NETWORK "Nome da rede overlay (Docker Swarm)" "${TRAEFIK_NETWORK:-klead}"
fi
export TRAEFIK_NETWORK

# Traefik + Portainer (só pergunta o que ainda não existir)
if [ "$SKIP_TRAEFIK" = false ]; then
  echo ""
  info "Traefik será instalado — dados do proxy/SSL:"
  ask ACME_EMAIL "Email para certificado SSL (Let's Encrypt)"
fi

if [ "$SKIP_PORTAINER" = false ]; then
  echo ""
  info "Portainer será instalado — dados de acesso:"
  ask_domain DOMAIN_PORTAINER "Domínio do Portainer (ex: portainer.seudominio.com)"
  ask_secret PORTAINER_ADMIN_PASSWORD "Senha do usuário 'admin' do Portainer" 12
fi

# PostgreSQL — sem prompt de domínio (uso interno). A senha é SEMPRE necessária:
# o backend do KLEAD persiste tudo no Postgres; sem ela, sobe em modo dev/teste
# (vazio, nada persiste) silenciosamente.
if [ "$SKIP_POSTGRES" = false ]; then
  ask_secret EVO_PG_PASSWORD "Senha do PostgreSQL (uso interno)" 16
else
  ask EVO_PG_USER "Usuário do PostgreSQL EXISTENTE" "${EVO_PG_USER:-postgres}"
  ask_secret_required EVO_PG_PASSWORD "Senha do PostgreSQL EXISTENTE (o KLEAD precisa dela para persistir dados)"
fi
EVO_PG_USER="${EVO_PG_USER:-postgres}"
export EVO_PG_USER EVO_PG_PASSWORD
if [ "$SKIP_POSTGRES" = true ]; then
  ask EVO_PG_HOST "Host/alias do PostgreSQL na rede overlay" "postgres"
  ask EVO_PG_PORT "Porta do PostgreSQL" "5432"
else
  EVO_PG_HOST=postgres
  EVO_PG_PORT=5432
fi
export EVO_PG_HOST EVO_PG_PORT

# Evolution GO
if [ "$SKIP_EVO" = false ]; then
  echo ""
  info "Evolution GO será instalado — dados do WhatsApp:"
  ask_domain DOMAIN_EVO "Domínio do Evolution GO (ex: go.seudominio.com)"
  ask_secret EVO_GLOBAL_API_KEY "Chave de API do Evolution GO" 16
else
  ask_secret_required EVO_GLOBAL_API_KEY "Global API Key do Evolution GO EXISTENTE"
fi
if [ "$SKIP_EVO" = true ]; then
  ask_domain DOMAIN_EVO "Domínio do Evolution GO EXISTENTE"
fi
EVO_API_URL="${EVO_API_URL:-https://${DOMAIN_EVO}}"
export EVO_API_URL

# KLEAD — sempre pergunta os domínios da aplicação (app nova)
echo ""
info "KLEAD — domínios da aplicação:"
ask_domain DOMAIN_API "Domínio da API/backend do KLEAD (ex: api.seudominio.com)"
ask_domain DOMAIN_APP "Domínio do frontend/dashboard (ex: app.seudominio.com)"
# DOMAIN_LOGIN/DOMAIN_PLANS são opcionais mas NÃO podem ficar vazios: o
# docker-stack.yml os usa na regra do Traefik (Host(``) vazio invalida a regra
# inteira e derruba a rota do dashboard). Default: o próprio DOMAIN_APP.
ask_domain DOMAIN_LOGIN "Domínio da página de login (Enter = dashboard)" "${DOMAIN_LOGIN:-$DOMAIN_APP}"
ask_domain DOMAIN_PLANS "Domínio da página de planos (Enter = dashboard)" "${DOMAIN_PLANS:-$DOMAIN_APP}"
export DOMAIN_LOGIN DOMAIN_PLANS
KLEAD_APP_URL="${KLEAD_APP_URL:-https://${DOMAIN_APP}}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-https://${DOMAIN_APP},https://${DOMAIN_LOGIN},https://${DOMAIN_PLANS}}"
export KLEAD_APP_URL ALLOWED_ORIGINS

echo ""
info "KLEAD — segredos internos:"
ask_secret DASHBOARD_SECRET "Chave mestra do sistema (DASHBOARD_SECRET)" 32
ask_secret WEBHOOK_SECRET "Proteção de webhooks (WEBHOOK_SECRET)" 32

echo ""
info "KLEAD — superadmin (primeiro acesso):"
ask SUPERADMIN_EMAIL "Email do superadmin"
ask_secret SUPERADMIN_PASSWORD "Senha do superadmin" 12
while ! python3 -c 'import os,sys; n=len(os.environ["SUPERADMIN_PASSWORD"].encode()); sys.exit(0 if 1<=n<=72 else 1)'; do
  warn "A senha do superadmin precisa ter no máximo 72 bytes (limite do bcrypt)."
  unset SUPERADMIN_PASSWORD
  ask_secret SUPERADMIN_PASSWORD "Senha do superadmin" 12
done

COOKIE_SECURE="${COOKIE_SECURE:-true}"
export COOKIE_SECURE

# Integrações opcionais: segredos solicitados com entrada oculta.
info "APIs de IA — Enter permite configurar depois no dashboard."
ask_optional_secret OPENAI_API_KEY "API Key OpenAI"
ask_optional_secret ANTHROPIC_API_KEY "API Key Anthropic"
ask_optional_secret GROQ_API_KEY "API Key Groq"
if [ -n "$OPENAI_API_KEY$ANTHROPIC_API_KEY$GROQ_API_KEY" ]; then
  AI_DEFAULT=groq
  [ -n "$OPENAI_API_KEY" ] && AI_DEFAULT=openai
  [ -z "$OPENAI_API_KEY" ] && [ -n "$ANTHROPIC_API_KEY" ] && AI_DEFAULT=anthropic
  while :; do
    ask KLEAD_LLM_PROVIDER "Provedor padrão: openai, anthropic ou groq" "$AI_DEFAULT"
    case "$KLEAD_LLM_PROVIDER" in openai|anthropic|groq) break ;; esac
    unset KLEAD_LLM_PROVIDER
  done
  ask_optional KLEAD_LLM_MODEL "Modelo de IA (vazio = padrão do app)"
fi
if ask_yes_no "Configurar email SMTP agora?"; then
  ask SMTP_HOST "Host SMTP"
  ask SMTP_PORT "Porta SMTP" "587"
  ask SMTP_USER "Usuário SMTP"
  ask_secret_required SMTP_PASSWORD "Senha SMTP"
fi
if ask_yes_no "Configurar App Meta/OAuth agora?"; then
  ask META_OAUTH_APP_ID "App ID Meta"
  ask_secret_required META_OAUTH_APP_SECRET "App Secret Meta"
  ask META_OAUTH_REDIRECT_URI "URL de callback Meta" "https://${DOMAIN_API}/meta/callback"
fi
if [ "$SKIP_TRAEFIK" = true ]; then
  ask TRAEFIK_ENTRYPOINT "Entrypoint HTTPS do Traefik EXISTENTE" "websecure"
  ask TRAEFIK_CERTRESOLVER "Resolver SSL do Traefik EXISTENTE" "letsencryptresolver"
else
  TRAEFIK_ENTRYPOINT=websecure
  TRAEFIK_CERTRESOLVER=letsencryptresolver
fi
export TRAEFIK_ENTRYPOINT TRAEFIK_CERTRESOLVER
EVO_PG_DB="${EVO_PG_DB:-evolution_go_users}"
INST_PG_DB="${INST_PG_DB:-klead}"
CRM_PG_DB="${CRM_PG_DB:-klead}"
WEBHOOK_BASE_URL="${WEBHOOK_BASE_URL:-http://klead_backend:8000}"
PUBLIC_WEBHOOK_BASE="https://${DOMAIN_API}"
EVO_MEDIA_BASE="${EVO_MEDIA_BASE:-http://klead_backend:8000}"
export EVO_PG_DB INST_PG_DB CRM_PG_DB WEBHOOK_BASE_URL PUBLIC_WEBHOOK_BASE EVO_MEDIA_BASE
EVO_PG_USER_URLENCODED=$(python3 -c 'import os,urllib.parse; print(urllib.parse.quote(os.environ["EVO_PG_USER"], safe=""))')
EVO_PG_PASSWORD_URLENCODED=$(python3 -c 'import os,urllib.parse; print(urllib.parse.quote(os.environ["EVO_PG_PASSWORD"], safe=""))')
export EVO_PG_USER_URLENCODED EVO_PG_PASSWORD_URLENCODED

# ─────────────────────────────────────────────────────────────────────────────
# FASE 4 — Gravar configuração privada (para rerun/depuração futura)
# ─────────────────────────────────────────────────────────────────────────────

log "Gravando configuração privada fora do repositório..."
if [ -f "$KLEAD_CONFIG_FILE" ]; then
  cp "$KLEAD_CONFIG_FILE" "${KLEAD_CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
fi
python3 scripts/install-env.py save "$KLEAD_CONFIG_FILE" \
  DOMAIN_API DOMAIN_APP DOMAIN_LOGIN DOMAIN_PLANS DOMAIN_EVO DOMAIN_PORTAINER \
  ACME_EMAIL TRAEFIK_NETWORK TRAEFIK_ENTRYPOINT TRAEFIK_CERTRESOLVER \
  DASHBOARD_SECRET WEBHOOK_SECRET EVO_PG_USER EVO_PG_PASSWORD EVO_PG_HOST EVO_PG_PORT \
  EVO_PG_DB INST_PG_DB CRM_PG_DB EVO_GLOBAL_API_KEY EVO_API_URL COOKIE_SECURE \
  EVO_PG_USER_URLENCODED EVO_PG_PASSWORD_URLENCODED \
  KLEAD_APP_URL ALLOWED_ORIGINS WEBHOOK_BASE_URL PUBLIC_WEBHOOK_BASE EVO_MEDIA_BASE \
  SUPERADMIN_EMAIL SUPERADMIN_PASSWORD PORTAINER_ADMIN_PASSWORD \
  OPENAI_API_KEY ANTHROPIC_API_KEY GROQ_API_KEY KLEAD_LLM_PROVIDER KLEAD_LLM_MODEL \
  SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD META_OAUTH_APP_ID META_OAUTH_APP_SECRET \
  META_OAUTH_REDIRECT_URI
log "Configuração salva em ${KLEAD_CONFIG_FILE} (permissão 600)."

# ─────────────────────────────────────────────────────────────────────────────
# FASE 5 — Rede overlay
# ─────────────────────────────────────────────────────────────────────────────

if ! docker network ls --format '{{.Name}}' | grep -q "^${TRAEFIK_NETWORK}$"; then
  log "Criando rede overlay '${TRAEFIK_NETWORK}'..."
  docker network create --driver=overlay --attachable "$TRAEFIK_NETWORK"
else
  log "Rede '${TRAEFIK_NETWORK}' já existe ✓"
fi

# ─────────────────────────────────────────────────────────────────────────────
# FASE 6 — Traefik
# ─────────────────────────────────────────────────────────────────────────────

if [ "$SKIP_TRAEFIK" = true ]; then
  log "Traefik já instalado — pulando ✓"
else
  log "Subindo Traefik (proxy + SSL)..."
  docker stack deploy -c stacks/traefik.yml traefik
  log "Aguardando Traefik estabilizar (10s)..."
  sleep 10
fi

# ─────────────────────────────────────────────────────────────────────────────
# FASE 7 — PostgreSQL
# ─────────────────────────────────────────────────────────────────────────────

if [ "$SKIP_POSTGRES" = true ]; then
  log "PostgreSQL já instalado — pulando ✓"
else
  log "Subindo PostgreSQL..."
  docker stack deploy -c stacks/postgres.yml postgres
fi

NET_ATTACHABLE=$(docker network inspect "$TRAEFIK_NETWORK" --format '{{.Attachable}}')
[ "$NET_ATTACHABLE" = true ] || die "A rede overlay precisa ser attachable para validar o banco e fazer backups. Habilite essa opção na rede existente e execute novamente."
_pg() {
  docker run --rm --network "$TRAEFIK_NETWORK" -e PGPASSWORD="$EVO_PG_PASSWORD" \
    postgres:14-alpine psql -h "$EVO_PG_HOST" -p "$EVO_PG_PORT" -U "$EVO_PG_USER" \
    -v ON_ERROR_STOP=1 "$@"
}
log "Aguardando o PostgreSQL configurado aceitar conexões..."
PG_OK=false
for _i in $(seq 1 30); do
  if _pg -d postgres -c 'SELECT 1;' >/dev/null 2>&1; then
    PG_OK=true
    break
  fi
  sleep 2
done
[ "$PG_OK" = true ] || die "PostgreSQL inacessível: confira host, porta, usuário, senha e rede overlay."
for database in "$INST_PG_DB" "$CRM_PG_DB" "$EVO_PG_DB" evolution_go_auth; do
  [[ "$database" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || die "Nome de banco inválido."
  exists=$(_pg -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$database';")
  if [ "$exists" != 1 ]; then
    _pg -d postgres -c "CREATE DATABASE \"$database\";" >/dev/null || die "Não foi possível criar o banco ${database}."
  fi
  _pg -d "$database" -c 'SELECT 1;' >/dev/null || die "Sem acesso ao banco ${database}."
done
log "Bancos criados e conexão validada pela rede usada pelo app."

# ─────────────────────────────────────────────────────────────────────────────
# FASE 8 — Evolution GO
# ─────────────────────────────────────────────────────────────────────────────

if [ "$SKIP_EVO" = true ]; then
  log "Evolution GO já instalado — pulando ✓"
else
  log "Compilando Evolution GO oficial na VPS (revisão fixada)..."
  bash scripts/build-evolution-go.sh
  log "Subindo Evolution GO..."
  docker stack deploy -c stacks/evolution-go.yml evolution_go
  log "Aguardando Evolution GO inicializar (15s)..."
  sleep 15
fi

# ─────────────────────────────────────────────────────────────────────────────
# FASE 9 — Build e deploy do KLEAD
# ─────────────────────────────────────────────────────────────────────────────

log "Compilando backend KLEAD (2-3 min)..."
docker build -t klead_backend:latest ./dashboard/backend

log "Compilando frontend KLEAD (3-5 min)..."
docker build -t klead_frontend:latest ./dashboard/frontend

log "Subindo KLEAD..."
docker stack deploy -c docker-stack.yml klead

# Backups privados fora do checkout; preserva instalações existentes do timer.
if [ ! -f /etc/systemd/system/klead-pg-backup.service ] && [ ! -f /etc/systemd/system/klead-pg-backup.timer ]; then
  log "Configurando backup diário dos bancos..."
  install -D -m 755 scripts/pg-backup.sh /usr/local/bin/klead-pg-backup.sh
  install -D -m 755 scripts/install-env.py /usr/local/lib/klead/install-env.py
  printf '[Unit]\nDescription=Backup PostgreSQL KLEAD\nAfter=docker.service\n[Service]\nType=oneshot\nEnvironment=KLEAD_CONFIG_FILE=%s\nExecStart=/usr/local/bin/klead-pg-backup.sh\nUMask=0077\n' "$KLEAD_CONFIG_FILE" > /etc/systemd/system/klead-pg-backup.service
  printf '[Unit]\nDescription=Backup diário KLEAD\n[Timer]\nOnCalendar=*-*-* 03:00:00\nPersistent=true\nRandomizedDelaySec=300\n[Install]\nWantedBy=timers.target\n' > /etc/systemd/system/klead-pg-backup.timer
  systemctl daemon-reload
  systemctl enable --now klead-pg-backup.timer
  /usr/local/bin/klead-pg-backup.sh
else
  info "Backup existente preservado; confira cobertura dos bancos desta instalação."
fi

# ─────────────────────────────────────────────────────────────────────────────
# FASE 10 — Portainer (gera hash de senha antes do deploy)
# ─────────────────────────────────────────────────────────────────────────────

if [ "$SKIP_PORTAINER" = true ]; then
  log "Portainer já instalado — pulando ✓"
elif [ -f stacks/portainer.yml ]; then
  log "Gerando credenciais do Portainer..."
  PORTAINER_SECRET_PATH="$(dirname "$KLEAD_CONFIG_FILE")/secrets/portainer_admin_password"
  mkdir -p "$(dirname "$PORTAINER_SECRET_PATH")"
  chmod 700 "$(dirname "$PORTAINER_SECRET_PATH")"
  docker run --rm httpd:2.4-alpine htpasswd -Bbn admin "$PORTAINER_ADMIN_PASSWORD" \
    | cut -d: -f2 > "$PORTAINER_SECRET_PATH"
  [ -s "$PORTAINER_SECRET_PATH" ] || die "Falha ao gerar senha do Portainer."
  chmod 600 "$PORTAINER_SECRET_PATH"
  export PORTAINER_SECRET_PATH
  python3 scripts/install-env.py save "$KLEAD_CONFIG_FILE" PORTAINER_SECRET_PATH

  log "Subindo Portainer..."
  docker stack deploy -c stacks/portainer.yml portainer
  sleep 10
fi

PUBLIC_OK=true
for url in "https://${DOMAIN_API}/health" "https://${DOMAIN_APP}/login" "${EVO_API_URL}/server/ok"; do
  log "Validando HTTPS: ${url}"
  if ! curl --fail --silent --show-error --retry 12 --retry-delay 5 --retry-all-errors \
       --connect-timeout 5 --max-time 15 "$url" >/dev/null; then
    PUBLIC_OK=false
    warn "Serviço não respondeu por HTTPS: confira DNS, firewall, Traefik e certificado."
  fi
done
if [ "$SKIP_PORTAINER" = false ]; then
  curl --fail --silent --show-error --retry 6 --retry-delay 5 --retry-all-errors \
    --connect-timeout 5 --max-time 15 "https://${DOMAIN_PORTAINER}/api/status" >/dev/null || PUBLIC_OK=false
fi

# ─────────────────────────────────────────────────────────────────────────────
# FASE 11 — Verificação final (não confiar no deploy: checar o /health de fato)
# ─────────────────────────────────────────────────────────────────────────────

log "Aguardando o backend do KLEAD ficar saudável (até ~2 min)..."
BACKEND_OK=false
for _i in $(seq 1 24); do
  BK=$(docker ps -q -f name=klead_backend 2>/dev/null | head -1)
  if [ -n "$BK" ] && docker exec "$BK" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" &>/dev/null; then
    BACKEND_OK=true
    break
  fi
  sleep 5
done

if [ "$BACKEND_OK" = true ]; then
  log "Backend respondendo no /health ✓"
else
  warn "Backend NÃO respondeu ao /health dentro do tempo esperado."
  warn "Diagnóstico:"
  warn "  docker service ps klead_backend --no-trunc"
  warn "  docker service logs klead_backend --tail 50"
  warn "Causa mais comum: banco/senha do Postgres — o backend aborta o boot de propósito se o Postgres estiver configurado mas inacessível."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Resumo
# ─────────────────────────────────────────────────────────────────────────────

echo ""
if [ "$BACKEND_OK" = true ] && [ "$PUBLIC_OK" = true ]; then
  echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
  echo -e "${GREEN}  KLEAD instalado com sucesso!${NC}"
  echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
else
  echo -e "${YELLOW}══════════════════════════════════════════════════════${NC}"
  echo -e "${YELLOW}  Instalação incompleta: um ou mais serviços não responderam${NC}"
  echo -e "${YELLOW}  — veja o diagnóstico acima antes de usar.${NC}"
  echo -e "${YELLOW}══════════════════════════════════════════════════════${NC}"
fi
echo ""
echo -e "  Rede usada:   ${BLUE}${TRAEFIK_NETWORK}${NC}"
echo -e "  Dashboard:    ${GREEN}https://${DOMAIN_APP}${NC}"
echo -e "  API/Webhooks: ${GREEN}https://${DOMAIN_API}${NC}"
[ -n "$DOMAIN_EVO" ]       && echo -e "  Evolution GO: ${GREEN}https://${DOMAIN_EVO}${NC}"
[ -n "$DOMAIN_PORTAINER" ] && echo -e "  Portainer:    ${GREEN}https://${DOMAIN_PORTAINER}${NC}"
echo ""
echo -e "  Login superadmin KLEAD: ${GREEN}${SUPERADMIN_EMAIL}${NC}"
[ "$SKIP_PORTAINER" = false ] && echo -e "  Login Portainer:        ${GREEN}admin${NC}"
echo ""
warn "As senhas estão em ${KLEAD_CONFIG_FILE} (permissão 600), fora do repositório."
echo ""
echo -e "  Infraestrutura reutilizada:"
[ "$SKIP_TRAEFIK" = true ]   && echo -e "    ${GREEN}✓${NC} Traefik"
[ "$SKIP_PORTAINER" = true ] && echo -e "    ${GREEN}✓${NC} Portainer"
[ "$SKIP_POSTGRES" = true ]  && echo -e "    ${GREEN}✓${NC} PostgreSQL"
[ "$SKIP_EVO" = true ]       && echo -e "    ${GREEN}✓${NC} Evolution GO"
echo ""
echo -e "${YELLOW}Próximos passos:${NC}"
echo "  1. Acesse o dashboard e faça login com o superadmin"
[ -n "$DOMAIN_EVO" ] && echo "  2. Se usar Evolution GO: escaneie o QR em https://${DOMAIN_EVO}"
echo "  3. Se usar Meta: configure os webhooks no Meta for Developers"
echo ""
info "Redeploy manual: veja INSTALL.md. O Swarm recebe a configuração privada em runtime."
[ "$BACKEND_OK" = true ] && [ "$PUBLIC_OK" = true ] || exit 1
