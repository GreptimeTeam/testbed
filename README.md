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

### GreptimeDB Standalone (Local File Backend)

```bash
process-compose up standalone-fs
```

Single-node GreptimeDB using **local disk** instead of Garage S3. No garage/etcd dependency — fastest mode to start. Data lives under `.greptimedb/standalone-fs/`. Same connection details as standalone above.

### GreptimeDB Distributed Cluster

```bash
process-compose up haproxy
```

| Protocol | Address |
|---|---|
| HTTP | `http://127.0.0.1:11050` |
| gRPC | `127.0.0.1:11051` |
| MySQL | `127.0.0.1:11052` |
| PostgreSQL | `127.0.0.1:11053` |

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
