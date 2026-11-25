#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR/.."

install_with_pip() {
    # The venv exists because the Start Command targets .venv/bin/uvicorn.
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.lock.txt
}

if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != "--pip" ]; }; then
    printf '%s\n' 'Usage: install_deps.sh [--pip]' >&2
    exit 2
fi

if [ "${1-}" = "--pip" ]; then
    printf '%s\n' 'Install path: pip fallback (pinned requirements.lock.txt; forced with --pip).'
    install_with_pip
elif command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'Install path: uv (locked base dependencies from uv.lock).'
    # Render ships Python and forbids uv downloads (UV_PYTHON_DOWNLOADS=never), so fetching its provided interpreter aborts the build.
    uv python find 3.12 >/dev/null 2>&1 || uv python install 3.12
    uv sync --locked
else
    printf '%s\n' 'Warning: uv not found; using pip fallback with pinned requirements.lock.txt.' >&2
    install_with_pip
fi
