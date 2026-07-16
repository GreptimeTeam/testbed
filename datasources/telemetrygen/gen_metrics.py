#!/usr/bin/env python3
"""
OTLP metrics generator for GreptimeDB.

A SINGLE process that emits many distinct metrics — a lighter, less hacky
replacement for spawning one telemetrygen container per metric name (telemetrygen
only emits a single --otlp-metric-name per process).

It creates `TG_METRIC_COUNT` metrics, each with a randomized name and a randomized
type (Gauge / Sum / Histogram), observes them continuously from `TG_METRICS_WORKERS`
threads at `TG_METRICS_RATE` observations/sec/worker, and exports every
`TG_METRICS_INTERVAL` over OTLP HTTP.

Every observation carries a semi-random `timebox` label (a bounded pool of many
distinct values whose leading digit distributes across 1..9), so the optional
storage partitioning (range partitions on `timebox`, see ./testbedctl
metrics-partition) still receives data in all partitions.

Metric names are reproducible for a given `TG_METRIC_SEED`.

Environment (all optional):
  TG_OTLP_ENDPOINT      host:port of the OTLP receiver      (default 127.0.0.1:11050)
  TG_OTLP_URL_PATH      OTLP HTTP path                       (default /v1/otlp/v1/metrics)
  TG_METRIC_COUNT       number of distinct metric names      (default 50)
  TG_METRIC_SEED        RNG seed for reproducible names       (default 0)
  TG_METRICS_INTERVAL   export / collection interval          (default 5s)
  TG_METRICS_RATE       observations/sec per worker           (default 10)
  TG_METRICS_WORKERS    worker threads                        (default 2)

  --list   just print the generated metric names + types and exit (no export)

Run (normally via testbedctl, which manages a pidfile + log):
  python3 gen_metrics.py            # run continuously
  python3 gen_metrics.py --list     # print names only
"""
import argparse
import os
import random
import signal
import sys
import threading
import time

# Realistic metric-name bases. Avoid suffixes GreptimeDB would double up: it
# derives the logical table name from the OTLP metric name + a type suffix
# (Sum -> "<name>_total", Histogram -> "<name>_{bucket,count,sum}", Gauge -> "<name>").
METRIC_BASES = [
    "cpu_usage", "cpu_load", "memory_usage", "memory_bytes", "disk_usage",
    "disk_io", "disk_reads", "disk_writes", "network_bytes", "network_packets",
    "network_errors", "http_requests", "http_request_duration", "http_response_size",
    "grpc_requests", "grpc_request_duration", "db_connections", "db_query_duration",
    "db_pool_size", "queue_depth", "queue_messages", "queue_latency",
    "gc_pause", "gc_count", "thread_count", "goroutine_count", "file_open",
    "cache_hits", "cache_misses", "cache_size", "session_count", "login_attempts",
    "api_latency", "request_errors", "uptime", "event_count", "log_bytes",
    "kafka_consumer_lag", "kafka_producer_rate", "redis_ops", "rpc_duration",
    "scheduler_jobs", "worker_pool_size", "connection_pool_used",
]

QUALIFIERS = [
    "billing", "checkout", "auth", "inventory", "search", "payments",
    "notifications", "shipping", "users", "orders", "cart", "catalog",
    "frontend", "backend", "worker", "scheduler", "gateway", "proxy",
    "api", "web", "mobile", "admin", "public", "internal", "staging",
    "prod", "edge", "core", "db", "cache", "queue", "batch",
    "realtime", "etl", "ingest", "export", "sync", "report", "audit",
    "health", "metrics", "trace", "log", "span",
]

METRIC_TYPES = ["Gauge", "Sum", "Histogram"]


# --- config helpers ---------------------------------------------------------

def env(name, default):
    v = os.environ.get(name)
    return v if v is not None else default


def env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def parse_duration(s, default_s):
    """Parse '5s', '500ms', '1m', or a bare number of seconds."""
    if not s:
        return float(default_s)
    s = s.strip()
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000.0
        if s.endswith("s"):
            return float(s[:-1])
        if s.endswith("m"):
            return float(s[:-1]) * 60.0
        if s.endswith("h"):
            return float(s[:-1]) * 3600.0
        return float(s)
    except ValueError:
        return float(default_s)


# --- generation -------------------------------------------------------------

def make_metric_specs(seed, count):
    """Return a list of (name, type) with unique names, reproducible for (seed,count)."""
    rng = random.Random(seed)
    used = set()
    specs = []
    while len(specs) < count:
        base = rng.choice(METRIC_BASES)
        qual = rng.choice(QUALIFIERS)
        if rng.random() < 0.3:
            name = f"{base}_{qual}_{rng.randint(0, 9)}"
        else:
            name = f"{base}_{qual}"
        if name in used:
            continue
        used.add(name)
        specs.append((name, rng.choice(METRIC_TYPES)))
    return specs


