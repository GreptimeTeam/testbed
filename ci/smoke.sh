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
    echo "    HTTP healthy after ${i}s"
    healthy=1
    break
  fi
  sleep 1
done
if [ "$healthy" != 1 ]; then
  echo "ERROR: HTTP port 11040 did not become healthy" >&2
  exit 1
fi

# HTTP liveness is only a first gate. In the distributed cluster, haproxy's
# per-backend TCP health checks (e.g. the PostgreSQL backend) and datanode
# registration can lag a few seconds behind the frontend HTTP server coming up,
# so an immediate SQL attempt can race and fail with a closed connection. Retry
# the write+read until the whole path is ready.
SQL_WAIT_SECS="${SQL_WAIT_SECS:-60}"
echo "==> write+read a row via testbedctl (client PostgreSQL port 11043, retrying up to ${SQL_WAIT_SECS}s)"
deadline=$(( $(date +%s) + SQL_WAIT_SECS ))
ok=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ./testbedctl psql -q -v ON_ERROR_STOP=1 \
      -c "CREATE TABLE IF NOT EXISTS ci_smoke (ts TIMESTAMP TIME INDEX, v INT);" \
      -c "INSERT INTO ci_smoke VALUES (now(), 42);" >/dev/null 2>&1; then
    val="$(./testbedctl psql -t -A -v ON_ERROR_STOP=1 \
      -c "SELECT v FROM ci_smoke ORDER BY ts DESC LIMIT 1;" 2>/dev/null || true)"
    if [ "${val:-}" = "42" ]; then
      ok=1
      break
    fi
  fi
  sleep 2
done
if [ "$ok" != 1 ]; then
  echo "ERROR: SQL write/read did not succeed within ${SQL_WAIT_SECS}s" >&2
  exit 1
fi

echo "    read-back value: 42"
echo "==> SMOKE TEST PASSED ($MODE)"
