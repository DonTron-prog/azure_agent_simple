"""Build the SQLite warehouse from the UCI Online Retail II dataset.

One-off script. Idempotent: re-running rebuilds `warehouse.db` from the cached
.xlsx in `data/raw/`. The source is public (UCI ML Repository, CC BY 4.0).

Flow:
  1. Download `online_retail_II.xlsx` into data/raw/ if not present.
  2. Load both sheets into a pandas DataFrame.
  3. Clean: drop cancellations (Invoice starting with 'C'), drop nulls, drop
     non-positive quantity/price.
  4. Write to SQLite table `sales` and a normalised view `sales_v` that the
     agent's tools query.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import httpx
import pandas as pd

# Add src/ so we can reuse config. Run this script from the project root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from config import get_config  # noqa: E402

load_dotenv(ROOT / ".env")

DATASET_URL = "https://archive.ics.uci.edu/ml/machine-learning-databases/00502/online_retail_II.xlsx"
RAW_PATH = ROOT / "data" / "raw" / "online_retail_II.xlsx"


def main() -> None:
    cfg = get_config()
    db_path = ROOT / cfg.warehouse_db if not cfg.warehouse_db.is_absolute() else cfg.warehouse_db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not RAW_PATH.exists():
        _download(DATASET_URL, RAW_PATH)
    else:
        print(f"[ingest] cached source present at {RAW_PATH}")

    print("[ingest] loading xlsx (both sheets)…")
    df = _load_and_clean(RAW_PATH)
    print(f"[ingest] {len(df):,} clean rows across {df['invoice_date'].min()} → {df['invoice_date'].max()}")

    print(f"[ingest] writing to {db_path}")
    _write_sqlite(df, db_path)
    print("[ingest] done.")


def _download(url: str, dest: Path) -> None:
    print(f"[ingest] downloading {url} → {dest}")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"[ingest] downloaded {dest.stat().st_size / 1_000_000:.1f} MB")


def _load_and_clean(xlsx_path: Path) -> pd.DataFrame:
    sheets = pd.read_excel(xlsx_path, sheet_name=None, engine="openpyxl")
    df = pd.concat(sheets.values(), ignore_index=True)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(
        columns={
            "Invoice": "invoice",
            "StockCode": "stock_code",
            "Description": "description",
            "Quantity": "quantity",
            "InvoiceDate": "invoice_datetime",
            "Price": "price",
            "Customer ID": "customer_id",
            "Country": "country",
        }
    )
    df["invoice"] = df["invoice"].astype(str)
    df = df[~df["invoice"].str.startswith("C", na=False)]  # drop cancellations
    df = df.dropna(subset=["invoice_datetime", "quantity", "price", "stock_code"])
    df = df[(df["quantity"] > 0) & (df["price"] > 0)]
    df["invoice_datetime"] = pd.to_datetime(df["invoice_datetime"])
    df["invoice_date"] = df["invoice_datetime"].dt.strftime("%Y-%m-%d")
    df["invoice_month"] = df["invoice_datetime"].dt.strftime("%Y-%m")
    df["customer_id"] = df["customer_id"].astype("Int64").astype(str).replace("<NA>", None)
    df["description"] = df["description"].fillna("").str.strip()
    cols = [
        "invoice",
        "stock_code",
        "description",
        "quantity",
        "price",
        "invoice_datetime",
        "invoice_date",
        "invoice_month",
        "customer_id",
        "country",
    ]
    return df[cols].reset_index(drop=True)


def _write_sqlite(df: pd.DataFrame, db_path: Path) -> None:
    # Clear any leftover db + lock files from a previous failed run.
    # SQLite over network filesystems (Azure Files / NFS / CIFS) sometimes
    # leaves stale -journal / -wal / -shm files that cause "database is locked"
    # on the next write attempt.
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = db_path.with_name(db_path.name + suffix)
        if p.exists():
            p.unlink()

    conn = sqlite3.connect(str(db_path), isolation_level=None)  # autocommit; we manage txns
    try:
        # journal_mode=OFF + synchronous=OFF is safe here because we're rebuilding
        # from scratch — if ingest fails, just re-run it. These pragmas avoid the
        # journal/WAL files that are the main source of lock contention on network FS.
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA locking_mode = EXCLUSIVE")
        conn.execute("BEGIN")
        df.to_sql("sales", conn, index=False, if_exists="replace")
        conn.executescript(
            """
            CREATE INDEX idx_sales_date ON sales(invoice_date);
            CREATE INDEX idx_sales_month ON sales(invoice_month);
            CREATE INDEX idx_sales_country ON sales(country);
            CREATE INDEX idx_sales_stock ON sales(stock_code);

            CREATE VIEW sales_v AS
            SELECT
              invoice_date,
              invoice_month,
              country,
              stock_code,
              description,
              customer_id,
              quantity,
              price,
              quantity * price AS revenue
            FROM sales;
            """
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
