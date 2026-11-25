#!/bin/sh
set -eu

BASE_URL=${1:-${BASE_URL:-https://nl2sql-agent-0hwl.onrender.com}}
DEPLOY_BRANCH=${DEPLOY_BRANCH:-v2-nextjs-fastapi}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
while [ "${BASE_URL%/}" != "$BASE_URL" ]; do BASE_URL=${BASE_URL%/}; done

# Render's free instance may need time to wake after a deploy.
deadline=$(python3 -c 'import time; print(int(time.monotonic()) + 120)')
health_ready=0
last_health="no response"
while [ "$health_ready" -eq 0 ]; do
    if response=$(curl -sS --connect-timeout 8 --max-time 12 -w '\n%{http_code}' "$BASE_URL/api/health" 2>/dev/null); then
        curl_rc=0
    else
        curl_rc=$?
    fi
    http_code=$(printf '%s' "$response" | python3 -c 'import sys; s=sys.stdin.read(); print(s.rsplit("\n", 1)[-1] if "\n" in s else "000")')
    response_body=$(printf '%s' "$response" | python3 -c 'import sys; s=sys.stdin.read(); print(s.rsplit("\n", 1)[0] if "\n" in s else "")')
    if [ "$curl_rc" -eq 0 ]; then
        case $http_code in
            2??)
                if printf '%s' "$response_body" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") == "ok" else 1)' 2>/dev/null; then
                    health_ready=1
                else
                    printf 'ERROR: health endpoint returned HTTP %s but status was not ok or JSON was invalid.\n' "$http_code" >&2
                    printf 'Summary: FAILED (health response invalid)\n' >&2
                    exit 1
                fi
                ;;
            5??) last_health="HTTP $http_code" ;;
            *)
                printf 'ERROR: health request returned HTTP %s.\n' "$http_code" >&2
                printf 'Summary: FAILED (health request)\n' >&2
                exit 1
                ;;
        esac
    else
        last_health="connection failure (curl exit $curl_rc)"
    fi
    if [ "$health_ready" -eq 0 ]; then
        now=$(python3 -c 'import time; print(int(time.monotonic()))')
        if [ "$now" -ge "$deadline" ]; then
            printf 'ERROR: health did not become ready within about 120 seconds; last result: %s.\n' "$last_health" >&2
            printf 'Summary: FAILED (health unreachable)\n' >&2
            exit 1
        fi
        sleep 5
    fi
done

health_json=$response_body
printf '%s' "$health_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); t=d.get("tracing") or {}; caps=d.get("capabilities") or []; print("Health: status={}; capabilities={}; tracing.enabled={}; version={}".format(d.get("status", ""), ",".join(map(str, caps)) if isinstance(caps, list) else caps, t.get("enabled", ""), d.get("version", "")))'