def make_timeboxes(seed, per_digit=12):
    """Bounded pool of many distinct timebox strings.

    Leading char is a digit 1..9, so the range partitions used by
    `metrics-partition` (< '2', '2'..'5', '5'..'8', >= '8') all receive data:
    '1' -> P1, '2'-'4' -> P2, '5'-'7' -> P3, '8'-'9' -> P4.
    """
    rng = random.Random(seed + 777)
    boxes = []
    for d in range(1, 10):
        for _ in range(per_digit):
            boxes.append(f"{d}{rng.randint(0, 9999):04d}")
    rng.shuffle(boxes)
    return boxes


# --- OTel wiring ------------------------------------------------------------

def build_url():
    endpoint = env("TG_OTLP_ENDPOINT", "127.0.0.1:11050").rstrip("/")
    path = env("TG_OTLP_URL_PATH", "/v1/otlp/v1/metrics")
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint  # caller knows what they're doing
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{endpoint}{path}"


class Metric:
    """Uniform wrapper over gauge/counter/histogram instruments."""

    def __init__(self, name, mtype, meter):
        self.name = name
        self.mtype = mtype
        if mtype == "Gauge":
            self._inst = meter.create_gauge(name)
            self._fn = self._inst.set
        elif mtype == "Sum":
            self._inst = meter.create_counter(name)
            self._fn = self._inst.add
        else:  # Histogram
            self._inst = meter.create_histogram(name)
            self._fn = self._inst.record

    def observe(self, value, attrs):
        self._fn(value, attrs)


def value_for(mtype, rng):
    if mtype == "Gauge":
        return rng.uniform(0, 100)
    if mtype == "Sum":
        return rng.uniform(1, 5)
    return rng.lognormvariate(0, 1.2)  # latency-like, long tail


def worker(metrics, rate, timeboxes, stop, offset):
    rng = random.Random()
    period = (1.0 / rate) if rate > 0 else 0.0
    n = len(metrics)
    i = offset
    while not stop.is_set():
        m = metrics[i % n]
        tb = timeboxes[rng.randrange(len(timeboxes))]
        try:
            m.observe(value_for(m.mtype, rng), {"timebox": tb})
        except Exception as e:  # never let a single observation kill the worker
            print(f"[worker] observe error: {e}", file=sys.stderr)
        i += 1
        if period > 0:
            stop.wait(period)
        else:
            stop.wait(0)


# --- main -------------------------------------------------------------------

def run():
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )

    count = max(1, env_int("TG_METRIC_COUNT", 50))
    seed = env_int("TG_METRIC_SEED", 0)
    interval = parse_duration(env("TG_METRICS_INTERVAL", "5s"), 5.0)
    rate = env_float("TG_METRICS_RATE", 10)
    workers_n = max(1, env_int("TG_METRICS_WORKERS", 2))
    url = build_url()

    specs = make_metric_specs(seed, count)
    timeboxes = make_timeboxes(seed)

    resource = Resource.create({SERVICE_NAME: "telemetrygen"})
    exporter = OTLPMetricExporter(endpoint=url)
    reader = PeriodicExportingMetricReader(
        exporter, export_interval_millis=int(interval * 1000)
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    meter = metrics.get_meter("telemetrygen.metrics")

    instruments = [Metric(name, mtype, meter) for name, mtype in specs]

    print(
        f"telemetrygen metrics -> {url}\n"
        f"  metrics={len(instruments)} workers={workers_n} "
        f"rate={rate}/s/worker interval={interval}s seed={seed}\n"
        f"  timebox pool={len(timeboxes)} distinct values "
        f"(leading digit 1..9 -> all partitions populated)\n"
        f"  exporting every {interval:g}s; Ctrl-C / SIGTERM to stop.",
        file=sys.stderr,
        flush=True,
    )

    stop = threading.Event()

    def handle_signal(signum, _frame):
        print(f"\nreceived signal {signum}, shutting down...", file=sys.stderr, flush=True)
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threads = []
    for w in range(workers_n):
        t = threading.Thread(
            target=worker,
            args=(instruments, rate, timeboxes, stop, w),
            name=f"tg-metrics-{w}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    stop.wait()
    for t in threads:
        t.join(timeout=2.0)
    provider.shutdown()
    print("stopped.", file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description="OTLP metrics generator for GreptimeDB.")
    ap.add_argument("--list", action="store_true",
                    help="print the generated metric names + types and exit")
    args = ap.parse_args()
    if args.list:
        count = max(1, env_int("TG_METRIC_COUNT", 50))
        seed = env_int("TG_METRIC_SEED", 0)
        for name, mtype in make_metric_specs(seed, count):
            print(f"{name}\t{mtype}")
        return
    run()


if __name__ == "__main__":
    main()
