#!/usr/bin/env bash
# Inventário da VPS antes (ou depois) de instalar o KLEAD-CLIENT.
#
# Uso, no terminal DA VPS, na pasta do projeto:
#   bash scripts/verificar-vps.sh                          # somente leitura
#   bash scripts/verificar-vps.sh app.example.com api.example.com go.example.com
#                                                          # também confere o DNS desses domínios
#   bash scripts/verificar-vps.sh --instalar [domínios]    # oferece instalar os pré-requisitos
#                                                          # do wizard e iniciar o wizard, com confirmação
#
# Sem --instalar o script não altera nada. Não exibe senhas nem o conteúdo de
# arquivos de configuração.

set -u

INSTALAR=0
DOMINIOS=()
for a in "$@"; do
  case "$a" in
    --instalar) INSTALAR=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) DOMINIOS+=("$a") ;;
  esac
done

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; C='\033[0;36m'; N='\033[0m'
ok()    { printf "  ${G}[OK]${N}    %s\n" "$1"; }
falta() { printf "  ${R}[FALTA]${N} %s\n" "$1"; FALTAS=$((FALTAS+1)); }
aviso() { printf "  ${Y}[AVISO]${N} %s\n" "$1"; AVISOS=$((AVISOS+1)); }
info()  { printf "  ${C}[INFO]${N}  %s\n" "$1"; }
titulo(){ printf "\n== %s ==\n" "$1"; }
tem()   { command -v "$1" >/dev/null 2>&1; }
FALTAS=0; AVISOS=0
PACOTES_FALTANDO=()

echo "KLEAD-CLIENT - verificação da VPS"
echo "Pasta do projeto: $RAIZ"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if tem sudo && sudo -n true 2>/dev/null; then SUDO="sudo"
  elif tem sudo && [ -t 0 ] && { echo "  O inventário do Docker e de /etc/klead usa sudo (somente leitura)."; sudo -v; }; then SUDO="sudo"
  elif tem sudo; then aviso "sudo indisponível sem senha neste terminal: inventário parcial (Docker, portas e /etc/klead podem ficar sem leitura)."
  else aviso "Sem root e sem sudo: o wizard precisa de um dos dois."; fi
fi

titulo "Sistema"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case "${ID:-}-${VERSION_ID:-}" in
    ubuntu-22.04|ubuntu-24.04|debian-12) ok "$PRETTY_NAME (aceito pelo wizard)" ;;
    *) falta "$PRETTY_NAME: o wizard aceita Ubuntu 22.04, Ubuntu 24.04 ou Debian 12" ;;
  esac
else falta "Não foi possível identificar o sistema (/etc/os-release)"; fi
ARQ=$(uname -m)
case "$ARQ" in x86_64|amd64) ok "Arquitetura $ARQ" ;; aarch64|arm64) ok "Arquitetura $ARQ (ARM64: build aceito, homologação pendente)" ;; *) falta "Arquitetura $ARQ não suportada" ;; esac
RAM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {printf "%d", $2/1024}' /proc/meminfo)
info "RAM: ${RAM_MB} MB, swap: ${SWAP_MB} MB"
if [ "$RAM_MB" -lt 3500 ] && [ "$SWAP_MB" -lt 2000 ]; then aviso "Pouca memória para os builds de Next.js e Go; considere 4 GB de RAM ou adicionar swap."; fi
LIVRE_GB=$(df -Pk / | awk 'NR==2 {printf "%d", $4/1024/1024}')
if [ "$LIVRE_GB" -lt 15 ]; then aviso "Disco / com ${LIVRE_GB} GB livres; imagens, builds e backups precisam de espaço (sugestão: 20 GB+)."; else ok "Disco /: ${LIVRE_GB} GB livres"; fi

titulo "Pré-requisitos do wizard"
for par in "python3:python3" "git:git" "curl:curl" "openssl:openssl"; do
  cmd="${par%%:*}"; pkg="${par##*:}"
  if tem "$cmd"; then ok "$cmd"; else falta "$cmd"; PACOTES_FALTANDO+=("$pkg"); fi
done
[ -d /etc/ssl/certs ] && [ -n "$(ls -A /etc/ssl/certs 2>/dev/null)" ] && ok "Certificados CA" || { falta "Certificados CA"; PACOTES_FALTANDO+=("ca-certificates"); }
for f in install.sh docker-stack.yml stacks/traefik.yml stacks/postgres.yml stacks/evolution-go.yml; do
  [ -f "$RAIZ/$f" ] && ok "$f presente" || falta "$f não encontrado (envie o pacote completo)"
done

titulo "Portas e firewall"
if tem ss; then
  for p in 80 443; do
    linha=$($SUDO ss -lntp 2>/dev/null | awk -v p=":$p\$" '$4 ~ p')
    if [ -n "$linha" ]; then
      proc=$(echo "$linha" | grep -oE 'users:\(\("[^"]+' | head -n1 | cut -d'"' -f2)
      info "Porta $p em uso por: ${proc:-desconhecido} (se não for o Traefik/Docker, há conflito com outro proxy)"
    else ok "Porta $p livre"; fi
  done
fi
if tem ufw; then
  st=$($SUDO ufw status 2>/dev/null | head -n1)
  info "ufw: ${st:-sem leitura}"
fi
info "Lembre-se do firewall do PROVEDOR (Oracle Security List/NSG, painel Contabo etc.): libere 80, 443 e a porta SSH."