deployed_version=$(printf '%s' "$health_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')
failures=""
if local_version=$(git rev-parse --short=12 HEAD 2>/dev/null); then
    local_branch=$(git branch --show-current 2>/dev/null || true)
    if [ "$deployed_version" = "$local_version" ]; then
        printf 'Version: %s MATCH (local HEAD; branch %s).\n' "$deployed_version" "${local_branch:-detached}"
    elif [ "$local_branch" = "$DEPLOY_BRANCH" ]; then
        printf 'Version: %s MISMATCH (local HEAD %s; deployed branch %s).\n' "$deployed_version" "$local_version" "$DEPLOY_BRANCH"
        failures="version mismatch"
    else
        printf 'Version: %s MISMATCH (local HEAD %s; local branch %s is not deployed branch %s; advisory).\n' "$deployed_version" "$local_version" "${local_branch:-detached/unknown}" "$DEPLOY_BRANCH"
    fi
else
    printf 'Version: %s (local git checkout unavailable; not compared).\n' "$deployed_version"
fi

# Probe interpreter and installed distributions in one execution request.
runtime_code='import importlib.metadata as md, os, re, sys
print("PYTHON_VERSION={}.{}.{}".format(*sys.version_info[:3]))
print("PYTHON_EXECUTABLE=" + sys.executable)
print("PREFIX=" + sys.prefix)
command = ""
try:
    with open(os.path.join(sys.prefix, "pyvenv.cfg"), encoding="utf-8") as cfg:
        command = next((line.split("=", 1)[1].strip() for line in cfg if line.lstrip().startswith("command =")), "")
except OSError:
    pass
creator = "uv" if "uv" in command.lower() else "stdlib" if "-m venv" in command else "unknown"
print("CREATOR=" + creator)
distributions = list(md.distributions())
print("INSTALLED=" + str(len(distributions)))
wanted = {"fastapi", "plotly", "altair", "pandas", "sqlalchemy", "pydantic", "openai", "langfuse", "google-cloud-bigquery", "uvicorn"}
versions = {re.sub(r"[-_.]+", "-", d.metadata.get("Name", "")).lower(): d.version for d in distributions}
for name in sorted(wanted):
    print("PKG {}=={}".format(name, versions.get(name, "MISSING")))'
runtime_payload=$(printf '%s' "$runtime_code" | python3 -c 'import json,sys; print(json.dumps({"code": sys.stdin.read()}))')
runtime_response=""
if runtime_response=$(curl -sS --connect-timeout 8 --max-time 90 -H 'Content-Type: application/json' --data-binary "$runtime_payload" -w '\n%{http_code}' "$BASE_URL/api/execute" 2>/dev/null); then
    runtime_curl_rc=0
else
    runtime_curl_rc=$?
fi
runtime_http=$(printf '%s' "$runtime_response" | python3 -c 'import sys; s=sys.stdin.read(); print(s.rsplit("\n", 1)[-1] if "\n" in s else "000")')
runtime_body=$(printf '%s' "$runtime_response" | python3 -c 'import sys; s=sys.stdin.read(); print(s.rsplit("\n", 1)[0] if "\n" in s else "")')
runtime_ok=0
if [ "$runtime_curl_rc" -eq 0 ] && [ "$runtime_http" -ge 200 ] && [ "$runtime_http" -lt 300 ]; then
    if runtime_stdout=$(printf '%s' "$runtime_body" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("stdout") or "", end="")' 2>/dev/null); then
        runtime_error=$(printf '%s' "$runtime_body" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("error") or "", end="")' 2>/dev/null)
        printf '%s\n' "$runtime_stdout"
        runtime_value() { printf '%s\n' "$runtime_stdout" | python3 -c 'import sys; k=sys.argv[1]+"="; print(next((x[len(k):] for x in sys.stdin.read().splitlines() if x.startswith(k)), ""))' "$1"; }
        runtime_python=$(runtime_value PYTHON_VERSION)
        runtime_creator=$(runtime_value CREATOR)
        installed_count=$(runtime_value INSTALLED)
        [ -n "$runtime_creator" ] || runtime_creator=unknown
        printf 'Venv creator: %s.\n' "$runtime_creator"
        case $runtime_python:$installed_count in *[!0-9.:]*) runtime_python="" ;; esac
        if [ -n "$runtime_python" ] && [ -n "$installed_count" ] && [ -z "$runtime_error" ]; then runtime_ok=1; fi
    fi
fi
if [ "$runtime_ok" -eq 0 ]; then
    printf 'Runtime probe: FAILED (curl exit %s; HTTP %s).\n' "$runtime_curl_rc" "$runtime_http"
    failures="${failures}${failures:+; }runtime probe"
