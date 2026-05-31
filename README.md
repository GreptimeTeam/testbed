# GreptimeDB Development Environment

A Nix flake for running a local GreptimeDB cluster with process-compose.

## Quick Start

```bash
nix develop
process-compose up
```

Place a `greptime` binary in the project root before starting.

## Connecting

| Protocol | Address |
|---|---|
| HTTP | `http://127.0.0.1:8080` |
| gRPC | `127.0.0.1:9090` |
| MySQL | `127.0.0.1:3307` |
| PostgreSQL | `127.0.0.1:5433` |

## Start Flownode

```bash
process-compose start flownode
```

## Cleanup

```bash
process-compose down
rm -rf .greptimedb
```
