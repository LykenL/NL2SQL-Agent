#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR/.."

use_pip=0
use_pipelines=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --pip) use_pip=1 ;;
        --pipelines) use_pipelines=1 ;;
        *)
            printf '%s\n' 'Usage: install_deps.sh [--pip] [--pipelines]' >&2
            exit 2
            ;;
    esac
    shift
done

install_with_pip() {
    # The venv exists because the Start Command targets .venv/bin/uvicorn.
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    if [ "$use_pipelines" -eq 1 ]; then
        .venv/bin/python -m pip install -r requirements.pipelines.lock.txt
    else
        .venv/bin/python -m pip install -r requirements.lock.txt
    fi
}

if [ "$use_pipelines" -eq 1 ]; then
    stack='base + pipelines'
else
    stack='base'
fi

if [ "$use_pip" -eq 1 ]; then
    if [ "$use_pipelines" -eq 1 ]; then
        printf '%s\n' 'Install path: pip fallback (base + pipelines pins from requirements.pipelines.lock.txt; forced with --pip).'
    else
        printf '%s\n' 'Install path: pip fallback (base pins from requirements.lock.txt; forced with --pip).'
    fi
    install_with_pip
elif command -v uv >/dev/null 2>&1; then
    printf 'Install path: uv (%s dependencies from uv.lock).\n' "$stack"
    # Render ships Python and forbids uv downloads (UV_PYTHON_DOWNLOADS=never), so fetching its provided interpreter aborts the build.
    uv python find 3.12 >/dev/null 2>&1 || uv python install 3.12
    if [ "$use_pipelines" -eq 1 ]; then
        uv sync --locked --extra pipelines
    else
        uv sync --locked
    fi
else
    printf 'Warning: uv not found; using pip fallback with pinned %s requirements.\n' "$stack" >&2
    if [ "$use_pipelines" -eq 1 ]; then
        printf '%s\n' 'Install path: pip fallback (base + pipelines pins from requirements.pipelines.lock.txt).'
    else
        printf '%s\n' 'Install path: pip fallback (base pins from requirements.lock.txt).'
    fi
    install_with_pip
fi
