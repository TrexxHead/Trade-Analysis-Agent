"""Loads candles from an MT4/MT5 History Center CSV export.

Handles the common variants without requiring a specific one:
- MT4's headerless export: Date,Time,Open,High,Low,Close,Volume
  (date as YYYY.MM.DD, time as HH:MM)
- MT5's headered export: <DATE>	<TIME>	<OPEN>	<HIGH>	<LOW>	<CLOSE>	<TICKVOL>	<VOL>	<SPREAD>
  (tab-delimited)
- A combined date+time first column instead of two separate ones

Timestamps are taken as-is from the file and treated as UTC. MT4/5 terminals
actually export in the broker's server time (often GMT+2/+3, not true UTC) -
that offset is not corrected for here. It doesn't affect the backtest's
relative sequencing, but keep it in mind if you ever compare these
timestamps against Deriv/MetaApi data that IS true UTC.
"""
import re
from pathlib import Path

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def _detect_delimiter(line: str) -> str:
    for delimiter in ("\t", ",", ";"):
        if line.count(delimiter) >= 4:
            return delimiter
    return ","


def _looks_like_header(first_field: str) -> bool:
    stripped = first_field.strip("<>").upper()
    return stripped == "DATE" or not any(ch.isdigit() for ch in first_field)


def _looks_like_time(field: str) -> bool:
    return bool(_TIME_RE.match(field))


def _to_iso(date_str: str, time_str: str) -> str:
    date_str = date_str.replace(".", "-").replace("/", "-")
    if time_str.count(":") == 1:
        time_str += ":00"
    return f"{date_str}T{time_str}+00:00"


def load_candles_csv(path: str | Path) -> list[dict]:
    lines = [line.strip() for line in Path(path).read_text().splitlines() if line.strip()]
    if not lines:
        return []

    delimiter = _detect_delimiter(lines[0])
    first_fields = [f.strip().strip('"') for f in lines[0].split(delimiter)]
    start = 1 if _looks_like_header(first_fields[0]) else 0

    candles = []
    for line in lines[start:]:
        fields = [f.strip().strip('"') for f in line.split(delimiter)]
        if len(fields) < 5:
            continue

        if _looks_like_time(fields[1]):
            date_str, time_str, o, h, l, c = fields[0], fields[1], fields[2], fields[3], fields[4], fields[5]
        else:
            date_str, time_str = (fields[0].split(" ", 1) + ["00:00:00"])[:2]
            o, h, l, c = fields[1], fields[2], fields[3], fields[4]

        candles.append({
            "time": _to_iso(date_str, time_str),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
        })

    candles.sort(key=lambda c: c["time"])
    return candles