else
    if declared_range=$(python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["requires-python"])' "$SCRIPT_DIR/pyproject.toml" 2>/dev/null); then
        if range_result=$(python3 -c 'import sys; from packaging.specifiers import SpecifierSet; print("IN" if SpecifierSet(sys.argv[2]).contains(sys.argv[1]) else "OUT")' "$runtime_python" "$declared_range" 2>/dev/null); then
            if [ "$range_result" = IN ]; then printf 'Python: %s (declared %s) OK.\n' "$runtime_python" "$declared_range"
            else printf 'Python: %s (declared %s) OUT OF RANGE.\n' "$runtime_python" "$declared_range"; failures="${failures}${failures:+; }Python range"; fi
        else printf 'Python range could not be checked (advisory; version parser unavailable).\n'; fi
    else printf 'Python range could not be checked (advisory; pyproject.toml or tomllib unavailable).\n'; fi
    if pinned_count=$(python3 -c 'import re,sys; print(sum(bool(re.match(r"^[A-Za-z0-9._-]+==", x)) for x in open(sys.argv[1], encoding="utf-8")))' "$SCRIPT_DIR/requirements.lock.txt" 2>/dev/null); then
        printf 'Pinned set: deployed %s / lock %s (ADVISORY; platform markers may differ).\n' "$installed_count" "$pinned_count"
    else printf 'Pinned set comparison unavailable (ADVISORY).\n'; fi
fi

run_chart() {
    chart_name=$1
    chart_code=$2
    payload=$(printf '%s' "$chart_code" | python3 -c 'import json,sys; print(json.dumps({"code": sys.stdin.read()}))')
    if response=$(curl -sS --connect-timeout 8 --max-time 90 -H 'Content-Type: application/json' --data-binary "$payload" -w '\n%{http_code}' "$BASE_URL/api/execute" 2>/dev/null); then
        curl_rc=0
    else
        curl_rc=$?
    fi
    http_code=$(printf '%s' "$response" | python3 -c 'import sys; s=sys.stdin.read(); print(s.rsplit("\n", 1)[-1] if "\n" in s else "000")')
    response_body=$(printf '%s' "$response" | python3 -c 'import sys; s=sys.stdin.read(); print(s.rsplit("\n", 1)[0] if "\n" in s else "")')
    if [ "$curl_rc" -ne 0 ]; then
        printf '%s: request failed (curl exit %s; HTTP %s).\n' "$chart_name" "$curl_rc" "$http_code"
        failures="${failures}${failures:+; }$chart_name request"
        return
    fi
    if [ "$http_code" -lt 200 ] || [ "$http_code" -ge 300 ]; then
        printf '%s: HTTP %s.\n' "$chart_name" "$http_code"
    fi
    if printf '%s' "$response_body" | python3 -c 'import json,sys; d=json.load(sys.stdin); e=d.get("error"); print("  error: " + str(e)) if e not in (None, "") else None; h=d.get("html"); n=len(h.encode("utf-8")) if isinstance(h, str) else 0; ok=d.get("has_chart") is True and n > 0; print("{}: has_chart={} html_bytes={}".format(sys.argv[1], str(d.get("has_chart") is True).lower(), n)); sys.exit(0 if ok and 200 <= int(sys.argv[2]) < 300 else 1)' "$chart_name" "$http_code"; then
        :
    else
        failures="${failures}${failures:+; }$chart_name chart"
    fi
}

plotly_code='import plotly.graph_objects as go; fig = go.Figure(go.Bar(x=["a","b"], y=[1,2], marker=dict(cornerradius=8))); fig.update_layout(title="t")'
altair_code='import altair as alt, pandas as pd; chart = alt.Chart(pd.DataFrame({"a":["x","y"],"b":[1,2]})).mark_bar().encode(x="a", y="b")'
run_chart plotly "$plotly_code"
run_chart altair "$altair_code"

if [ -n "$failures" ]; then
    printf 'Summary: FAILED (%s; checks: health, version, Python range, pinned set advisory, venv creator, plotly, altair).\n' "$failures"
    exit 1
fi
printf 'Summary: PASS (health, version, Python range, pinned set advisory, venv creator, plotly, altair).\n'
