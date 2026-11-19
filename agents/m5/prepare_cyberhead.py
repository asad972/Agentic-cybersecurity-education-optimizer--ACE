"""Prepare a CYBERHEAD Word table for review and M5 ingestion."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}
_COUNTRIES = {
    "austria": "AT",
    "osterreich": "AT",
    "belgium": "BE",
    "belgien": "BE",
    "denmark": "DK",
    "danmark": "DK",
    "danemark": "DK",
    "deutschland": "DE",
    "germany": "DE",
    "finland": "FI",
    "finnland": "FI",
    "greece": "GR",
    "griechenland": "GR",
    "ireland": "IE",
    "irland": "IE",
    "italy": "IT",
    "italien": "IT",
    "latvia": "LV",
    "lettland": "LV",
    "luxembourg": "LU",
    "luxemburg": "LU",
    "malta": "MT",
    "poland": "PL",
    "polen": "PL",
    "portugal": "PT",
    "romania": "RO",
    "rumanien": "RO",
    "sweden": "SE",
    "schweden": "SE",
    "slovakia": "SK",
    "slowakei": "SK",
    "slovenia": "SI",
    "slowenien": "SI",
    "spain": "ES",
    "spanien": "ES",
    "cyprus": "CY",
    "zypern": "CY",
}
_HEADERS = {
    "name": {"name", "titel", "programmname"},
    "level": {"abschlussgrad", "level", "degree"},
    "programme_id": {"programmeid", "porgrammeid", "programid", "id"},
    "institution": {"institution", "hochschule", "anbieter"},
    "country": {"land", "country"},
    "url": {"url", "institutionsurl", "sourceurl"},
    "curriculum": {
        "lehrplanskillinformation",
        "lehrplanskillsinformation",
        "lehrplanskills",
        "curriculum",
        "skills",
    },
    "compatible_roles": {"kompatiblerollen", "compatibleroles", "roles"},
    "cyberhead_url": {"cyberheadurl", "enisaurl"},
}
_REQUIRED = ("name", "level", "programme_id", "institution", "country", "url")


def _key(value: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    return re.sub(r"[^a-z0-9]", "", ascii_text)


def _cell_text(cell: ElementTree.Element) -> str:
    paragraphs = []
    for paragraph in cell.findall(".//w:p", _NS):
        paragraphs.append(
            "".join(
                node.text or ""
                for node in paragraph.findall(".//w:t", _NS)
            )
        )
    return " ".join(part.strip() for part in paragraphs if part.strip()).strip()


def read_docx_rows(path: Path) -> list[dict[str, str]]:
    """Read the first Word table whose header contains the required columns."""
    try:
        with zipfile.ZipFile(path) as document:
            xml = document.read("word/document.xml")
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise ValueError(f"cannot read a DOCX document: {path}") from error

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise ValueError(f"invalid Word document XML: {path}") from error

    for table in root.findall(".//w:tbl", _NS):
        rows = table.findall("./w:tr", _NS)
        if not rows:
            continue
        header_cells = [_cell_text(cell) for cell in rows[0].findall("./w:tc", _NS)]
        header_map = {}
        for index, header in enumerate(header_cells):
            normalized = _key(header)
            for field, aliases in _HEADERS.items():
                if normalized in aliases:
                    header_map[field] = index
                    break
        if not all(field in header_map for field in _REQUIRED):
            continue

        records = []
        for row_number, row in enumerate(rows[1:], start=2):
            cells = [_cell_text(cell) for cell in row.findall("./w:tc", _NS)]
            if not any(cells):
                continue
            records.append(
                {
                    field: cells[index] if index < len(cells) else ""
                    for field, index in header_map.items()
                }
                | {"_row": str(row_number)}
            )
        return records
    raise ValueError("no table with the required CYBERHEAD columns was found")


def _country_code(country: str) -> str | None:
    return _COUNTRIES.get(_key(country))


def _level(value: str) -> str:
    normalized = _key(value)
    if normalized.startswith(("bsc", "bachelor", "bachelors")):
        return "bachelor"
    if normalized.startswith(("msc", "master", "masters")):
        return "master"
    if normalized.startswith(("phd", "doctor", "doctoral")):
        return "phd"
    return "other"


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def prepare_records(
    source_rows: list[dict[str, str]], accessed_on: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return audit records and only country-valid records for M5."""
    if accessed_on is not None:
        try:
            parsed_date = date.fromisoformat(accessed_on)
        except ValueError as error:
            raise ValueError("accessed_on must be an ISO 8601 date") from error
        if parsed_date.isoformat() != accessed_on:
            raise ValueError("accessed_on must use YYYY-MM-DD format")

    review_records = []
    m5_records = []
    seen_ids: set[str] = set()
    for row in source_rows:
        errors = []
        warnings = []
        record = {field: row.get(field, "").strip() for field in _HEADERS}
        record["row"] = int(row["_row"])
        for field in _REQUIRED:
            if not record[field]:
                errors.append(f"missing {field}")
        record["country_code"] = _country_code(record["country"])
        if record["country"] and record["country_code"] is None:
            errors.append(
                "country is not a recognized country name; regional entries "
                "cannot be published as country records"
            )
        if record["url"] and not _valid_url(record["url"]):
            errors.append("url must be an absolute http(s) URL")
        if record["cyberhead_url"] and not _valid_url(record["cyberhead_url"]):
            errors.append("cyberhead_url must be an absolute http(s) URL")
        if record["programme_id"]:
            if record["programme_id"] in seen_ids:
                errors.append("duplicate programme_id")
            seen_ids.add(record["programme_id"])
        if not record["cyberhead_url"]:
            warnings.append("CYBERHEAD detail-page URL was not provided")
        if accessed_on is None:
            warnings.append("access date was not provided")

        record["level_normalized"] = _level(record["level"])
        record["ecsf_roles"] = []
        record["accessed_on"] = accessed_on
        record["validation_errors"] = errors
        record["warnings"] = warnings
        review_records.append(record)

        if not errors:
            m5_records.append(
                {
                    "programme_id": record["programme_id"],
                    "country": record["country_code"],
                    "name": record["name"],
                    "institution": record["institution"],
                    "level": record["level_normalized"],
                    "ecsf_roles": [],
                    "url": record["url"],
                }
            )
    return review_records, m5_records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="CYBERHEAD .docx table")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--accessed-on",
        help="verified retrieval date in YYYY-MM-DD format; never guessed",
    )
    args = parser.parse_args(argv)

    rows = read_docx_rows(args.input)
    review_records, m5_records = prepare_records(rows, args.accessed_on)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    review_path = args.output_dir / "cyberhead_review.jsonl"
    programmes_path = args.output_dir / "programmes.jsonl"
    if review_path.exists() or programmes_path.exists():
        raise FileExistsError(
            f"output files already exist in {args.output_dir}; choose a new directory"
        )
    _write_jsonl(review_path, review_records)
    _write_jsonl(programmes_path, m5_records)

    invalid_count = len(review_records) - len(m5_records)
    print(f"Review rows: {len(review_records)}")
    print(f"Country-valid M5 rows: {len(m5_records)}")
    print(f"Rows excluded from M5: {invalid_count}")
    print(f"Review file: {review_path}")
    print(f"M5 input file: {programmes_path}")
    if args.accessed_on is None:
        print("Warning: retrieval date was not supplied.", file=sys.stderr)
    if any(not row["cyberhead_url"] for row in review_records):
        print(
            "Warning: add CYBERHEAD detail-page URLs to the review data "
            "where available.",
            file=sys.stderr,
        )
    if invalid_count:
        print(
            "Warning: inspect validation_errors in cyberhead_review.jsonl; "
            "invalid rows were not included in programmes.jsonl.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
