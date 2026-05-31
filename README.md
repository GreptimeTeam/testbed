# GreptimeDB Development Environment

A Nix flake that provides a local GreptimeDB cluster with all dependencies, managed by process-compose.

## Prerequisites

- [Nix](https://nixos.org/download/) with flake support
- A `greptime` binary in the project root

## Quick Start

```bash
# Enter the dev shell
nix develop

# Start the full cluster
process-compose up

# Start with TUI disabled (detached mode)
process-compose up -t=false

# Stop everything
process-compose down
```

## Cluster Architecture

```
etcd ──► metasrv ──► datanode-{0,1,2} ──► frontend ──► haproxy
  └──► garage ──► garage-setup ─────────────┘
```

| Process | Description | Default Ports |
|---|---|---|
| etcd | Metadata store for metasrv | 2379 |
| garage | S3-compatible object storage | 3900 (API), 3901 (RPC) |
| garage-setup | One-shot: creates bucket, key, layout | — |
| metasrv | GreptimeDB meta server | 3002 (gRPC), 4000 (HTTP) |
| datanode-0 | Data node 0 | 4100 (RPC), 4400 (HTTP) |
| datanode-1 | Data node 1 | 4101 (RPC), 4401 (HTTP) |
| datanode-2 | Data node 2 | 4102 (RPC), 4402 (HTTP) |
| frontend | Query front-end | 5000 (HTTP), 5001 (gRPC), 5002 (MySQL), 5003 (PostgreSQL) |
| haproxy | Unified proxy to frontend | 8080 (HTTP), 9090 (gRPC), 3307 (MySQL), 5433 (PostgreSQL) |
| flownode | Flow engine (disabled by default) | 6800 (RPC), 6900 (HTTP) |

Datanodes store data in garage (S3) at `http://127.0.0.1:3900`, bucket `test-bucket`.

## Connecting

After `haproxy` is healthy, connect via the proxy ports:

| Protocol | Address |
|---|---|
| HTTP API | `http://127.0.0.1:8080` |
| gRPC | `127.0.0.1:9090` |
| MySQL | `127.0.0.1:3307` (user `root`, no password) |
| PostgreSQL | `127.0.0.1:5433` (user `root`, no password) |

Or connect directly to the frontend (bypassing haproxy) on ports 5000-5003.

## Starting Flownode

Flownode is disabled by default. Start it on demand:

```bash
process-compose start flownode
```

## Managing Individual Processes

```bash
# Start a specific process and its dependencies
process-compose up metasrv

# Restart a process
process-compose restart datanode-0

# View logs for a process
process-compose logs datanode-0

# Check process status
process-compose ps
```

## Data and Cleanup

All runtime data is stored in `.greptimedb/`. To reset the cluster:

```bash
process-compose down
rm -rf .greptimedb
```

## Configuration Variables

The following variables in `process-compose.yml` can be overridden:

| Variable | Default | Description |
|---|---|---|
| `GREPTIME_BIN` | `./greptime` | Path to the greptime binary |
| `GREPTIME_HOME` | `./.greptimedb` | Data directory for all components |
| `METASRV_ADDR` | `127.0.0.1:3002` | Metasrv gRPC address |

## Dev Shell Packages

The flake provides: `pkg-config`, `git`, `curl`, `kind`, `podman`, `garage`, `postgresql`, `process-compose`, `etcd`, `haproxy`.
