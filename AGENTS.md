# AGENTS.md

This is a Nix flake for running a local GreptimeDB distributed cluster for testing and development.

## Project Layout

```
flake.nix             Dev shell with all dependencies
process-compose.yml   Process orchestration (cluster startup order, health checks, dependencies)
config/               GreptimeDB component TOML templates (use __PLACEHOLDER__ vars)
  metasrv.toml          Metasrv template (grpc, http, logging)
  frontend.toml         Frontend template (http, grpc, mysql, postgres, meta_client)
  datanode.toml         Datanode template (S3 storage, WAL, region engines)
  standalone.toml       Standalone template (all-in-one with S3 storage)
  standalone-fs.toml    Standalone template (all-in-one with local File storage)
  standalone-standby.toml  Enterprise active/standby standalone template (S3 + Postgres metadata/election)
  flownode.toml         Flownode template (flow engine, grpc, http)
haproxy.cfg           Proxy routing HTTP/gRPC/MySQL/PostgreSQL to the frontend
haproxy-standby.cfg   Proxy routing client ports to the active/standby LEADER only
garage.toml           Garage S3-compatible storage config
scripts/
  garage-setup          One-shot init: creates bucket, API key, layout in garage
  start-metasrv         Wrapper: generates metasrv config from template, starts metasrv
  start-frontend        Wrapper: generates frontend config from template, starts frontend
  start-datanode        Wrapper: generates datanode config from template with S3 creds, starts datanode
  start-flownode        Wrapper: generates flownode config from template, starts flownode
  start-standalone      Wrapper: generates standalone config from template with S3 creds
  start-standalone-fs   Wrapper: generates standalone-fs config from template (local File backend, no S3)
  start-standalone-standby  Wrapper: generates per-node active/standby config from template, starts one node
  start-postgres        Wrapper: idempotent initdb + foreground Postgres (metadata + election backend)
  garage-local          Standalone garage launcher (not used by process-compose)
.env                  Process-compose env vars (PC_PORT_NUM)
.greptimedb/          Runtime data (gitignored), created on first start
```

## Starting the Cluster

```bash
nix develop
```

All processes are disabled by default. Dependencies are automatically resolved.

### Garage S3 Storage Only

```bash
process-compose up garage
```

### GreptimeDB Standalone

```bash
process-compose up standalone
```

Starts garage → garage-setup → standalone.

### GreptimeDB Standalone (Local File Backend)

```bash
process-compose up standalone-fs
```

Single-node GreptimeDB using **local disk** instead of Garage S3. No dependencies — starts immediately without garage/postgres. Data lives under `.greptimedb/standalone-fs/`. Reuses ports 11040-11043. Fastest way to iterate on GreptimeDB itself.

### Enterprise Active/Standby Standalone

```bash
process-compose up haproxy-standby
```

Two enterprise standalone instances form an **active/standby** pair: they share **Garage S3 as the main data store** and a **Postgres** table for shared metadata + leader **election**, while each keeps its own **dedicated local WAL**. Only the elected **leader** accepts writes; the **follower** rejects writes with HTTP 503 and can serve read-refreshed queries. `haproxy-standby` routes client traffic (11040-11043) to whichever node is currently leader, using each node's `/status/standalone/is_leader` endpoint (200 = leader, 503 = follower).

Starts the chain: garage → garage-setup → postgres → standby-a → standby-b → haproxy-standby.

**Requires an ENTERPRISE `greptime` binary** — provide it in place as `./greptime` (or set `GREPTIME_BIN`), the same path the other modes use. The OSS `./greptime` cannot run this mode (it does not load the `[enterprise_standalone]` options). The election backend is **Postgres** (the shared `postgres` process on port 11080) — the enterprise active/standby election is built on the external RDS metadata store.

