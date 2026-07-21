#!/usr/bin/env bash
# CI smoke test for the testbed.
#
# Starts the single-node standalone-fs server (local File backend — no
# garage/postgres needed) and asserts that at least one SQL statement works
# (a write that round-trips through the client port).
#
# Run inside `nix develop` (which provides process-compose, psql and curl):
#
#   nix develop --command bash ci/smoke.sh
#
# Expects a `greptime` binary at the project root (GREPTIME_BIN default),
# e.g. downloaded from the latest GreptimeTeam/greptimedb release.
#
# NOTE: this script intentionally does NOT tear the server down on exit — the
# caller (CI workflow) fetches logs on failure and tears down in a later step.
set -euo pipefail

HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-120}"

echo "==> starting standalone-fs (single-node, local File backend)"
process-compose --tui=false up --detached standalone-fs

echo "==> waiting for client HTTP port 11040 to become healthy (up to ${HEALTH_WAIT_SECS}s)"
healthy=0
for i in $(seq 1 "$HEALTH_WAIT_SECS"); do
  if curl -sf http://127.0.0.1:11040/health >/dev/null; then
    echo "    GreptimeDB healthy after ${i}s"
    healthy=1
    break
  fi
  sleep 1
done
if [ "$healthy" != 1 ]; then
  echo "ERROR: GreptimeDB did not become healthy on port 11040" >&2
  exit 1
fi

echo "==> write a row via testbedctl (client PostgreSQL port 11043)"
./testbedctl psql -q -v ON_ERROR_STOP=1 \
  -c "CREATE TABLE IF NOT EXISTS ci_smoke (ts TIMESTAMP TIME INDEX, v INT);" \
  -c "INSERT INTO ci_smoke VALUES (now(), 42);"

echo "==> read it back"
val="$(./testbedctl psql -t -A -v ON_ERROR_STOP=1 \
  -c "SELECT v FROM ci_smoke ORDER BY ts DESC LIMIT 1;")"
echo "    read-back value: ${val:-<empty>}"
if [ "${val:-}" != "42" ]; then
  echo "ERROR: expected SQL to return 42, got '${val:-}'" >&2
  exit 1
fi

echo "==> SMOKE TEST PASSED"
