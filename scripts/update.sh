#!/usr/bin/env bash
# Atualização na VPS, com rebuild e rollback explícito da imagem anterior.
set -euo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")/.."
[ "$EUID" -eq 0 ] || { echo 'Execute como root: sudo bash scripts/update.sh'; exit 1; }
KLEAD_CONFIG_FILE="${KLEAD_CONFIG_FILE:-/etc/klead/klead.env}"
[ -f "$KLEAD_CONFIG_FILE" ] || { echo 'Configuração privada não encontrada. Execute install.sh.'; exit 1; }
CONFIG_EXPORTS=$(python3 scripts/install-env.py export "$KLEAD_CONFIG_FILE")
eval "$CONFIG_EXPORTS"
unset CONFIG_EXPORTS
target="${1:-all}"
mode="${2:-}"
case "$target" in backend|frontend|all) ;; *) echo 'Uso: update.sh [backend|frontend|all] [--rollback|--config]'; exit 1 ;; esac
case "$mode" in ''|--rollback|--config) ;; *) echo 'Opção inválida.'; exit 1 ;; esac
if [ "$mode" = --config ]; then
    KLEAD_BACKEND_IMAGE=$(docker service inspect klead_backend --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}')
    KLEAD_FRONTEND_IMAGE=$(docker service inspect klead_frontend --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}')
    export KLEAD_BACKEND_IMAGE KLEAD_FRONTEND_IMAGE
    docker stack deploy -c docker-stack.yml klead
    exit 0
fi
state_dir="$(dirname "$KLEAD_CONFIG_FILE")/rollback"
mkdir -p "$state_dir"
for component in backend frontend; do
    [ "$target" = all ] || [ "$target" = "$component" ] || continue
    service="klead_${component}"
    marker="$state_dir/${component}.image"
    if [ "$mode" = --rollback ]; then
        [ -s "$marker" ] || { echo "Imagem anterior não registrada: $component"; exit 1; }
        image=$(cat "$marker")
        [[ "$image" =~ ^klead_(backend|frontend):[a-zA-Z0-9_.-]+$ ]] || { echo 'Registro de rollback inválido.'; exit 1; }
        docker image inspect "$image" >/dev/null
        docker service update --image "$image" "$service"
    else
        stamp=$(date +%Y%m%d-%H%M%S)
        old=$(docker service inspect "$service" --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}')
        previous="klead_${component}:pre-${stamp}"
        docker tag "$old" "$previous"
        image="klead_${component}:${stamp}"
        docker build -t "$image" "./dashboard/${component}"
        printf '%s\n' "$previous" > "$marker"
        docker service update --image "$image" "$service"
    fi
done
