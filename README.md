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
| HTTP | `http://127.0.0.1:11070` |
| gRPC | `127.0.0.1:11071` |
| MySQL | `127.0.0.1:11072` |
| PostgreSQL | `127.0.0.1:11073` |

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
./testbedctl prometheus up -d               # Start Prometheus + node-exporter
./testbedctl prometheus down                # Stop Prometheus
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
rm -rf .greptimedb
```
