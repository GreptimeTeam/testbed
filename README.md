# GreptimeDB Development Environment

A Nix flake for running a local GreptimeDB cluster with process-compose.

## Quick Start

All processes are disabled by default. Start what you need:

```bash
nix develop
```

### Garage S3 Storage Only

```bash
process-compose up garage
```

### GreptimeDB Standalone

```bash
process-compose up standalone
```

| Protocol | Address |
|---|---|
| HTTP | `http://127.0.0.1:11040` |
| gRPC | `127.0.0.1:11041` |
| MySQL | `127.0.0.1:11042` |
| PostgreSQL | `127.0.0.1:11043` |

> These are the **client-facing ports**, shared by **standalone**, **standalone-fs**, and the distributed cluster's **haproxy** — they are never run at the same time, so the same client code works unchanged across every mode.

### GreptimeDB Standalone (Local File Backend)

```bash
process-compose up standalone-fs
```

Single-node GreptimeDB using **local disk** instead of Garage S3. No garage/etcd dependency — fastest mode to start. Data lives under `.greptimedb/standalone-fs/`. Same connection details as standalone above.

### Enterprise Active/Standby Standalone

```bash
process-compose up haproxy-standby
```

Two **enterprise** standalone instances form an active/standby pair sharing Garage S3 (main data store) and a **Postgres** table (shared metadata + leader election); each keeps its own dedicated WAL. Only the elected **leader** accepts writes; the **follower** rejects writes and serves read-refreshed queries. `haproxy-standby` routes the client ports to whichever node is currently leader.

- Requires an **enterprise** `greptime` binary provided in place as `./greptime` (or via `GREPTIME_BIN`) — the same path every other mode uses. The OSS binary cannot run this mode.
- Election backend is **Postgres** (the shared `postgres` process on port 11080) — the enterprise active/standby election is built on the external RDS metadata store.
- Clients use the same ports 11040-11043 as every other mode; traffic always reaches the active leader.

| Protocol | Address |
|---|---|
| HTTP | `http://127.0.0.1:11040` |
| gRPC | `127.0.0.1:11041` |
| MySQL | `127.0.0.1:11042` |
| PostgreSQL | `127.0.0.1:11043` |

Test failover with `process-compose process stop standby-a` (stop the leader); standby-b is elected and haproxy reroutes automatically. Inspect roles via `curl http://127.0.0.1:11070/status/standalone/role` (and `:11074`).

### GreptimeDB Distributed Cluster

```bash
process-compose up haproxy
```

Clients connect to **haproxy** on the same ports as standalone (**11040-11043**) — haproxy load-balances the internal frontend instance(s), which are not exposed directly. So client code written for standalone works here unchanged.

| Protocol | Address |
|---|---|
| HTTP | `http://127.0.0.1:11040` |
| gRPC | `127.0.0.1:11041` |
| MySQL | `127.0.0.1:11042` |
| PostgreSQL | `127.0.0.1:11043` |

Place a `greptime` binary in the project root before starting. Process-compose runs on port **11099**.

## testbedctl

A utility script for common operations against the running cluster:

```bash
./testbedctl psql                           # PostgreSQL CLI
./testbedctl mysql                          # MySQL CLI
./testbedctl s3 ls                          # List S3 buckets
./testbedctl s3 ls s3://test-bucket/        # List objects in bucket
./testbedctl s3 ls s3://test-bucket/ --recursive  # List all objects
./testbedctl telemetrygen                   # Ingest OTel traces
./testbedctl telemetrygen down              # Stop trace ingestion
./testbedctl telemetrygen metrics up        # Ingest continuous OTel metrics (gauge/sum/histogram)
./testbedctl telemetrygen metrics down      # Stop metrics ingestion
./testbedctl metrics-partition              # Partition greptime_physical_table into 4 ranges on 'timebox'
./testbedctl flush <table>                  # Flush a table's memtable (admin flush_table)
./testbedctl compact <table> [type] [opts]  # Trigger compaction (admin compact_table); optional twcs/swcs + parallelism=N
./testbedctl clean                          # Remove .greptimedb
```

## Start Extra Processes

```bash
process-compose process start metasrv-1
process-compose process start frontend-1
process-compose process start flownode
```

## Cleanup

```bash
process-compose down
./testbedctl clean
```
