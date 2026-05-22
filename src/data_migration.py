"""Generate Supabase-ready SQL from the local CSV/JSON corpus.

Two public entry points:

- `vulnerabilities_to_sql()` reads every `data/processed/*.json` file (each
  shaped as ``{"sources": [Vulnerability...]}``) and writes
  `sql/vulnerabilities.sql`.
- `noise_to_sql()` reads every `data/noise/*.csv` file (header
  ``date_accessed,source_name,title,url,reason,body_preview``) and writes
  `sql/noise.sql`.

Both target the schema in `src/config/schema.sql`. Rows that would violate a
NOT NULL / non-empty / subsector CHECK constraint are skipped with a
`[SKIP]` line printed to stdout. Every emitted INSERT carries
``ON CONFLICT (...) DO NOTHING`` so the script is safe to re-run.

Run from the venv:

    python src/data_migration.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

PROCESSED_DIR = _PROJECT_ROOT / "data" / "processed"
NOISE_DIR = _PROJECT_ROOT / "data" / "noise"
SQL_DIR = _PROJECT_ROOT / "sql"
VULN_SQL_PATH = SQL_DIR / "vulnerabilities.sql"
NOISE_SQL_PATH = SQL_DIR / "noise.sql"

ALLOWED_SUBSECTORS = {
    "drug_shortage",
    "medical_device_shortage",
    "cyber_attack",
    "natural_disaster",
    "other",
}

VULN_COLUMNS: tuple[str, ...] = (
    "source_name",
    "title",
    "direct_link",
    "subsector",
    "date_accessed",
    "date_published",
    "content",
    "exec_summary",
    "geography_scope",
    "start_date",
    "end_date",
    "resilience_or_mitigation_observed",
    "subsector_data",
)

NOISE_COLUMNS: tuple[str, ...] = (
    "source_name",
    "title",
    "url",
    "reason",
    "body_preview",
    "date_accessed",
)

# Cap rows per multi-row INSERT so a single statement stays small enough to
# paste comfortably into the Supabase SQL editor.
_BATCH_SIZE = 100


def _skip_with_warning(file_label: str, reason: str, title: str) -> None:
    """Uniform skip log line for rows we refuse to emit."""
    snippet = (title or "").strip().replace("\n", " ")[:80]
    print(f"[SKIP] {file_label}: {reason}: {snippet}")


def _pick_dollar_tag(body: str) -> str:
    """Pick a dollar-quote tag that doesn't collide with the body."""
    base = "mig"
    if f"${base}$" not in body:
        return base
    i = 1
    while f"${base}{i}$" in body:
        i += 1
    return f"{base}{i}"


def _quote_literal(value: object) -> str:
    """Render any scalar as a Postgres SQL literal using dollar-quoted strings.

    Handles single quotes, backslashes and newlines without escaping. Returns
    the literal ``NULL`` for ``None``.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    tag = _pick_dollar_tag(text)
    return f"${tag}${text}${tag}$"


def _quote_jsonb(obj: object) -> str:
    """Render any JSON-serializable object as a ``jsonb`` literal."""
    if obj is None:
        return "'{}'::jsonb"
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    tag = _pick_dollar_tag(payload)
    return f"${tag}${payload}${tag}$::jsonb"


def _normalize_date(value: object) -> str | None:
    """Return a ``YYYY-MM-DD`` string or ``None`` for the schema's ``date`` column.

    Accepts plain ISO dates, full ISO timestamps (``2023-12-01T00:00:00Z``) and
    space-separated timestamps. Empty / unparseable input returns ``None``.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    head = text.split("T", 1)[0].split(" ", 1)[0]
    parts = head.split("-")
    if len(parts) != 3:
        return None
    y, m, d = parts
    if not (y.isdigit() and m.isdigit() and d.isdigit()):
        return None
    if not (len(y) == 4 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31):
        return None
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _quote_date(value: object) -> str:
    """Render a value as a ``date`` SQL literal, or ``NULL``."""
    normalized = _normalize_date(value)
    if normalized is None:
        return "NULL"
    return f"'{normalized}'::date"


