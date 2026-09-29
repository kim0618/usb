"""ZIP safety check, file inventory, raw-string load and typed normalization of the AOA ledger.

The raw CSVs are read as strings first so nothing is coerced before the schema is profiled;
typed columns are derived in `normalize_executions`, which keeps every original column.
"""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv

BASE = Path("data/research/expert_execution")
RAW_DIR = BASE / "aoa_raw"
NORM_DIR = BASE / "aoa_normalized"

# Guard rails for the central-directory check (before any extraction).
MAX_MEMBER_RATIO = 100.0
MAX_TOTAL_UNCOMPRESSED = 5 * 1024**3

EXEC_INT_COLS = ["lastqty", "orderqty", "leavesqty", "cumqty", "execcost", "execcomm"]
EXEC_FLOAT_COLS = ["lastpx", "price", "avgpx", "commission", "stoppx", "displayqty",
                   "homenotional", "foreignnotional", "pegoffsetvalue"]
SIDE_SIGN = {"Buy": 1, "Sell": -1, "": 0}


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


@dataclass
class MemberCheck:
    name: str
    compressed: int
    uncompressed: int
    ratio: float
    encrypted: bool
    utf8_name_flag: bool


def check_zip(path: Path | str) -> dict:
    """Read the central directory only and decide whether extraction is safe."""
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
    members = [MemberCheck(i.filename, i.compress_size, i.file_size,
                           round(i.file_size / max(i.compress_size, 1), 3),
                           bool(i.flag_bits & 0x1), bool(i.flag_bits & 0x800)) for i in infos]
    names = [m.name for m in members]
    traversal = [n for n in names if ".." in Path(n).parts]
    absolute = [n for n in names if n.startswith(("/", "\\")) or (len(n) > 1 and n[1] == ":")]
    nested = [n for n in names if "/" in n.rstrip("/") or "\\" in n]
    suspicious = [n for n in names if Path(n).suffix.lower() in
                  {".exe", ".dll", ".bat", ".cmd", ".ps1", ".sh", ".js", ".vbs", ".scr", ".jar", ".msi"}]
    total_u = sum(m.uncompressed for m in members)
    reasons = []
    if traversal: reasons.append("path_traversal")
    if absolute: reasons.append("absolute_path")
    if len(set(names)) != len(names): reasons.append("duplicate_names")
    if any(m.encrypted for m in members): reasons.append("encrypted_member")
    if suspicious: reasons.append("executable_member")
    if any(m.ratio > MAX_MEMBER_RATIO for m in members): reasons.append("compression_ratio")
    if total_u > MAX_TOTAL_UNCOMPRESSED: reasons.append("total_uncompressed")
    return {
        "members": [asdict(m) for m in members],
        "member_count": len(members),
        "total_compressed": sum(m.compressed for m in members),
        "total_uncompressed": total_u,
        "overall_ratio": round(total_u / max(sum(m.compressed for m in members), 1), 3),
        "path_traversal": traversal, "absolute_paths": absolute, "nested_paths": nested,
        "duplicates": len(names) - len(set(names)), "suspicious": suspicious,
        "safe": not reasons, "unsafe_reasons": reasons,
    }


def classify_file(name: str) -> str:
    n = name.lower()
    if n.startswith("aoa-execution-") and n.endswith(".csv"):
        return "execution"
    if n.startswith("aoa-wallet-") and n.endswith(".csv"):
        return "wallet"
    if n.endswith((".txt", ".md", ".pdf")):
        return "letter_or_metadata"
    return "other"


def _encoding(head: bytes) -> str:
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "unknown"


def inventory(raw_dir: Path = RAW_DIR) -> list[dict]:
    rows = []
    for p in sorted(raw_dir.iterdir()):
        if not p.is_file():
            continue
        with open(p, "rb") as f:
            head = f.read(65536)
        enc = _encoding(head)
        kind = classify_file(p.name)
        delim = ""
        records = None
        if p.suffix.lower() == ".csv":
            delim = csv.Sniffer().sniff(head.decode("utf-8-sig", "replace").splitlines()[0]).delimiter
            # Logical CSV records (quoted cells may contain newlines), excluding the header.
            with open(p, encoding="utf-8-sig", newline="") as f:
                records = sum(1 for _ in csv.reader(f)) - 1
        with open(p, "rb") as f:
            physical = sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))
        rows.append({"filename": p.name, "extension": p.suffix.lower(), "kind": kind,
                     "bytes": p.stat().st_size, "sha256": sha256_file(p), "encoding": enc,
                     "physical_lines": physical, "csv_records": records, "delimiter": delim})
    return rows