Failover: stop the leader (`process-compose process stop standby-a`) and the follower is elected leader automatically; `haproxy-standby` then routes to it with no client-side change. Restarting a node with accumulated data replays its local WAL and re-opens shared-S3 regions, so the standby config sets `init_regions_in_background = true` — the HTTP server binds within ~1–2s (regions open in the background) and the node comes straight back up as a follower instead of being restart-killed by the readiness probe. Inspect roles directly:

```bash
curl -s http://127.0.0.1:11070/status/standalone/role   # standby-a: {"role":"leader"|"follower", "is_leader":true|false}
curl -s http://127.0.0.1:11074/status/standalone/role   # standby-b
```

### GreptimeDB Distributed Cluster

```bash
process-compose up haproxy
```

Starts the full chain: postgres → metasrv → datanode-{0,1} → frontend → haproxy, with garage → garage-setup running in parallel (datanodes depend on both metasrv and garage-setup). Postgres is the metasrv metadata + election backend (etcd is no longer used).

Requires a `greptime` binary in the project root. All processes start in dependency order with health checks. Garage data is wiped on each start.

Process-compose server runs on port **11099** (set via `PC_PORT_NUM` in `.env`).

## Cluster Topology

```
postgres -> metasrv -> datanode-{0,1} -> frontend -> haproxy
garage -> garage-setup -(setup complete)-> datanodes

# Enterprise active/standby (process-compose up haproxy-standby):
garage -> garage-setup -(setup complete)-> standby-a, standby-b
postgres -> standby-a, standby-b -(election via Postgres)-> haproxy-standby
```

### Default Processes (started via `process-compose up haproxy`)

- **postgres**: Postgres metadata + election backend for metasrv (port 11080), trust auth, data under `.greptimedb/postgres/`
- **garage**: S3 storage for datanodes (port 11010, bucket `test-bucket`)
- **garage-setup**: exits after creating bucket/key/layout, writes creds to `.greptimedb/s3.env`
- **metasrv**: cluster coordinator (port 11020 gRPC, 11021 HTTP)
- **datanode-{0,1}**: store data in garage via S3 protocol, each uses `scripts/start-datanode`
- **frontend**: query layer, internal (ports 11050-11053 for HTTP/gRPC/MySQL/PostgreSQL); fronted by haproxy, not exposed to clients
- **haproxy**: client-facing entry point (ports 11040-11043) load-balancing the frontend instance(s)

### Active/Standby Processes (started via `process-compose up haproxy-standby`)

- **garage**: shared S3 object storage for both standalones (same as above)
- **garage-setup**: writes creds to `.greptimedb/s3.env`
- **postgres**: Postgres metadata + election backend (port 11080), trust auth, data under `.greptimedb/postgres/`
- **standby-a**: enterprise standalone, internal (ports 11070-11073 for HTTP/gRPC/MySQL/PostgreSQL), dedicated WAL under `.greptimedb/standby-a/wal`
- **standby-b**: enterprise standalone, internal (ports 11074-11077), dedicated WAL under `.greptimedb/standby-b/wal`
- **haproxy-standby**: client-facing entry point (ports 11040-11043) routing ONLY to the elected leader

### Optional Processes (start manually)

- **metasrv-1**: second metasrv instance (port 11022 gRPC, 11023 HTTP)
- **frontend-1**: second frontend instance, internal (ports 11054-11057 for HTTP/gRPC/MySQL/PostgreSQL)
- **flownode**: flow engine (port 11060 gRPC, 11061 HTTP)
- **standalone**: single-node GreptimeDB using garage for object storage (binds the client-facing ports 11040-11043 for HTTP/gRPC/MySQL/PostgreSQL, since standalone and the distributed cluster are never run simultaneously)
- **standalone-fs**: single-node GreptimeDB using **local disk** (File backend) instead of Garage S3. Fastest/lightest mode: no object store, no garage dependency, data lives under `.greptimedb/standalone-fs/`. Reuses the same ports 11040-11043.

Start optional processes with:
```bash
process-compose process start <process-name>
```

## Connecting to GreptimeDB

