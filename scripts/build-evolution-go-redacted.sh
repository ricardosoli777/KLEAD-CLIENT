#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_REPO="https://github.com/evolution-foundation/evolution-go.git"
UPSTREAM_COMMIT="3a2dcc79d5a57c4f4c759420a7dfc85dec818e15"
IMAGE_TAG="${EVOLUTION_IMAGE_TAG:-klead-evolution-go:0.7.2-pr145-redacted-poll1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="${SCRIPT_DIR}/../patches/evolution-go-redact-webhook-url.patch"
POLL_PATCH_FILE="${SCRIPT_DIR}/../patches/evolution-go-poll-vote-jid.patch"
BUILD_DIR="$(mktemp -d)"

cleanup() {
  rm -rf -- "${BUILD_DIR}"
}
trap cleanup EXIT

git clone --filter=blob:none "${UPSTREAM_REPO}" "${BUILD_DIR}/evolution-go"
git -C "${BUILD_DIR}/evolution-go" fetch origin pull/145/head
git -C "${BUILD_DIR}/evolution-go" checkout --detach FETCH_HEAD

actual_commit="$(git -C "${BUILD_DIR}/evolution-go" rev-parse HEAD)"
if [[ "${actual_commit}" != "${UPSTREAM_COMMIT}" ]]; then
  echo "Unexpected upstream commit: ${actual_commit}" >&2
  exit 1
fi

git -C "${BUILD_DIR}/evolution-go" apply --check "${PATCH_FILE}"
git -C "${BUILD_DIR}/evolution-go" apply "${PATCH_FILE}"
git -C "${BUILD_DIR}/evolution-go" apply --check "${POLL_PATCH_FILE}"
git -C "${BUILD_DIR}/evolution-go" apply "${POLL_PATCH_FILE}"

docker build \
  --build-arg VERSION="0.7.2-pr145-redacted-poll1" \
  -t "${IMAGE_TAG}" \
  "${BUILD_DIR}/evolution-go"

echo "Built ${IMAGE_TAG} from ${UPSTREAM_COMMIT}"