def read_raw_strings(path: Path | str) -> pd.DataFrame:
    """Every column as string; empty cells stay '' (not NaN) so emptiness is auditable."""
    with open(path, encoding="utf-8-sig") as f:
        header = next(csv.reader(f))
    t = pacsv.read_csv(
        path,
        read_options=pacsv.ReadOptions(use_threads=False, column_names=header, skip_rows=1),
        parse_options=pacsv.ParseOptions(newlines_in_values=True),
        convert_options=pacsv.ConvertOptions(column_types={c: pa.string() for c in header},
                                             strings_can_be_null=False, quoted_strings_can_be_null=False),
    )
    return t.to_pandas()


def load_raw_executions(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    parts = []
    for p in sorted(raw_dir.glob("aoa-execution-*.csv")):
        d = read_raw_strings(p)
        d.insert(0, "src_file", p.name)
        d.insert(1, "src_row", np.arange(len(d), dtype=np.int64))
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def parse_ts(s: pd.Series) -> pd.Series:
    """BitMEX 'YYYY-MM-DD HH:MM:SS[.f{1,6}]' in UTC. Malformed values become NaT (counted upstream)."""
    return pd.to_datetime(s.replace("", None), format="ISO8601", utc=True, errors="coerce")


def _num(s: pd.Series, kind: str) -> pd.Series:
    x = pd.to_numeric(s.replace("", None), errors="coerce")
    if kind == "int":
        return x.astype("Int64")
    return x.astype("float64")


def normalize_side(s: pd.Series) -> pd.Series:
    unknown = set(s.unique()) - set(SIDE_SIGN)
    if unknown:
        raise ValueError(f"unknown side values: {sorted(unknown)}")
    return s.map(SIDE_SIGN).astype("int8")


def normalize_executions(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    for c in EXEC_INT_COLS:
        df[c] = _num(df[c], "int")
    for c in EXEC_FLOAT_COLS:
        df[c] = _num(df[c], "float")
    df["transact_ts"] = parse_ts(raw["transacttime"])
    df["record_ts"] = parse_ts(raw["timestamp"])
    df["side_sign"] = normalize_side(raw["side"])
    df["signed_qty"] = (df["lastqty"].astype("int64") * df["side_sign"]).astype("int64")
    df["is_maker"] = raw["lastliquidityind"].map({"AddedLiquidity": True, "RemovedLiquidity": False})
    df["year"] = df["transact_ts"].dt.year.astype("int16")
    # Stable global order: file order first (files are chronological and disjoint), then row.
    df = df.sort_values(["transact_ts", "src_file", "src_row"], kind="mergesort").reset_index(drop=True)
    df.insert(0, "seq", np.arange(len(df), dtype=np.int64))
    return df


def load_wallet(raw_dir: Path = RAW_DIR) -> tuple[pd.DataFrame, dict]:
    p = next(raw_dir.glob("aoa-wallet-*.csv"))
    raw = read_raw_strings(p)
    blank = (raw.drop(columns=[]).apply(lambda c: c.str.strip() == "")).all(axis=1)
    w = raw[~blank].copy()
    w.insert(0, "src_row", w.index.astype("int64"))
    w["amount_xbt_sat"] = _num(w["amount"], "int")
    w["walletbalance_sat"] = _num(w["walletbalance"], "int")
    w["date_utc"] = pd.to_datetime(w["date"], format="%Y-%m-%d", errors="coerce")
    ts_ok = w["timestamp"].str.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?")
    meta = {"file": p.name, "raw_records": len(raw), "blank_records": int(blank.sum()),
            "records": len(w), "timestamp_full_datetime": int(ts_ok.sum()),
            "timestamp_truncated_mmss": int(w["timestamp"].str.fullmatch(r"\d{2}:\d{2}\.\d").sum())}
    return w.reset_index(drop=True), meta


def write_parquet(df: pd.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="zstd")
    return path.stat().st_size