Via haproxy (distributed cluster) — the **client-facing ports 11040-11043**, same as standalone/standalone-fs:

| Protocol | Address | Notes |
|---|---|---|
| HTTP API | `http://127.0.0.1:11040` | Dashboard and REST API |
| gRPC | `127.0.0.1:11041` | |
| MySQL | `127.0.0.1:11042` | User `root`, no password |
| PostgreSQL | `127.0.0.1:11043` | User `root`, no password |

Frontend instances are internal and not exposed to clients (frontend-0: 11050-11053, frontend-1: 11054-11057).

Standalone (when started): binds the same client-facing ports 11040-11043, so all client code (e.g. `scripts/read_iceberg.py`) works identically in either mode.

Active/standby (`haproxy-standby`): the client-facing ports 11040-11043 route to whichever of standby-a/standby-b is currently the elected leader. The same client code works unchanged. (During the startup election window, or while failing over, these ports return 503 until a new leader is published.)

## Port Allocation

| Service | Ports |
|---|---|
| process-compose | 11099 (server) |
| garage | 11010 (S3 API), 11011 (RPC), 11012 (web) |
| postgres (metasrv + active/standby) | 11080 (metadata + election backend) |
| metasrv | 11020 (gRPC), 11021 (HTTP) |
| metasrv-1 | 11022 (gRPC), 11023 (HTTP) |
| datanode-0 | 11030 (gRPC), 11031 (HTTP) |
| datanode-1 | 11032 (gRPC), 11033 (HTTP) |
| standby-a | 11070 (HTTP), 11071 (gRPC), 11072 (MySQL), 11073 (PostgreSQL) — internal |
| standby-b | 11074 (HTTP), 11075 (gRPC), 11076 (MySQL), 11077 (PostgreSQL) — internal |
| frontend | 11050 (HTTP), 11051 (gRPC), 11052 (MySQL), 11053 (PostgreSQL) — internal |
| frontend-1 | 11054 (HTTP), 11055 (gRPC), 11056 (MySQL), 11057 (PostgreSQL) — internal |
| haproxy | 11040 (HTTP), 11041 (gRPC), 11042 (MySQL), 11043 (PostgreSQL) — client-facing |
| haproxy-standby | 11040 (HTTP), 11041 (gRPC), 11042 (MySQL), 11043 (PostgreSQL) — client-facing, leader-only |
| flownode | 11060 (gRPC), 11061 (HTTP) |
| standalone | 11040 (HTTP), 11041 (gRPC), 11042 (MySQL), 11043 (PostgreSQL) — client-facing, shared with haproxy |

## Useful Commands

```bash
# Cluster management
process-compose process list                  # list processes
process-compose process get <process>         # check status
process-compose process logs <process>        # view logs
process-compose process restart <process>     # restart a process
process-compose process start <process>       # start an optional process
process-compose process stop <process>        # stop a process
process-compose down                          # stop everything
rm -rf .greptimedb                            # clean all data

# testbedctl - quick access to common operations
./testbedctl psql                              # PostgreSQL CLI to GreptimeDB
./testbedctl mysql                             # MySQL CLI to GreptimeDB
./testbedctl s3 ls                             # list S3 buckets
./testbedctl s3 ls s3://test-bucket/           # list objects
./testbedctl s3 ls s3://test-bucket/ --recursive
./testbedctl telemetrygen up                   # start trace ingestion
./testbedctl telemetrygen down                 # stop trace ingestion
./testbedctl telemetrygen metrics up           # start continuous metrics (gauge/sum/histogram)
./testbedctl telemetrygen metrics down         # stop metrics ingestion
./testbedctl metrics-partition                 # partition greptime_physical_table into 4 ranges on 'timebox'
./testbedctl flush <table>                      # flush a table's memtable (admin flush_table)
./testbedctl compact <table> [type] [opts]      # trigger compaction (admin compact_table); optional twcs/swcs + parallelism=N
./testbedctl clean                              # remove .greptimedb (full data reset)
```

