#!/usr/bin/env bash
# Build the compose stack, start it, and assert both services actually answer.
#
#   bash scripts/docker_smoke_test.sh              # build, start, check, tear down
#   KEEP=1 bash scripts/docker_smoke_test.sh       # leave the stack running
#   bash scripts/docker_smoke_test.sh --no-build   # use images built elsewhere (CI)
#
# Exit status is the check's status, so this doubles as the CI gate.
set -euo pipefail

cd "$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)"

build=1
for arg in "$@"; do
    case "$arg" in
        --no-build) build=0 ;;
        -h|--help)
            sed -n '2,10p' "$0"
            exit 0
            ;;
        *)
            printf 'unknown argument: %s\n' "$arg" >&2
            exit 2
            ;;
    esac
done

api_port=${API_PORT:-8000}
web_port=${WEB_PORT:-3000}

if ! command -v docker >/dev/null 2>&1; then
    printf 'docker is not installed or not on PATH\n' >&2
    exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
    printf 'docker compose (v2) is required\n' >&2
    exit 2
fi

if [ ! -f .env ]; then
    cp configs/.env.example .env
    printf 'created .env from configs/.env.example (placeholder keys: chat needs a real LLM_API_KEY)\n'
fi

cleanup() {
    if [ "${KEEP:-0}" = "1" ]; then
        printf '\nstack left running: docker compose down  (compose up logs -f to watch it)\n'
    else
        docker compose down --remove-orphans >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

if [ "$build" -eq 1 ]; then
    docker compose build
fi

# --wait blocks until both images' HEALTHCHECK reports healthy.
docker compose up -d --wait --wait-timeout 240 --no-build
docker compose ps

python3 scripts/docker_smoke_check.py \
    --api "http://127.0.0.1:${api_port}" \
    --web "http://127.0.0.1:${web_port}"
