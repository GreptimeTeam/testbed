# AGENTS.md

This is a Nix flake for running a local GreptimeDB distributed cluster for testing and development.

## Project Layout

```
flake.nix             Dev shell with all dependencies
process-compose.yml   Process orchestration (cluster startup order, health checks, dependencies)
haproxy.cfg           Proxy routing HTTP/gRPC/MySQL/PostgreSQL to the frontend
garage.toml           Garage S3-compatible storage config
scripts/garage-setup  One-shot init: creates bucket, API key, layout in garage
scripts/start-datanode Wrapper: generates S3 storage config and starts a datanode
scripts/start-standalone Wrapper: generates S3 storage config and starts standalone mode
scripts/garage-local  Standalone garage launcher (not used by process-compose)
.env                  Process-compose env vars (PC_PORT_NUM)
.greptimedb/          Runtime data (gitignored), created on first start
datasources/prometheus/ Prometheus + node-exporter (podman-compose, remote writes to greptimedb)
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

### GreptimeDB Distributed Cluster

```bash
process-compose up haproxy
```

Starts the full chain: etcd → garage → garage-setup → metasrv → datanode-{0,1} → frontend → haproxy.

Requires a `greptime` binary in the project root. All processes start in dependency order with health checks. Garage data is wiped on each start.

Process-compose server runs on port **11099** (set via `PC_PORT_NUM` in `.env`).

## Cluster Topology

```
etcd -> metasrv -> datanode-{0,1} -> frontend -> haproxy
      -> garage -> garage-setup -(setup complete)-> datanodes
```

### Default Processes (started via `process-compose up haproxy`)

- **etcd**: metadata backend for metasrv (port 11001)
- **garage**: S3 storage for datanodes (port 11010, bucket `test-bucket`)
- **garage-setup**: exits after creating bucket/key/layout, writes creds to `.greptimedb/s3.env`
- **metasrv**: cluster coordinator (port 11020 gRPC, 11021 HTTP)
- **datanode-{0,1}**: store data in garage via S3 protocol, each uses `scripts/start-datanode`
- **frontend**: query layer (ports 11040-11043 for HTTP/gRPC/MySQL/PostgreSQL)
- **haproxy**: unified entry point proxying to frontend

### Optional Processes (start manually)

- **metasrv-1**: second metasrv instance (port 11022 gRPC, 11023 HTTP)
- **frontend-1**: second frontend instance (ports 11044-11047 for HTTP/gRPC/MySQL/PostgreSQL)
- **flownode**: flow engine (port 11060 gRPC, 11061 HTTP)
- **standalone**: single-node GreptimeDB using garage for object storage (ports 11070-11073 for HTTP/gRPC/MySQL/PostgreSQL)

Start optional processes with:
```bash
process-compose process start <process-name>
```

## Connecting to GreptimeDB

Via haproxy (distributed cluster):

| Protocol | Address | Notes |
|---|---|---|
| HTTP API | `http://127.0.0.1:11050` | Dashboard and REST API |
| gRPC | `127.0.0.1:11051` | |
| MySQL | `127.0.0.1:11052` | User `root`, no password |
| PostgreSQL | `127.0.0.1:11053` | User `root`, no password |

Direct to frontend (bypass haproxy): ports 11040-11043.

Standalone (when started): ports 11070-11073.

## Port Allocation

| Service | Ports |
|---|---|
| process-compose | 11099 (server) |
| etcd | 11001 (client), 11002 (peer) |
| garage | 11010 (S3 API), 11011 (RPC), 11012 (web) |
| metasrv | 11020 (gRPC), 11021 (HTTP) |
| metasrv-1 | 11022 (gRPC), 11023 (HTTP) |
| datanode-0 | 11030 (gRPC), 11031 (HTTP) |
| datanode-1 | 11032 (gRPC), 11033 (HTTP) |
| frontend | 11040 (HTTP), 11041 (gRPC), 11042 (MySQL), 11043 (PostgreSQL) |
| frontend-1 | 11044 (HTTP), 11045 (gRPC), 11046 (MySQL), 11047 (PostgreSQL) |
| haproxy | 11050 (HTTP), 11051 (gRPC), 11052 (MySQL), 11053 (PostgreSQL) |
| flownode | 11060 (gRPC), 11061 (HTTP) |
| standalone | 11070 (HTTP), 11071 (gRPC), 11072 (MySQL), 11073 (PostgreSQL) |
| prometheus | 11080 (HTTP UI) |

## Useful Commands

```bash
process-compose process list                  # list processes
process-compose process get <process>         # check status
process-compose process logs <process>        # view logs
process-compose process restart <process>     # restart a process
process-compose process start <process>       # start an optional process
process-compose process stop <process>        # stop a process
process-compose down                          # stop everything
rm -rf .greptimedb                            # clean all data
```

## Common Tasks

- **Reset cluster**: `process-compose down && rm -rf .greptimedb && process-compose up haproxy`
- **Change greptime binary**: replace `./greptime` or set `GREPTIME_BIN` in `process-compose.yml` vars
- **Adjust ports**: edit `process-compose.yml` (process ports) and `haproxy.cfg` (proxy ports)
- **Run standalone only**: `process-compose up standalone` (auto-starts garage + garage-setup)
- **S3 credentials**: `source .greptimedb/s3.env` (sets `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL`, `AWS_REGION`)

## Prometheus + Node Exporter

A minimal Prometheus setup that scrapes node-exporter metrics and remote writes them into GreptimeDB via haproxy.

Prerequisite: GreptimeDB cluster must be running (`process-compose up haproxy`).

```bash
podman-compose -f datasources/prometheus/compose.yaml up -d
```

- **Prometheus UI**: `http://127.0.0.1:11080`
- **Remote write target**: `http://host.containers.internal:11050/v1/prometheus/write?db=public`
- Scrapes `node_exporter:9100` every 15s

Stop:
```bash
podman-compose -f datasources/prometheus/compose.yaml down
```

Verify metrics in GreptimeDB:
```sql
SHOW TABLES FROM public;
SELECT * FROM node_cpu_seconds_total LIMIT 5;
```
