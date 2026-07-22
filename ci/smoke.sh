#!/usr/bin/env bash
# CI smoke test for the testbed.
#
# Starts ONE GreptimeDB deployment mode and asserts that SQL works (a write
# that round-trips through the client PostgreSQL port 11043):
#
#   standalone-fs  single-node GreptimeDB, local File backend (no deps)
#   distributed    full cluster: postgres -> metasrv -> datanode-{0,1} ->
#                  frontend -> haproxy (Garage S3 for data, Postgres for
#                  metadata + metasrv election)
#
# Both modes expose the SAME client ports (11040 HTTP / 11043 PostgreSQL), so
# the SQL checks are identical — only the process-compose target differs.
#
# Usage: ci/smoke.sh [standalone-fs|distributed]   (default: standalone-fs)
#
# Run inside `nix develop` (which provides process-compose, psql and curl):
#   nix develop --command bash ci/smoke.sh distributed
#
# Expects a `greptime` binary at the project root (GREPTIME_BIN default), e.g.
# downloaded from the latest GreptimeTeam/greptimedb release.
#
# NOTE: this script intentionally does NOT tear the server down on exit — the
# caller (CI workflow) fetches logs on failure and tears down in a later step.
set -euo pipefail

MODE="${1:-standalone-fs}"
HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-120}"

case "$MODE" in
  standalone-fs) TARGET="standalone-fs" ;;
  distributed)   TARGET="haproxy" ;;
  *)
    echo "ERROR: unknown mode '$MODE' (expected: standalone-fs | distributed)" >&2
    exit 2
    ;;
esac

echo "==> starting mode='$MODE' (process-compose target: $TARGET)"
process-compose --tui=false up --detached "$TARGET"

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

echo "==> SMOKE TEST PASSED ($MODE)"