titulo "Docker"
if tem docker; then
  ok "$(docker --version)"
  if $SUDO docker info >/dev/null 2>&1; then
    ok "Docker Engine em execução"
    SW=$($SUDO docker info --format '{{.Swarm.LocalNodeState}} {{.Swarm.ControlAvailable}}' 2>/dev/null)
    case "$SW" in
      "active true") ok "Swarm ativo e este nó é manager" ;;
      active*) aviso "Swarm ativo, mas este nó não é manager" ;;
      *) info "Swarm inativo (o wizard inicializa)" ;;
    esac
    if [ "${SW%% *}" = "active" ]; then
      info "Stacks:"; $SUDO docker stack ls 2>/dev/null | sed 's/^/          /'
      info "Serviços:"; $SUDO docker service ls --format '{{.Name}}  {{.Replicas}}  {{.Image}}' 2>/dev/null | sed 's/^/          /'
    fi
    info "Containers fora do Swarm:"; $SUDO docker ps -a --filter 'label!=com.docker.swarm.service.name' --format '{{.Names}}  {{.Status}}  {{.Image}}' 2>/dev/null | sed 's/^/          /'
    info "Redes overlay:"; $SUDO docker network ls --filter driver=overlay --format '{{.Name}}' 2>/dev/null | sed 's/^/          /'
    info "Volumes:"; $SUDO docker volume ls --format '{{.Name}}' 2>/dev/null | sed 's/^/          /'
  else
    falta "Docker instalado mas o daemon não responde (sudo systemctl status docker)"
  fi
else
  info "Docker ausente (o wizard instala Docker Engine e inicializa o Swarm)"
fi

titulo "Instalação KLEAD existente"
if $SUDO test -f /etc/klead/klead.env; then
  perm=$($SUDO stat -c '%a' /etc/klead/klead.env)
  ok "Configuração privada /etc/klead/klead.env existe (permissão $perm; conteúdo não exibido)"
  [ "$perm" = "600" ] || aviso "Permissão esperada 600 em /etc/klead/klead.env"
  info "Reexecutar o wizard preserva os valores; NÃO regenere DASHBOARD_SECRET."
else
  info "Nenhuma configuração em /etc/klead (instalação nova ou nome personalizado)"
fi
if tem systemctl; then
  if systemctl list-unit-files klead-pg-backup.timer >/dev/null 2>&1 && systemctl is-enabled klead-pg-backup.timer >/dev/null 2>&1; then
    ok "Timer de backup klead-pg-backup ativo"
  else info "Timer de backup ainda não instalado"; fi
fi
if tem curl; then
  for url in http://127.0.0.1:8000/health; do
    r=$(curl -s -m 5 "$url" 2>/dev/null)
    [ -n "$r" ] && info "$url -> $r"
  done
fi

if [ ${#DOMINIOS[@]} -gt 0 ]; then
  titulo "DNS dos domínios"
  info "IPs locais desta VPS: $(hostname -I 2>/dev/null)"
  info "Em provedores com NAT (ex.: Oracle) o IP público não aparece acima; compare com o painel do provedor."
  for d in "${DOMINIOS[@]}"; do
    v4=$(getent ahostsv4 "$d" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')
    v6=$(getent ahostsv6 "$d" 2>/dev/null | awk '{print $1}' | grep ':' | sort -u | tr '\n' ' ')
    if [ -z "$v4" ]; then falta "$d não resolve (crie o registro A)"; else ok "$d -> $v4"; fi
    [ -n "$v6" ] && aviso "$d também tem IPv6 ($v6): confira se o AAAA aponta para esta VPS ou remova-o"
  done
fi

titulo "Resumo"
if [ "$FALTAS" -eq 0 ]; then printf "  ${G}Nenhum bloqueio encontrado.${N}\n"; else printf "  ${R}%s item(ns) faltando ou incompatíveis (veja [FALTA] acima).${N}\n" "$FALTAS"; fi
[ "$AVISOS" -gt 0 ] && printf "  ${Y}Avisos: %s${N}\n" "$AVISOS"
[ ${#PACOTES_FALTANDO[@]} -gt 0 ] && echo "  Pacotes ausentes: ${PACOTES_FALTANDO[*]}"

[ "$INSTALAR" -eq 0 ] && { [ "$FALTAS" -eq 0 ]; exit $?; }

titulo "Instalação guiada"
if [ ${#PACOTES_FALTANDO[@]} -gt 0 ]; then
  read -r -p "  Instalar ${PACOTES_FALTANDO[*]} com apt-get? [s/N] " resp
  case "$resp" in
    s|S|sim|y|yes) $SUDO apt-get update -qq && $SUDO apt-get install -y "${PACOTES_FALTANDO[@]}" && ok "Pacotes instalados" || falta "Falha ao instalar pacotes" ;;
    *) info "Pacotes não instalados" ;;
  esac
fi
echo
echo "  O próximo passo é o wizard (sudo bash install.sh). Ele pode instalar Docker,"
echo "  inicializar o Swarm, alterar o firewall e criar Traefik, Postgres, Evolution GO,"
echo "  backend, frontend, Portainer e o backup diário. Não há modo de simulação."
read -r -p "  Iniciar o wizard agora? [s/N] " resp
case "$resp" in
  s|S|sim|y|yes) cd "$RAIZ" && exec $SUDO bash install.sh ;;
  *) info "Wizard não iniciado. Quando quiser: cd $RAIZ && sudo bash install.sh" ;;
esac
