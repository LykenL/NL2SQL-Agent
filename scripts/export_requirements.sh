#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR/.."

if [ "$#" -ne 0 ]; then
    printf '%s\n' 'Usage: export_requirements.sh' >&2
    exit 2
fi

if ! uv lock --check; then
    printf '%s\n' 'ERROR: pyproject.toml and uv.lock disagree; run `uv lock` and retry.' >&2
    exit 1
fi

uv export --frozen --no-hashes --no-dev --no-emit-project --no-header -o requirements.lock.txt >/dev/null
uv export --frozen --no-hashes --no-dev --no-emit-project --no-header --extra pipelines -o requirements.pipelines.lock.txt >/dev/null

for file in requirements.lock.txt requirements.pipelines.lock.txt; do
    pin_count=$(awk '/^[[:alnum:]_.-]+==/ { count++ } END { print count + 0 }' "$file")
    printf '%s: %s pins\n' "$file" "$pin_count"
done

printf '%s\n' 'Git status:'
status=$(git status --short -- requirements.lock.txt requirements.pipelines.lock.txt)
if [ -n "$status" ]; then
    printf '%s\n' "$status"
else
    printf '%s\n' '(clean)'
fi