## Common Tasks

- **Reset cluster**: `process-compose down && ./testbedctl clean && process-compose up haproxy`
- **Resume cluster**: `process-compose up haproxy` (preserves data if `.greptimedb` is not deleted; garage-setup will reuse existing credentials)
- **Change greptime binary**: replace `./greptime` or set `GREPTIME_BIN` in `process-compose.yml` vars
- **Active/standby binary**: provide an enterprise build as `./greptime` (or set `GREPTIME_BIN`); start with `process-compose up haproxy-standby`
- **Test failover**: `process-compose process stop standby-a` (leader) → standby-b is elected and `haproxy-standby` reroutes automatically. Inspect roles via `/status/standalone/role` on ports 11070/11074.
- **Adjust ports**: edit `process-compose.yml` (process ports) and `haproxy.cfg` / `haproxy-standby.cfg` (proxy ports)
- **Run standalone only**: `process-compose up standalone` (auto-starts garage + garage-setup)
- **testbedctl s3**: auto-sources `.greptimedb/s3.env` credentials before running `aws s3`

## OpenTelemetry Traces (telemetrygen)

Generates synthetic OpenTelemetry traces and ingests them into GreptimeDB via haproxy using the OTLP HTTP endpoint.

Prerequisite: GreptimeDB cluster must be running (`process-compose up haproxy`).

```bash
./testbedctl telemetrygen up
```

- **OTLP endpoint**: `http://host.containers.internal:11040/v1/otlp/v1/traces`
- Generates 25000 traces with 6 child spans each, at 10000 traces/sec
- Uses the `greptime_trace_v1` pipeline and writes to `opentelemetry_traces4` table

Stop:
```bash
./testbedctl telemetrygen down
```

Verify traces in GreptimeDB:
```sql
SHOW TABLES FROM public;
SELECT * FROM opentelemetry_traces4 LIMIT 5;
```

## OpenTelemetry Metrics (Python OTLP generator)

Generates a continuous stream of OpenTelemetry metrics and ingests them into GreptimeDB via the OTLP HTTP endpoint. A **single Python process** (`datasources/telemetrygen/gen_metrics.py`, using `opentelemetry-sdk`) emits **`TG_METRIC_COUNT` (default 50) distinct metrics**, each with a **randomized metric name** and a **randomized type** (**Gauge** / **Sum** / **Histogram**). This replaces the earlier one-telemetrygen-container-per-name setup. Names are reproducible for a given `TG_METRIC_SEED`; change the seed to reshuffle.

Prerequisite: GreptimeDB cluster must be running (`process-compose up haproxy`), and you must be inside `nix develop` (the dev shell provides `opentelemetry-sdk` / `opentelemetry-exporter-otlp-proto-http`).

```bash
./testbedctl telemetrygen metrics up
```

- **OTLP endpoint**: `http://127.0.0.1:11040/v1/otlp/v1/metrics` (client HTTP port: haproxy in distributed, standalone/standalone-fs in single-node)
- Runs indefinitely as a background process until `metrics down`; pidfile at `.greptimedb/tg-metrics.pid`, log at `.greptimedb/tg-metrics.log`
- To preview the metric names without starting: `python3 datasources/telemetrygen/gen_metrics.py --list`
- Tunables (env): `TG_METRIC_COUNT` (default `50`, number of distinct metric names), `TG_METRIC_SEED` (default `0`, RNG seed — change to reshuffle names), `TG_OTLP_ENDPOINT` (default `127.0.0.1:11040`), `TG_OTLP_URL_PATH` (default `/v1/otlp/v1/metrics`), `TG_METRICS_INTERVAL` (export interval, default `5s`), `TG_METRICS_RATE` (observations/sec per worker, default `10`), `TG_METRICS_WORKERS` (worker threads, default `2`)

All metrics share the single metric-engine physical table (`greptime_physical_table`); each metric name is exposed as a **logical table** under `public`. GreptimeDB derives the logical table name from the OTLP metric name plus a type-specific suffix:

| Metric type | Logical table(s) from metric name `<name>` |
|---|---|
| Gauge | `<name>` |
| Sum (counter) | `<name>_total` |
| Histogram | `<name>_bucket`, `<name>_count`, `<name>_sum` |

With ~50 randomized names you get dozens of logical tables (e.g. `cpu_usage_billing_api`, `http_requests_checkout_total`, `db_query_duration_api_bucket`). List them with `SHOW TABLES FROM public;`.

Stop:
```bash
./testbedctl telemetrygen metrics down
```

Verify metrics in GreptimeDB:
```sql
SHOW TABLES FROM public;                                                 -- ~50 logical metric tables
SELECT * FROM <metric_table> ORDER BY greptime_timestamp DESC LIMIT 5;    -- pick any name from SHOW TABLES
```

Every observation carries a semi-random `timebox` label (a bounded pool of many
distinct values whose leading digit spans 1..9). This column is the partition key
for the optional storage partitioning below — all four partitions receive data.

## Storage Partitioning (metric engine)

GreptimeDB's metric engine stores every metric in a single shared physical table
(`greptime_physical_table`), with each metric name exposed as a logical table.
On a multi-datanode cluster you can range-partition that physical table to
spread data across datanodes. See
https://docs.greptime.com/tutorials/k8s-metrics-monitor/#storage-partitioning.

`./testbedctl metrics-partition` recreates `greptime_physical_table` with **4
range partitions on the `timebox` column** (the semi-random label emitted by the
metrics generators):

```sql
CREATE TABLE greptime_physical_table (
  greptime_timestamp TIMESTAMP NOT NULL,
  greptime_value DOUBLE NULL,
  timebox STRING NULL,
  TIME INDEX (greptime_timestamp),
  PRIMARY KEY (timebox)
)
PARTITION ON COLUMNS (timebox) (
  timebox < '2',
  timebox >= '2' AND timebox < '5',
  timebox >= '5' AND timebox < '8',
  timebox >= '8'
)
ENGINE = metric WITH ('physical_metric_table' = 'true');
```

Ranges bucket `timebox` by leading digit; because `timebox` is a string, every
value starting with `'1'` sorts before `'2'`, so the buckets stay populated as
the counter grows. Distribution is approximate (leading digits are Benford-like,
so partition 1 holds more data), but all 4 partitions receive data.

### Workflow

Run on a **clean** database, before ingesting metrics:

```bash
process-compose down && ./testbedctl clean && process-compose up haproxy   # or: up standalone-fs
./testbedctl metrics-partition                                            # create the partitioned physical table
./testbedctl telemetrygen metrics up                                      # ingest (carries 'timebox')
```

If `greptime_physical_table` already backs live metrics, `metrics-partition`
refuses to drop it and tells you to clean first (the physical table cannot be
replaced while logical metric tables depend on it).

Verify:
```sql
SHOW CREATE TABLE greptime_physical_table;          -- PARTITION ON COLUMNS (timebox) ...
-- rows per partition for any one metric (pick a table from SHOW TABLES FROM public;
-- e.g. a gauge-type logical table):
SELECT CASE WHEN timebox < '2' THEN 'P1'
           WHEN timebox < '5' THEN 'P2'
           WHEN timebox < '8' THEN 'P3'
           ELSE 'P4' END AS partition, count(*)
FROM <metric_table> GROUP BY 1 ORDER BY 1;
```

### Caveats

- `greptime_physical_table` is **shared** by all metric sources. Once it is
  partitioned on `timebox`, only metrics that carry `timebox` route correctly,
  so do not ingest other metric sources (which lack `timebox`) on the same
  database after partitioning.
- `timebox` is only emitted by the telemetrygen metrics generators
  (`--unique-timeseries`). Trace ingestion is unaffected.
- On the distributed cluster the 4 partitions are distributed across the
  datanodes (2 datanodes → 2 partitions each).
