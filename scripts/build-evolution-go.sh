#!/usr/bin/env bash
set -euo pipefail

# Fonte oficial para novas instalações; revisão fixada, sem credenciais.
readonly REPOSITORY="https://github.com/evolution-foundation/evolution-go.git"
readonly EXPECTED_COMMIT="9337afc47e10b86cc896a6f432240e40fee95dd1"
readonly IMAGE="klead-evolution-go:9337afc47e10"

build_dir="$(mktemp -d -t klead-evolution-go.XXXXXX)"
cleanup() {
  if [[ -n "${build_dir:-}" && -d "$build_dir" ]]; then
    rm -rf -- "$build_dir"
  fi
}
trap cleanup EXIT

git -C "$build_dir" init -q
git -C "$build_dir" remote add origin "$REPOSITORY"
git -C "$build_dir" fetch --depth=1 origin "$EXPECTED_COMMIT"
git -C "$build_dir" checkout -q --detach FETCH_HEAD

actual_commit="$(git -C "$build_dir" rev-parse HEAD)"
if [[ "$actual_commit" != "$EXPECTED_COMMIT" ]]; then
  echo "Código diferente da versão fixada: esperado $EXPECTED_COMMIT, recebido $actual_commit" >&2
  exit 1
fi

# Defesa adicional ao Dockerfile do fork: nenhum arquivo de ambiente/chave
# entra no contexto, inclusive em camadas intermediárias do build.
cat >> "$build_dir/.dockerignore" <<'EOF'

.env
.env.*
**/.env
**/.env.*
*.env
*.pem
*.key
*.p12
*.pfx
id_rsa*
id_ed25519*
secrets/
EOF
docker build --build-arg VERSION=klead-9337afc47e10 -t "$IMAGE" "$build_dir"
echo "Imagem criada: $IMAGE ($EXPECTED_COMMIT)"
