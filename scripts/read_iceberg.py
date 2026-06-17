#!/usr/bin/env python3
"""
Read GreptimeDB Iceberg tables via the REST catalog using pyiceberg.

Usage:
    source .greptimedb/s3.env
    python3 scripts/read_iceberg.py
"""

import os
import json
import traceback
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import pyarrow.parquet as pq
import pyarrow.compute as pc
import pyarrow as pa
import requests

from pyiceberg.catalog.rest import RestCatalog
from pyiceberg.io.pyarrow import PyArrowFileIO
from pyiceberg.manifest import read_manifest_list

# ── Load credentials from .greptimedb/s3.env ──
S3_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".greptimedb", "s3.env")
S3_ENV = {}
if os.path.exists(S3_ENV_PATH):
    with open(S3_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                parts = line[7:].split("=", 1)
                if len(parts) == 2:
                    S3_ENV[parts[0]] = parts[1].strip("\"'")
else:
    print(f"WARNING: {S3_ENV_PATH} not found. Start the cluster first.")
    exit(1)

CATALOG_URI = os.environ.get("ICEBERG_CATALOG_URI", "http://127.0.0.1:11040/v1/iceberg")
CATALOG_NAME = os.environ.get("ICEBERG_CATALOG", "greptime")
TABLE_NAME = os.environ.get("ICEBERG_TABLE", "opentelemetry_traces4")

s3_props = {
    "s3.endpoint": S3_ENV.get("AWS_ENDPOINT_URL", "http://127.0.0.1:11010"),
    "s3.access-key-id": S3_ENV.get("AWS_ACCESS_KEY_ID", ""),
    "s3.secret-access-key": S3_ENV.get("AWS_SECRET_ACCESS_KEY", ""),
    "s3.region": S3_ENV.get("AWS_REGION", "garage"),
}

io = PyArrowFileIO(properties=s3_props)

# ── Connect to REST catalog ──
catalog = RestCatalog(
    name=CATALOG_NAME,
    **{**s3_props, "uri": CATALOG_URI, "prefix": CATALOG_NAME},
)

print(f"=== Iceberg REST catalog at {CATALOG_URI} ===\n")

# ── Namespaces ──
print("--- Namespaces ---")
for ns in catalog.list_namespaces():
    print(f"  {'.'.join(ns)}")
print()

# ── Tables ──
print("--- Tables ---")
for table_id in catalog.list_tables("public"):
    print(f"  {'/'.join(table_id)}")
print()

# ── Load table ──
print(f"--- {TABLE_NAME} ---")
table = catalog.load_table(("public", TABLE_NAME))
iceberg_schema = table.schema()
print(f"  Schema ({len(iceberg_schema.fields)} fields):")
for field in iceberg_schema.fields:
    print(f"    {field.field_id}: {field.name} -> {field.field_type}")
snap = table.current_snapshot()
print(f"  Snapshot: {snap.snapshot_id}")
print(f"  Manifest list: {snap.manifest_list}")
print()

# ── Read all manifest entries ──
print("--- Reading manifest entries ---")
ml = list(read_manifest_list(io.new_input(snap.manifest_list)))
all_entries = []
for mf in ml:
    entries = list(mf.fetch_manifest_entry(io, discard_deleted=True))
    all_entries.extend(entries)
    print(f"  {mf.manifest_path}: {len(entries)} file(s)")

parquet_files = [e.data_file.file_path for e in all_entries]
print(f"\n  Total: {len(parquet_files)} parquet file(s)")
print()

# ── Query: read data from parquet files ──
# Equivalent to:
#   SELECT service_name, span_name, trace_id, span_id, parent_span_id,
#          duration_nano, span_kind, timestamp
#   FROM opentelemetry_traces4
#   ORDER BY timestamp DESC
#   LIMIT 10;
print("--- Query: recently ingested traces ---")

# Iceberg schema column names to read from parquet
iceberg_col_names = {f.name for f in iceberg_schema.fields}

def read_parquet_file(file_path: str) -> pa.Table:
    """Download and read a parquet file, selecting only Iceberg schema columns."""
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp_path = tmp.name
    subprocess.run(
        ["aws", "s3", "cp", file_path, tmp_path,
         "--endpoint-url", s3_props["s3.endpoint"],
         "--region", s3_props["s3.region"]],
        check=True, capture_output=True,
    )
    try:
        table = pq.read_table(tmp_path, columns=list(iceberg_col_names))
        return table
    finally:
        os.unlink(tmp_path)

tables = []
with ThreadPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(read_parquet_file, fp): fp for fp in parquet_files}
    for fut in as_completed(futures):
        try:
            tbl = fut.result()
            tables.append(tbl)
        except Exception as e:
            fp = futures[fut]
            print(f"  WARN: {os.path.basename(fp)}: {e}")

if tables:
    combined = pa.concat_tables(tables)
    sort_idx = pc.array_sort_indices(combined["timestamp"], order="descending")
    top10 = combined.take(sort_idx[:10])

    cols = ["service_name", "span_name", "trace_id", "span_id",
            "parent_span_id", "duration_nano", "span_kind", "timestamp"]
    for i in range(len(top10)):
        row = {}
        for c in cols:
            v = top10.column(c)[i]
            try:
                s = str(v.as_py())[:66]
            except ValueError:
                s = str(v.cast(pa.int64()).as_py())[:66]
            row[c] = s
        print(f"  {row}")
    print(f"  ({combined.num_rows} total rows)")
else:
    print("  No data read.")