def _quote_timestamptz(value: object) -> str | None:
    """Render a value as a ``timestamptz`` SQL literal.

    Returns ``None`` (caller should omit the column) for empty / missing input
    so the schema default ``now()`` fires.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    body = text.replace("T", " ").rstrip("Z").strip()
    if text.endswith("Z") or "+" in body or body.endswith("UTC"):
        body = body.replace("UTC", "").strip()
        return f"'{body}+00'::timestamptz"
    return f"'{body}+00'::timestamptz"


def _build_row_sql(
    columns: tuple[str, ...],
    column_values: dict[str, str],
) -> str:
    """Format a single row tuple from a column->literal mapping."""
    return "(" + ", ".join(column_values[c] for c in columns) + ")"


def _write_inserts(
    out_handle,
    table: str,
    columns: tuple[str, ...],
    conflict_target: str,
    file_groups: list[tuple[str, list[str]]],
) -> int:
    """Emit batched ``INSERT ... ON CONFLICT DO NOTHING`` blocks.

    Each ``file_groups`` entry is ``(file_label, [row_tuple_sql, ...])``.
    Rows are split into chunks of ``_BATCH_SIZE`` to keep a single statement
    readable in the SQL editor.
    """
    columns_clause = ",\n   ".join(
        ", ".join(columns[i : i + 4]) for i in range(0, len(columns), 4)
    )
    total = 0
    for file_label, rows in file_groups:
        if not rows:
            continue
        out_handle.write(f"\n-- {file_label} (n={len(rows)})\n")
        for start in range(0, len(rows), _BATCH_SIZE):
            chunk = rows[start : start + _BATCH_SIZE]
            out_handle.write(f"INSERT INTO {table}\n  ({columns_clause})\nVALUES\n")
            out_handle.write(",\n".join("  " + r for r in chunk))
            out_handle.write(f"\nON CONFLICT {conflict_target} DO NOTHING;\n")
        total += len(rows)
    return total


def _vuln_row_to_sql(
    record: dict,
    file_label: str,
) -> tuple[str, tuple[str, ...]] | None:
    """Build one VALUES tuple for a vulnerability record.

    Returns a tuple ``(values_sql, columns_used)`` or ``None`` if the row was
    skipped. ``columns_used`` is needed because we may omit ``date_accessed``
    so the schema default fires; rows with omitted columns end up in their
    own batch.
    """
    title = (record.get("title") or "").strip()
    direct_link = (record.get("direct_link") or "").strip()
    source_name = (record.get("source_name") or "").strip()
    subsector = (record.get("subsector") or "").strip()

    if not title:
        _skip_with_warning(file_label, "empty title", title)
        return None
    if not direct_link:
        _skip_with_warning(file_label, "empty direct_link", title)
        return None
    if not source_name:
        _skip_with_warning(file_label, "empty source_name", title)
        return None
    if subsector not in ALLOWED_SUBSECTORS:
        _skip_with_warning(
            file_label, f"subsector '{subsector}' not allowed", title
        )
        return None

    start_norm = _normalize_date(record.get("start_date"))
    end_norm = _normalize_date(record.get("end_date"))
    if start_norm and end_norm and end_norm < start_norm:
        end_norm = None

    date_accessed_lit = _quote_timestamptz(record.get("date_accessed"))

    values: dict[str, str] = {
        "source_name": _quote_literal(source_name),
        "title": _quote_literal(title),
        "direct_link": _quote_literal(direct_link),
        "subsector": _quote_literal(subsector),
        "date_published": _quote_literal(record.get("date_published")),
        "content": _quote_literal(record.get("content")),
        "exec_summary": _quote_literal(record.get("exec_summary") or ""),
        "geography_scope": _quote_literal(record.get("geography_scope")),
        "start_date": (
            f"'{start_norm}'::date" if start_norm else "NULL"
        ),
        "end_date": f"'{end_norm}'::date" if end_norm else "NULL",
        "resilience_or_mitigation_observed": _quote_literal(
            record.get("resilience_or_mitigation_observed")
        ),
        "subsector_data": _quote_jsonb(record.get("subsector_data") or {}),
    }

    if date_accessed_lit is not None:
        values["date_accessed"] = date_accessed_lit
        columns = VULN_COLUMNS
    else:
        columns = tuple(c for c in VULN_COLUMNS if c != "date_accessed")

    return _build_row_sql(columns, values), columns


def vulnerabilities_to_sql() -> None:
    """Read every ``data/processed/*.json`` and write ``sql/vulnerabilities.sql``."""
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    if not PROCESSED_DIR.exists():
        print(f"[ERROR] {PROCESSED_DIR} does not exist; nothing to migrate")
        return

    json_files = sorted(PROCESSED_DIR.glob("*.json"))
    if not json_files:
        print(f"[WARN] No *.json files under {PROCESSED_DIR}")

    # Group rows by (file_label, columns_used) so each multi-row INSERT has
    # a uniform column list.
    groups_full: list[tuple[str, list[str]]] = []
    groups_no_da: list[tuple[str, list[str]]] = []
    columns_no_da = tuple(c for c in VULN_COLUMNS if c != "date_accessed")
    skipped_total = 0
    valid_total = 0

    for path in json_files:
        file_label = path.name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Could not read {path}: {exc}")
            continue

        sources = payload.get("sources", []) if isinstance(payload, dict) else []
        if not isinstance(sources, list):
            print(f"[ERROR] {file_label}: 'sources' is not a list, skipping file")
            continue

        rows_full: list[str] = []
        rows_no_da: list[str] = []
        for record in sources:
            if not isinstance(record, dict):
                _skip_with_warning(file_label, "non-object record", "")
                skipped_total += 1
                continue
            built = _vuln_row_to_sql(record, file_label)
            if built is None:
                skipped_total += 1
                continue
            row_sql, columns_used = built
            if columns_used == VULN_COLUMNS:
                rows_full.append(row_sql)
            else:
                rows_no_da.append(row_sql)

        if rows_full:
            groups_full.append((file_label, rows_full))
        if rows_no_da:
            groups_no_da.append((file_label + " (default date_accessed)", rows_no_da))
        valid_total += len(rows_full) + len(rows_no_da)

    with open(VULN_SQL_PATH, "w", encoding="utf-8") as out:
        out.write("-- sql/vulnerabilities.sql\n")
        out.write("-- Auto-generated by src/data_migration.py — do not edit by hand.\n")
        out.write("-- Source: data/processed/*.json\n")
        out.write(f"-- Rows: {valid_total} valid, {skipped_total} skipped\n\n")
        out.write("BEGIN;\n")
        _write_inserts(
            out,
            table="public.vulnerabilities",
            columns=VULN_COLUMNS,
            conflict_target="(source_name, direct_link)",
            file_groups=groups_full,
        )
        _write_inserts(
            out,
            table="public.vulnerabilities",
            columns=columns_no_da,
            conflict_target="(source_name, direct_link)",
            file_groups=groups_no_da,
        )
        out.write("\nCOMMIT;\n")

    print(
        f"[OK] Wrote {VULN_SQL_PATH.relative_to(_PROJECT_ROOT)}: "
        f"{valid_total} row(s), {skipped_total} skipped"
    )


def _noise_row_to_sql(
    row: dict,
    file_label: str,
) -> tuple[str, tuple[str, ...]] | None:
    """Build one VALUES tuple for a noise CSV row."""
    source_name = (row.get("source_name") or "").strip()
    url = (row.get("url") or "").strip()
    title = row.get("title") or ""

    if not source_name:
        _skip_with_warning(file_label, "empty source_name", title)
        return None
    if not url:
        _skip_with_warning(file_label, "empty url", title)
        return None

    date_accessed_lit = _quote_timestamptz(row.get("date_accessed"))

    values: dict[str, str] = {
        "source_name": _quote_literal(source_name),
        "title": _quote_literal(title),
        "url": _quote_literal(url),
        "reason": _quote_literal(row.get("reason")),
        "body_preview": _quote_literal(row.get("body_preview")),
    }

    if date_accessed_lit is not None:
        values["date_accessed"] = date_accessed_lit
        columns = NOISE_COLUMNS
    else:
        columns = tuple(c for c in NOISE_COLUMNS if c != "date_accessed")

    return _build_row_sql(columns, values), columns


def noise_to_sql() -> None:
    """Read every ``data/noise/*.csv`` and write ``sql/noise.sql``."""
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    if not NOISE_DIR.exists():
        print(f"[ERROR] {NOISE_DIR} does not exist; nothing to migrate")
        return

    csv_files = sorted(NOISE_DIR.glob("*.csv"))
    if not csv_files:
        print(f"[WARN] No *.csv files under {NOISE_DIR}")

    groups_full: list[tuple[str, list[str]]] = []
    groups_no_da: list[tuple[str, list[str]]] = []
    columns_no_da = tuple(c for c in NOISE_COLUMNS if c != "date_accessed")
    skipped_total = 0
    valid_total = 0

    for path in csv_files:
        file_label = path.name
        rows_full: list[str] = []
        rows_no_da: list[str] = []
        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    built = _noise_row_to_sql(row, file_label)
                    if built is None:
                        skipped_total += 1
                        continue
                    row_sql, columns_used = built
                    if columns_used == NOISE_COLUMNS:
                        rows_full.append(row_sql)
                    else:
                        rows_no_da.append(row_sql)
        except OSError as exc:
            print(f"[ERROR] Could not read {path}: {exc}")
            continue

        if rows_full:
            groups_full.append((file_label, rows_full))
        if rows_no_da:
            groups_no_da.append((file_label + " (default date_accessed)", rows_no_da))
        valid_total += len(rows_full) + len(rows_no_da)

    with open(NOISE_SQL_PATH, "w", encoding="utf-8") as out:
        out.write("-- sql/noise.sql\n")
        out.write("-- Auto-generated by src/data_migration.py — do not edit by hand.\n")
        out.write("-- Source: data/noise/*.csv\n")
        out.write(f"-- Rows: {valid_total} valid, {skipped_total} skipped\n\n")
        out.write("BEGIN;\n")
        _write_inserts(
            out,
            table="public.noise",
            columns=NOISE_COLUMNS,
            conflict_target="(source_name, url)",
            file_groups=groups_full,
        )
        _write_inserts(
            out,
            table="public.noise",
            columns=columns_no_da,
            conflict_target="(source_name, url)",
            file_groups=groups_no_da,
        )
        out.write("\nCOMMIT;\n")

    print(
        f"[OK] Wrote {NOISE_SQL_PATH.relative_to(_PROJECT_ROOT)}: "
        f"{valid_total} row(s), {skipped_total} skipped"
    )


if __name__ == "__main__":
    vulnerabilities_to_sql()
    noise_to_sql()
