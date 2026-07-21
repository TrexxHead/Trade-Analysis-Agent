"""Loads candles from an MT4/MT5 or TradingView chart-data CSV export.

Handles the common variants without requiring a specific one:
- MT4's headerless export: Date,Time,Open,High,Low,Close,Volume
  (date as YYYY.MM.DD, time as HH:MM) - separate date and time columns
- MT5's headered export: <DATE>	<TIME>	<OPEN>	<HIGH>	<LOW>	<CLOSE>	<TICKVOL>	<VOL>	<SPREAD>
  (tab-delimited) - also separate date/time columns
- TradingView's "Export chart data": time,open,high,low,close[,Volume,...]
  - a single timestamp column, either a Unix epoch (seconds) or an ISO-ish
  string ("2024-01-02T00:00:00Z" or "2024-01-02 00:00:00"), plus whatever
  extra indicator columns happen to be on the chart (ignored - only the
  four columns right after the timestamp are read as OHLC)
- A single combined "date time" column using MT-style dotted dates and no
  explicit offset ("2024.01.02 00:00") - what MT5's own "Export Bars"
  actually produces, as opposed to the two-separate-columns format above

MT5 exports are also commonly saved as UTF-16 (with a BOM) rather than
UTF-8 - detected and decoded automatically here.

Timestamps are taken as-is from the file and treated as UTC. MT4/5 terminals
actually export in the broker's server time (often GMT+2/+3, not true UTC) -
that offset is not corrected for here. It doesn't affect the backtest's
relative sequencing, but keep it in mind if you ever compare these
timestamps against Deriv/MetaApi data (or a TradingView export) that IS
true UTC.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def _detect_delimiter(line: str) -> str:
    for delimiter in ("\t", ",", ";"):
        if line.count(delimiter) >= 4:
            return delimiter
    return ","


def _looks_like_header(first_field: str) -> bool:
    stripped = first_field.strip("<>").upper()
    return stripped in ("DATE", "TIME") or not any(ch.isdigit() for ch in first_field)


def _looks_like_time(field: str) -> bool:
    return bool(_TIME_RE.match(field))


def _to_iso(date_str: str, time_str: str) -> str:
    date_str = date_str.replace(".", "-").replace("/", "-")
    if time_str.count(":") == 1:
        time_str += ":00"
    return f"{date_str}T{time_str}+00:00"


def _parse_timestamp(value: str) -> str:
    value = value.strip()
    if value.isdigit():
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()

    value = value.replace(".", "-").replace("Z", "+00:00")
    date_part, _, rest = value.partition(" ") if " " in value else value.partition("T")
    time_part, has_offset, offset = rest.partition("+")
    if time_part.count(":") == 1:
        time_part += ":00"
    return f"{date_part}T{time_part}+{offset}" if has_offset else f"{date_part}T{time_part}+00:00"


def _read_text(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8")


def load_candles_csv(path: str | Path) -> list[dict]:
    lines = [line.strip() for line in _read_text(path).splitlines() if line.strip()]
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
            # separate date/time columns (MT4/5-style)
            iso_time = _to_iso(fields[0], fields[1])
            o, h, l, c = fields[2], fields[3], fields[4], fields[5]
        else:
            # single timestamp column (TradingView-style)
            iso_time = _parse_timestamp(fields[0])
            o, h, l, c = fields[1], fields[2], fields[3], fields[4]

        candles.append({
            "time": iso_time,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
        })

    candles.sort(key=lambda c: c["time"])
    return candles
