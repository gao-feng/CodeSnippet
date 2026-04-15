#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


TOOL_NAME = "mmkv_analyzer"
DEFAULT_LOG_DIR = Path("D:/mmkv_analyzer_logs")
MAX_PREVIEW_BYTES = 64
SKIP_SUFFIXES_IN_DIR_MODE = (".crc", ".lock", ".tmp", ".bak")


class MMKVAnalyzeError(RuntimeError):
    pass


@dataclass
class Varint:
    value: int
    start: int
    end: int


@dataclass
class Entry:
    index: int
    entry_offset: int
    entry_end: int
    key: str
    key_hex: str
    key_len: int
    value_len: int
    value_sha256_16: str
    value_preview_hex: str
    value_hint: str
    value_inner_length: int | None

    @property
    def total_encoded_len(self) -> int:
        return self.entry_end - self.entry_offset


def info(message: str) -> None:
    print(f"[info] {message}")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(name: str) -> str:
    cleaned = re.sub(r"\s+", "_", name.strip())
    cleaned = re.sub(r"[^0-9A-Za-z_.-]", "_", cleaned)
    return cleaned or "unknown"


def read_varint(blob: bytes, offset: int) -> Varint:
    result = 0
    shift = 0
    start = offset
    while offset < len(blob):
        byte = blob[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if byte & 0x80 == 0:
            return Varint(result, start, offset)
        shift += 7
        if shift >= 64:
            raise MMKVAnalyzeError(f"varint too long at payload offset {start}")
    raise MMKVAnalyzeError(f"unexpected EOF while reading varint at payload offset {start}")


def read_container(blob: bytes, offset: int, label: str) -> tuple[bytes, Varint, int]:
    length = read_varint(blob, offset)
    start = length.end
    end = start + length.value
    if end > len(blob):
        raise MMKVAnalyzeError(
            f"{label} length {length.value} at payload offset {length.start} exceeds payload size {len(blob)}"
        )
    return blob[start:end], length, end


def decode_key(raw: bytes) -> tuple[str, str]:
    key_hex = raw.hex()
    try:
        key = raw.decode("utf-8")
    except UnicodeDecodeError:
        key = "hex:" + key_hex
    return key, key_hex


def try_inner_container(value: bytes) -> tuple[str, int | None]:
    if not value:
        return "empty", None

    try:
        inner_len = read_varint(value, 0)
    except MMKVAnalyzeError:
        inner_len = None

    if inner_len and inner_len.end + inner_len.value == len(value):
        inner = value[inner_len.end :]
        try:
            inner.decode("utf-8")
            return "length_delimited_utf8_string_candidate", inner_len.value
        except UnicodeDecodeError:
            return "length_delimited_bytes_candidate", inner_len.value

    if len(value) == 1 and value[0] in (0, 1):
        return "bool_candidate", None

    if len(value) in (4, 8):
        return f"fixed{len(value) * 8}_or_raw_bytes_candidate", None

    try:
        varint = read_varint(value, 0)
        if varint.end == len(value):
            return "varint_candidate", None
    except MMKVAnalyzeError:
        pass

    return "raw_bytes", None


def parse_crc_file(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = path.read_bytes()
    result: dict[str, Any] = {
        "path": str(path),
        "file_size": len(data),
        "format": "unknown",
    }
    if len(data) >= 4:
        result["crc_digest"] = struct.unpack_from("<I", data, 0)[0]
        result["format"] = "mmkv_meta_or_old_crc"
    if len(data) >= 12:
        result["version"] = struct.unpack_from("<I", data, 4)[0]
        result["sequence"] = struct.unpack_from("<I", data, 8)[0]
    if len(data) >= 28:
        result["aes_iv_hex"] = data[12:28].hex()
    if len(data) >= 32:
        result["actual_size"] = struct.unpack_from("<I", data, 28)[0]
        result["format"] = "mmkv_meta_info"
    if len(data) >= 40:
        result["last_actual_size"] = struct.unpack_from("<I", data, 32)[0]
        result["last_crc_digest"] = struct.unpack_from("<I", data, 36)[0]
    return result


def parse_mmkv(data: bytes) -> tuple[dict[str, Any], list[Entry], list[str]]:
    if len(data) < 4:
        raise MMKVAnalyzeError("MMKV file is smaller than 4 bytes")

    actual_size = struct.unpack_from("<I", data, 0)[0]
    if 4 + actual_size > len(data):
        raise MMKVAnalyzeError(
            f"actual size header says {actual_size}, but file only has {max(len(data) - 4, 0)} payload bytes"
        )

    payload = data[4 : 4 + actual_size]
    notes: list[str] = []
    entries: list[Entry] = []
    leading = None
    offset = 0

    if payload:
        leading_varint = read_varint(payload, 0)
        leading = {
            "value": leading_varint.value,
            "encoded_offset": leading_varint.start,
            "encoded_length": leading_varint.end - leading_varint.start,
        }
        offset = leading_varint.end

    index = 0
    while offset < len(payload):
        entry_offset = offset
        key_raw, key_len, offset = read_container(payload, offset, "key")
        value_raw, value_len, offset = read_container(payload, offset, "value")
        key, key_hex = decode_key(key_raw)
        value_hint, value_inner_length = try_inner_container(value_raw)
        entries.append(
            Entry(
                index=index,
                entry_offset=entry_offset,
                entry_end=offset,
                key=key,
                key_hex=key_hex,
                key_len=key_len.value,
                value_len=value_len.value,
                value_sha256_16=hashlib.sha256(value_raw).hexdigest()[:16],
                value_preview_hex=value_raw[:MAX_PREVIEW_BYTES].hex(),
                value_hint=value_hint,
                value_inner_length=value_inner_length,
            )
        )
        index += 1

    summary = {
        "file_size": len(data),
        "actual_size_header": actual_size,
        "payload_start_offset": 4,
        "payload_end_offset": 4 + actual_size,
        "unused_tail_bytes": len(data) - 4 - actual_size,
        "leading_varint": leading,
    }
    return summary, entries, notes


def aggregate_entries(entries: list[Entry]) -> list[dict[str, Any]]:
    buckets: dict[str, list[Entry]] = {}
    for entry in entries:
        buckets.setdefault(entry.key, []).append(entry)

    rows: list[dict[str, Any]] = []
    for key, group in buckets.items():
        latest = group[-1]
        rows.append(
            {
                "key": key,
                "key_hex": latest.key_hex,
                "occurrence_count": len(group),
                "latest_index": latest.index,
                "latest_payload_offset": latest.entry_offset,
                "key_length": latest.key_len,
                "latest_value_length": latest.value_len,
                "total_value_bytes_all_occurrences": sum(item.value_len for item in group),
                "latest_entry_encoded_length": latest.total_encoded_len,
                "all_value_lengths": [item.value_len for item in group],
                "all_payload_offsets": [item.entry_offset for item in group],
                "value_hint": latest.value_hint,
                "value_inner_length": latest.value_inner_length,
                "value_sha256_16": latest.value_sha256_16,
                "value_preview_hex": latest.value_preview_hex,
            }
        )

    rows.sort(key=lambda item: item["latest_index"])
    return rows


def build_report(mmkv_path: Path, crc_path: Path | None) -> dict[str, Any]:
    data = mmkv_path.read_bytes()
    summary, entries, notes = parse_mmkv(data)
    crc_info = parse_crc_file(crc_path)

    computed_crc = zlib.crc32(data[4 : summary["payload_end_offset"]]) & 0xFFFFFFFF
    crc_check: dict[str, Any] = {"computed_payload_crc32": computed_crc}
    if crc_info and "crc_digest" in crc_info:
        crc_check["stored_crc_digest"] = crc_info["crc_digest"]
        crc_check["match"] = computed_crc == crc_info["crc_digest"]
    if crc_info and "actual_size" in crc_info:
        crc_check["crc_actual_size_matches_header"] = crc_info["actual_size"] == summary["actual_size_header"]

    keys = aggregate_entries(entries)
    report = {
        "tool": TOOL_NAME,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mmkv_file": str(mmkv_path),
        "crc_file": str(crc_path) if crc_path else None,
        "summary": {
            **summary,
            "entry_count_in_file": len(entries),
            "unique_key_count": len(keys),
            "duplicate_key_count": sum(1 for item in keys if item["occurrence_count"] > 1),
        },
        "crc": crc_info,
        "crc_check": crc_check,
        "keys": keys,
        "parse_notes": notes,
    }
    return report


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "key",
        "occurrence_count",
        "latest_index",
        "latest_payload_offset",
        "key_length",
        "latest_value_length",
        "total_value_bytes_all_occurrences",
        "latest_entry_encoded_length",
        "all_value_lengths",
        "all_payload_offsets",
        "value_hint",
        "value_inner_length",
        "value_sha256_16",
        "value_preview_hex",
        "key_hex",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            rendered = dict(row)
            rendered["all_value_lengths"] = json.dumps(row["all_value_lengths"], ensure_ascii=False)
            rendered["all_payload_offsets"] = json.dumps(row["all_payload_offsets"], ensure_ascii=False)
            writer.writerow(rendered)


def write_text_summary(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    crc_check = report["crc_check"]
    lines = [
        f"Tool: {report['tool']}",
        f"Generated: {report['generated_at']}",
        f"MMKV: {report['mmkv_file']}",
        f"CRC: {report['crc_file'] or '(not provided)'}",
        "",
        "Summary:",
        f"  file_size: {summary['file_size']}",
        f"  actual_size_header: {summary['actual_size_header']}",
        f"  unused_tail_bytes: {summary['unused_tail_bytes']}",
        f"  entry_count_in_file: {summary['entry_count_in_file']}",
        f"  unique_key_count: {summary['unique_key_count']}",
        f"  duplicate_key_count: {summary['duplicate_key_count']}",
        "",
        "CRC:",
        f"  computed_payload_crc32: 0x{crc_check['computed_payload_crc32']:08x}",
    ]
    if "stored_crc_digest" in crc_check:
        lines.append(f"  stored_crc_digest: 0x{crc_check['stored_crc_digest']:08x}")
        lines.append(f"  match: {crc_check['match']}")
    if "crc_actual_size_matches_header" in crc_check:
        lines.append(f"  crc_actual_size_matches_header: {crc_check['crc_actual_size_matches_header']}")

    lines.extend(["", "Keys:"])
    for row in report["keys"]:
        lines.append(
            "  "
            f"[{row['latest_index']}] {row['key']} "
            f"value_len={row['latest_value_length']} "
            f"key_len={row['key_length']} "
            f"occurrences={row['occurrence_count']} "
            f"offset={row['latest_payload_offset']} "
            f"hint={row['value_hint']}"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_mmkv_files(root: Path) -> list[tuple[Path, Path | None]]:
    candidates: list[tuple[int, str, Path, Path | None]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name_lower = path.name.lower()
        if name_lower.endswith(SKIP_SUFFIXES_IN_DIR_MODE):
            continue

        crc_path = Path(str(path) + ".crc")
        score = 0
        if crc_path.is_file():
            score += 100
        if path.suffix.lower() in ("", ".mmkv"):
            score += 10
        if name_lower.startswith(("log", "report")):
            score -= 10

        if score <= 0:
            continue
        candidates.append((score, str(path.relative_to(root)).lower(), path, crc_path if crc_path.is_file() else None))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [(path, crc_path) for _, _, path, crc_path in candidates]


def write_batch_index(out_dir: Path, batch_base: str, rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    json_path = out_dir / f"{batch_base}_index.json"
    csv_path = out_dir / f"{batch_base}_index.csv"
    write_json(
        json_path,
        {
            "tool": TOOL_NAME,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(rows),
            "items": rows,
        },
    )

    fieldnames = [
        "status",
        "mmkv_file",
        "crc_file",
        "unique_key_count",
        "entry_count_in_file",
        "crc_match",
        "json_report",
        "csv_report",
        "text_summary",
        "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    return json_path, csv_path


def analyze_one_file(mmkv_path: Path, crc_path: Path | None, out_dir: Path, stamp: str) -> dict[str, Any]:
    report = build_report(mmkv_path, crc_path)
    base = f"{safe_name(mmkv_path.name)}_{stamp}"
    json_path = out_dir / f"{base}.json"
    csv_path = out_dir / f"{base}_keys.csv"
    txt_path = out_dir / f"{base}_summary.txt"

    write_json(json_path, report)
    write_csv(csv_path, report["keys"])
    write_text_summary(txt_path, report)

    return {
        "status": "ok",
        "mmkv_file": str(mmkv_path),
        "crc_file": str(crc_path) if crc_path else "",
        "unique_key_count": report["summary"]["unique_key_count"],
        "entry_count_in_file": report["summary"]["entry_count_in_file"],
        "crc_match": report["crc_check"].get("match", ""),
        "json_report": str(json_path),
        "csv_report": str(csv_path),
        "text_summary": str(txt_path),
        "error": "",
    }


def default_output_dir() -> Path:
    return DEFAULT_LOG_DIR


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a Tencent MMKV data file, or scan a directory and analyze every MMKV file found."
    )
    parser.add_argument("input", type=Path, help="MMKV main data file, or a directory containing MMKV files")
    parser.add_argument(
        "crc_file",
        nargs="?",
        type=Path,
        help="MMKV .crc metadata file for single-file mode. Optional, but recommended for CRC and meta checks.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=default_output_dir(),
        help=f"report output directory. Default on Windows: {DEFAULT_LOG_DIR}",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    input_path = args.input.resolve()
    crc_path = args.crc_file.resolve() if args.crc_file else None
    out_dir = ensure_dir(args.output_dir)
    stamp = now_stamp()

    if input_path.is_dir():
        if crc_path is not None:
            raise MMKVAnalyzeError("directory mode does not accept a separate crc_file argument")

        targets = find_mmkv_files(input_path)
        if not targets:
            raise MMKVAnalyzeError(f"no MMKV candidates found in directory: {input_path}")

        info(f"found {len(targets)} MMKV candidate(s) in {input_path}")
        rows: list[dict[str, Any]] = []
        for index, (mmkv_path, paired_crc_path) in enumerate(targets, start=1):
            try:
                row = analyze_one_file(mmkv_path, paired_crc_path, out_dir, f"{stamp}_{index:03d}")
                rows.append(row)
                info(
                    f"[{index}/{len(targets)}] ok: {mmkv_path.name}, "
                    f"keys={row['unique_key_count']}, entries={row['entry_count_in_file']}"
                )
            except Exception as exc:
                rows.append(
                    {
                        "status": "error",
                        "mmkv_file": str(mmkv_path),
                        "crc_file": str(paired_crc_path) if paired_crc_path else "",
                        "unique_key_count": "",
                        "entry_count_in_file": "",
                        "crc_match": "",
                        "json_report": "",
                        "csv_report": "",
                        "text_summary": "",
                        "error": str(exc),
                    }
                )
                info(f"[{index}/{len(targets)}] error: {mmkv_path.name}: {exc}")

        index_json, index_csv = write_batch_index(out_dir, f"batch_{safe_name(input_path.name)}_{stamp}", rows)
        ok_count = sum(1 for row in rows if row["status"] == "ok")
        error_count = len(rows) - ok_count
        info(f"batch ok: {ok_count}, errors: {error_count}")
        info(f"batch json index: {index_json}")
        info(f"batch csv index: {index_csv}")
        return 0 if ok_count else 1

    if not input_path.is_file():
        raise MMKVAnalyzeError(f"MMKV file or directory does not exist: {input_path}")
    if crc_path and not crc_path.is_file():
        raise MMKVAnalyzeError(f"CRC file does not exist: {crc_path}")

    row = analyze_one_file(input_path, crc_path, out_dir, stamp)
    info(f"unique keys: {row['unique_key_count']}")
    info(f"entries in file: {row['entry_count_in_file']}")
    if row["crc_match"] != "":
        info(f"crc match: {row['crc_match']}")
    info(f"json report: {row['json_report']}")
    info(f"csv report: {row['csv_report']}")
    info(f"text summary: {row['text_summary']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except MMKVAnalyzeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
