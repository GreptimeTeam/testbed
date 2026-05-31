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
scripts/garage-local  Standalone garage launcher (not used by process-compose)
.greptimedb/          Runtime data (gitignored), created on first start
```

## Starting the Cluster

```bash
nix develop
process-compose up
```

Requires a `greptime` binary in the project root. All processes start in dependency order with health checks. Garage data is wiped on each start.

## Cluster Topology

```
etcd -> metasrv -> datanode-{0,1,2} -> frontend -> haproxy
      -> garage -> garage-setup -(setup complete)-> datanodes
```

- **etcd**: metadata backend for metasrv (port 2379)
- **garage**: S3 storage for datanodes (port 3900, bucket `test-bucket`)
- **garage-setup**: exits after creating bucket/key/layout, writes creds to `.greptimedb/s3.env`
- **metasrv**: cluster coordinator (port 3002 gRPC, 4000 HTTP)
- **datanode-{0,1,2}**: store data in garage via S3 protocol, each uses `scripts/start-datanode`
- **frontend**: query layer (ports 5000-5003 for HTTP/gRPC/MySQL/PostgreSQL)
- **haproxy**: unified entry point proxying to frontend
- **flownode**: disabled by default, start with `process-compose start flownode`

## Connecting to GreptimeDB

Via haproxy (preferred):

| Protocol | Address | Notes |
|---|---|---|
| HTTP API | `http://127.0.0.1:8080` | Dashboard and REST API |
| gRPC | `127.0.0.1:9090` | |
| MySQL | `127.0.0.1:3307` | User `root`, no password |
| PostgreSQL | `127.0.0.1:5433` | User `root`, no password |

Direct to frontend (bypass haproxy): ports 5000-5003.

## Useful Commands

```bash
process-compose ps                          # check status
process-compose logs <process>              # view logs
process-compose restart <process>           # restart a process
process-compose start flownode              # start flownode
process-compose down                        # stop everything
rm -rf .greptimedb                          # clean all data
```

## Common Tasks

- **Reset cluster**: `process-compose down && rm -rf .greptimedb && process-compose up`
- **Change greptime binary**: replace `./greptime` or set `GREPTIME_BIN` in `process-compose.yml` vars
- **Adjust ports**: edit `process-compose.yml` (process ports) and `haproxy.cfg` (proxy ports)
