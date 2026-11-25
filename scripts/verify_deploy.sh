#!/bin/sh
set -eu

BASE_URL=${1:-${BASE_URL:-https://nl2sql-agent-0hwl.onrender.com}}
DEPLOY_BRANCH=${DEPLOY_BRANCH:-v2-nextjs-fastapi}
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
    printf 'Summary: FAILED (%s).\n' "$failures"
    exit 1
fi
printf 'Summary: PASS (health, version, plotly, altair).\n'
