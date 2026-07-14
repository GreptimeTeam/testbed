#!/usr/bin/env python3
"""
Read GreptimeDB Iceberg tables via the REST catalog using pyiceberg.

Two modes:
  Default (S3):    talks to the REST catalog, then reads parquet data from
                   Garage S3 (needs .greptimedb/s3.env).
  -local (File):   talks to the REST catalog, then reads parquet data from
                   the local filesystem (for standalone-fs mode). No S3 config
                   is needed.

Usage:
    # S3 mode (default): requires .greptimedb/s3.env
    python3 scripts/read_iceberg.py [TABLE] [-local]

    # Local File mode: for standalone-fs
    python3 scripts/read_iceberg.py -local [TABLE]

    TABLE is an optional positional argument naming the table to query
    (default: opentelemetry_traces4). Works for any Iceberg-exposed table,
    regardless of schema (display is generic: sorts by `timestamp` if present,
    then shows all columns).

Optional env vars:
    ICEBERG_CATALOG_URI   REST catalog URI (default http://127.0.0.1:11040/v1/iceberg)
    ICEBERG_CATALOG       catalog prefix   (default greptime)
    ICEBERG_TABLE         table to query   (overridden by the TABLE arg)
"""

import argparse
import os
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import pyarrow.parquet as pq
import pyarrow.compute as pc
import pyarrow as pa
from pyiceberg.catalog.rest import RestCatalog
from pyiceberg.io.pyarrow import PyArrowFileIO
from pyiceberg.manifest import read_manifest_list

CATALOG_URI = os.environ.get("ICEBERG_CATALOG_URI", "http://127.0.0.1:11040/v1/iceberg")
CATALOG_NAME = os.environ.get("ICEBERG_CATALOG", "greptime")
TABLE_NAME = os.environ.get("ICEBERG_TABLE", "opentelemetry_traces4")


def parse_args():
    p = argparse.ArgumentParser(description="Query GreptimeDB Iceberg tables via the REST catalog.")
    p.add_argument(
        "table", nargs="?", default=os.environ.get("ICEBERG_TABLE", "opentelemetry_traces4"),
        help="Table name to query (default: opentelemetry_traces4, or $ICEBERG_TABLE).",
    )
    p.add_argument(
        "-local", "--local", dest="local", action="store_true",
        help="Read parquet data from the local filesystem (standalone-fs mode); "
             "ignore S3 config and .greptimedb/s3.env.",
    )
    return p.parse_args()


