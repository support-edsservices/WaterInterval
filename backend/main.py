"""
main.py

Reads the cumulative water-meter totals from Teal_Water_Data.TEAL_WATER_DATA and keeps two
result tables in TEAL_DIFF up to date:

  Daily_Interval_Fwd : one row per rounded interval (00:00, 00:05, 00:10 ...)
                       the row 12:40 holds  (reading at 12:45) - (reading at 12:40)
  Daily_Fwd          : one row per day (00:00:00 of that day)
                       value = last reading of the day - first reading of the day

Run:   python water_diff.py          (keeps running, checks the source every RUN_INTERVAL_SEC seconds)
       python water_diff.py --once   (single run, then exit)
"""
import sys
import time
import logging
from datetime import datetime, timedelta

import pandas as pd

import config.settings as settings
from config.db_config import get_connection

log = logging.getLogger("water_diff")


# ----------------------------------------------------------------------------
# Column naming
# ----------------------------------------------------------------------------
def source_col(meter_id: int) -> str:
    return f"{settings.COLUMN_NAME_PREFIX}{meter_id}{settings.SOURCE_COLUMN_SUFFIX}"


def target_col(meter_id: int) -> str:
    return f"{settings.COLUMN_NAME_PREFIX}{meter_id}{settings.COLUMN_NAME_SUFFIX}"


# ----------------------------------------------------------------------------
# Database / table creation
# ----------------------------------------------------------------------------
def _connect(database: str):
    conn = get_connection(database)
    if conn is None:
        raise RuntimeError(f"Could not connect to database '{database}'")
    return conn


def ensure_database():
    conn = _connect("master")
    try:
        conn.autocommit = True      # CREATE DATABASE cannot run inside a transaction
        conn.cursor().execute(
            f"IF DB_ID(N'{settings.INTERVAL_DB}') IS NULL CREATE DATABASE [{settings.INTERVAL_DB}]"
        )
    finally:
        conn.close()


def ensure_table(conn, table: str, columns: list):
    """Create the table if missing and add any meter column that is not there yet."""
    cur = conn.cursor()
    cur.execute(f"""
        IF OBJECT_ID(N'dbo.{table}', N'U') IS NULL
        CREATE TABLE dbo.[{table}] (
            seq_no      INT IDENTITY(1,1) NOT NULL,
            [timestamp] DATETIME2(0)      NOT NULL,
            CONSTRAINT [PK_{table}] PRIMARY KEY ([timestamp])
        )
    """)
    for col in columns:
        cur.execute(
            f"IF COL_LENGTH(N'dbo.{table}', N'{col}') IS NULL "
            f"ALTER TABLE dbo.[{table}] ADD [{col}] FLOAT NULL"
        )
    conn.commit()


