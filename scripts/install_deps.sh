#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR/.."

use_pip=0
use_pipelines=0
use_dev=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --pip) use_pip=1 ;;
        --pipelines) use_pipelines=1 ;;
        --dev) use_dev=1 ;;
        *)
            printf '%s\n' 'Usage: install_deps.sh [--pip] [--pipelines] [--dev]' >&2
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
    if [ "$use_dev" -eq 1 ]; then
        printf '%s\n' 'Warning: --dev installs unpinned pytest via pip; no dev export exists because dev dependencies are only needed where uv exists.' >&2
        .venv/bin/python -m pip install pytest
    fi
}

stack='base'
if [ "$use_pipelines" -eq 1 ]; then
    stack="$stack + pipelines"
    requirements_lock=requirements.pipelines.lock.txt
else
    requirements_lock=requirements.lock.txt
fi
if [ "$use_dev" -eq 1 ]; then
    stack="$stack + dev"
fi

if [ "$use_pip" -eq 1 ] || ! command -v uv >/dev/null 2>&1; then
    pip_note=''
    if [ "$use_pip" -eq 1 ]; then
        pip_note='; forced with --pip'
    else
        printf 'Warning: uv not found; using pip fallback with pinned %s requirements.\n' "$stack" >&2
    fi
    dev_note=''
    if [ "$use_dev" -eq 1 ]; then
        dev_note='; pytest installed unpinned'
    fi
    printf 'Install path: pip fallback (%s; pinned export: %s%s%s).\n' "$stack" "$requirements_lock" "$dev_note" "$pip_note"
    install_with_pip
else
    printf 'Install path: uv (%s dependencies from uv.lock).\n' "$stack"
    # Render ships Python and forbids uv downloads (UV_PYTHON_DOWNLOADS=never), so fetching its provided interpreter aborts the build.
    uv python find 3.12 >/dev/null 2>&1 || uv python install 3.12
    set -- --locked
    if [ "$use_pipelines" -eq 1 ]; then
        set -- "$@" --extra pipelines
    fi
    if [ "$use_dev" -eq 1 ]; then
        set -- "$@" --extra dev
    fi
    uv sync "$@"
fi