def load_s3_env():
    """Load S3 credentials from .greptimedb/s3.env. Returns a dict (may be empty)."""
    s3_env_path = os.path.join(os.path.dirname(__file__), "..", ".greptimedb", "s3.env")
    env = {}
    if os.path.exists(s3_env_path):
        with open(s3_env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    parts = line[7:].split("=", 1)
                    if len(parts) == 2:
                        env[parts[0]] = parts[1].strip("\"'")
    return env, s3_env_path


def local_path(file_path: str) -> str:
    """Normalize a local file path by stripping any file:// scheme prefix."""
    if file_path.startswith("file://"):
        return file_path[len("file://"):]
    return file_path


def normalize_dictionary_columns(table: pa.Table) -> pa.Table:
    """Compatibility layer: cast dictionary-encoded columns to their underlying
    value type so tables written with different physical encodings can be
    concatenated.

    GreptimeDB writes the ARROW:schema parquet annotation, which pyarrow honors.
    As a result a column that is physically dictionary-encoded (e.g.
    `__primary_key` as `dictionary<values=binary, indices=uint32>`) is exposed
    as a dictionary type. Different flushes/compactions may encode the same
    column differently (plain `binary` in one file, `dictionary<binary>` in
    another), which makes `pa.concat_tables` fail with a schema mismatch.

    Casting every dictionary<T> column to T here makes all files consistent,
    independent of how each parquet writer happened to encode the column. This
    is a pure read-side fix and does not touch the underlying data. Most major
    Iceberg consumers (Spark, Trino, DuckDB) are unaffected by this because
    they ignore the ARROW:schema annotation; this normalizes it for pyarrow.
    """
    new_fields = []
    changed = False
    for field in table.schema:
        if pa.types.is_dictionary(field.type):
            new_fields.append(field.with_type(field.type.value_type))
            changed = True
        else:
            new_fields.append(field)
    if not changed:
        return table
    return table.cast(pa.schema(new_fields))


def concat_tables_compat(tables):
    """Concatenate tables, tolerating per-file type drift.

    Each table is first normalized (dictionary -> value type, see above), then
    concatenated with `promote_options="default"` so any remaining type
    differences (e.g. timestamp precision `ns` vs `ms`) are reconciled to a
    common type instead of raising.
    """
    normalized = [normalize_dictionary_columns(t) for t in tables]
    return pa.concat_tables(normalized, promote_options="default")


def main():
    args = parse_args()
    LOCAL = args.local
    TABLE_NAME = args.table

    # ── Set up file IO and REST catalog based on mode ──
    if LOCAL:
        print("Mode: LOCAL (reading from filesystem, no S3)\n")
        s3_props = {}
        io = PyArrowFileIO()  # plain local filesystem IO
        catalog = RestCatalog(
            name=CATALOG_NAME, uri=CATALOG_URI, prefix=CATALOG_NAME,
        )
    else:
        s3_env, s3_env_path = load_s3_env()
        if not s3_env:
            print(f"WARNING: {s3_env_path} not found or empty. "
                  f"Start the cluster first, or use -local for standalone-fs.")
            exit(1)
        print("Mode: S3 (reading from Garage)\n")
        s3_props = {
            "s3.endpoint": s3_env.get("AWS_ENDPOINT_URL", "http://127.0.0.1:11010"),
            "s3.access-key-id": s3_env.get("AWS_ACCESS_KEY_ID", ""),
            "s3.secret-access-key": s3_env.get("AWS_SECRET_ACCESS_KEY", ""),
            "s3.region": s3_env.get("AWS_REGION", "garage"),
        }
        io = PyArrowFileIO(properties=s3_props)
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
    print(f"--- Query: recent rows from {TABLE_NAME} (LIMIT 10) ---")

    iceberg_col_names = [f.name for f in iceberg_schema.fields]
    iceberg_col_set = set(iceberg_col_names)

    def read_parquet_file(file_path: str) -> pa.Table:
        """Read a parquet file, selecting only Iceberg schema columns."""
        if LOCAL:
            tbl = pq.read_table(local_path(file_path), columns=iceberg_col_names)
        else:
            # S3 mode: download then read
            with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
                tmp_path = tmp.name
            subprocess.run(
                ["aws", "s3", "cp", file_path, tmp_path,
                 "--endpoint-url", s3_props["s3.endpoint"],
                 "--region", s3_props["s3.region"]],
                check=True, capture_output=True,
            )
            try:
                tbl = pq.read_table(tmp_path, columns=iceberg_col_names)
            finally:
                os.unlink(tmp_path)
        # Compatibility layer: normalize dictionary encodings so files with
        # differing physical encodings can be concatenated later.
        return normalize_dictionary_columns(tbl)

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
        combined = concat_tables_compat(tables)

        # Generic display: sort by `timestamp` descending if present, else
        # leave rows in file order; then take the first LIMIT rows.
        LIMIT = 10
        VALUE_WIDTH = 50
        sort_col = "timestamp" if "timestamp" in combined.column_names else None
        if sort_col:
            sort_idx = pc.array_sort_indices(combined[sort_col], order="descending")
            top = combined.take(sort_idx[:LIMIT])
        else:
            top = combined.slice(0, LIMIT)

        def fmt_scalar(v):
            try:
                s = str(v.as_py())
            except ValueError:
                # nanosecond timestamp without pandas: fall back to raw int
                try:
                    s = str(v.cast(pa.int64()).as_py())
                except Exception:
                    s = str(v)
            return s[:VALUE_WIDTH]

        for i in range(len(top)):
            row = {c: fmt_scalar(top.column(c)[i]) for c in top.column_names}
            print(f"  {row}")
        print(f"  ({combined.num_rows} total rows)")
    else:
        print("  No data read.")


if __name__ == "__main__":
    main()