# ----------------------------------------------------------------------------
# Read the source table
# ----------------------------------------------------------------------------
def fetch_source(fetch_from: datetime) -> pd.DataFrame:
    """Raw readings since fetch_from. Columns are renamed to the result column names."""
    conn = _connect(settings.SOURCE_TABLE_DB)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?",
            settings.SOURCE_TABLE_ALL_DATA,
        )
        existing = {r[0].lower() for r in cur.fetchall()}

        meters = []
        for m in settings.METER_IDS:
            if source_col(m).lower() in existing:
                meters.append(m)
            else:
                log.warning("Source column %s not found - meter %s skipped", source_col(m), m)
        if not meters:
            return pd.DataFrame(columns=["timestamp"])

        select = ", ".join(f"[{source_col(m)}]" for m in meters)
        cur.execute(
            f"SELECT [timestamp], {select} FROM dbo.[{settings.SOURCE_TABLE_ALL_DATA}] "
            f"WHERE [timestamp] >= ? ORDER BY [timestamp]",
            fetch_from,
        )
        rows = [tuple(r) for r in cur.fetchall()]
    finally:
        conn.close()

    df = pd.DataFrame.from_records(rows, columns=["timestamp"] + [target_col(m) for m in meters])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for c in df.columns[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ----------------------------------------------------------------------------
# Calculations (pure functions - no database)
# ----------------------------------------------------------------------------
def build_interval_rows(df: pd.DataFrame, minutes: int, write_from: datetime,
                        now: datetime, wait_minutes: float) -> pd.DataFrame:
    """
    1. Round every reading to the nearest interval (12:46:49 -> 12:45 for 5 min).
    2. If several readings fall in one interval keep the one closest to the interval time.
    3. The row of an interval holds (reading of the NEXT interval) - (reading of this interval),
       e.g. row 12:40 = reading(12:45) - reading(12:40).
    4. WAIT: the row of interval S is written only when  now >= S + wait_minutes.
       (row 12:45 needs the reading of 12:50, which arrives about 12:51:49, so it is not
       written before 12:52 - nothing is inserted early with a wrong value.)
    """
    value_cols = [c for c in df.columns if c != "timestamp"]
    if df.empty or not value_cols:
        return pd.DataFrame(columns=value_cols)

    d = df.copy()
    d["slot"] = d["timestamp"].dt.round(f"{minutes}min")
    d["dist"] = (d["timestamp"] - d["slot"]).abs()
    d = (d.sort_values(["slot", "dist", "timestamp"], ascending=[True, True, False])
          .drop_duplicates("slot", keep="first")
          .set_index("slot")
          .sort_index())

    out = pd.DataFrame(index=d.index)
    for c in value_cols:
        diff = d[c].dropna().diff().shift(-1)   # next - this
        bad = diff < 0                       # meter reset / replaced -> do not store a negative usage
        if bad.any():
            log.warning("%s: %d negative difference(s) ignored", c, int(bad.sum()))
            diff[bad] = float("nan")
        out[c] = diff.reindex(d.index)

    ready_until = now - timedelta(minutes=wait_minutes)
    out = out[(out.index >= write_from) & (out.index <= ready_until)]
    return out.dropna(how="all")


def build_daily_rows(df: pd.DataFrame, write_from: datetime) -> pd.DataFrame:
    """Daily value = last reading of the day - first reading of the day (keyed at 00:00:00)."""
    value_cols = [c for c in df.columns if c != "timestamp"]
    d = df[df["timestamp"] >= write_from].copy()
    if d.empty or not value_cols:
        return pd.DataFrame(columns=value_cols)

    d["day"] = d["timestamp"].dt.normalize()
    g = d.sort_values("timestamp").groupby("day")
    out = g[value_cols].last() - g[value_cols].first()
    for c in value_cols:
        out.loc[out[c] < 0, c] = float("nan")
    return out


# ----------------------------------------------------------------------------
# Write: update the row if the timestamp exists, otherwise insert
# ----------------------------------------------------------------------------
_written = {}      # table -> {timestamp: values}; rows already saved with the same values are skipped


def upsert(conn, table: str, frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    cache = _written.setdefault(table, {})
    cols = list(frame.columns)
    set_clause = ", ".join(f"[{c}] = ?" for c in cols)
    col_list = ", ".join(f"[{c}]" for c in cols)
    marks = ", ".join("?" for _ in cols)
    sql = (
        f"UPDATE dbo.[{table}] SET {set_clause} WHERE [timestamp] = ?; "
        f"IF @@ROWCOUNT = 0 INSERT INTO dbo.[{table}] ([timestamp], {col_list}) VALUES (?, {marks});"
    )
    params, pending = [], {}
    for ts, row in frame.iterrows():
        vals = [None if pd.isna(v) else float(v) for v in row]
        ts = ts.to_pydatetime().replace(microsecond=0)
        if cache.get(ts) == vals:
            continue                                  # nothing changed since the last run
        params.append(vals + [ts, ts] + vals)
        pending[ts] = vals

    if not params:
        return 0
    cur = conn.cursor()
    cur.executemany(sql, params)
    conn.commit()
    cache.update(pending)
    return len(params)


# ----------------------------------------------------------------------------
# One run
# ----------------------------------------------------------------------------
_setup_done = False


def run_once():
    global _setup_done
    minutes = int(settings.INTERVAL_STORAGE)
    if minutes <= 0:
        raise ValueError("INTERVAL_STORAGE must be greater than 0")

    now = datetime.now()
    write_from = datetime.combine(now.date() - timedelta(days=settings.WATCH_BACK_DAYS), datetime.min.time())
    fetch_from = write_from

    conn = None
    try:
        if not _setup_done:                           # database / tables / meter columns: first run only
            ensure_database()
        conn = _connect(settings.INTERVAL_DB)
        if not _setup_done:
            result_cols = [target_col(m) for m in settings.METER_IDS]
            ensure_table(conn, settings.INTERVAL_TABLE, result_cols)
            ensure_table(conn, settings.DAILY_TABLE, result_cols)
            _setup_done = True

        df = fetch_source(fetch_from)
        if df.empty:
            log.info("No source data since %s", fetch_from)
            return

        n_int = upsert(conn, settings.INTERVAL_TABLE, build_interval_rows(df, minutes, write_from, now, settings.WAIT_MINUTES))
        n_day = upsert(conn, settings.DAILY_TABLE, build_daily_rows(df, write_from))
        if n_int or n_day:
            log.info("Interval rows written: %d | Daily rows written: %d", n_int, n_day)
    except Exception:
        _setup_done = False                           # re-check tables on the next run
        raise
    finally:
        if conn:
            conn.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--once" in sys.argv:
        run_once()
        return

    wait = max(1, int(settings.RUN_INTERVAL_SEC))
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Run failed - will retry on the next cycle")
        time.sleep(wait)


if __name__ == "__main__":
    main()