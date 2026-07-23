import os
import itertools
from dotenv import load_dotenv

try:
    import httpx
    from supabase import create_client, ClientOptions
except ModuleNotFoundError:
    create_client = None

from pathlib import Path

from .classes import Vulnerability
from .logging_utils import get_file_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "supabase_function.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


REVIEWERS = [
    "Edgar",
    "Hunter",
    "Evan",
    "Lachlan",
    "Dolan",
    "Briana",
]
_reviewer_cycle = itertools.cycle(REVIEWERS)


def _next_person() -> str:
    """
    Return the next reviewer, cycling through the 6-person REVIEWERS list
    """
    return next(_reviewer_cycle)


def has_supabase_creds() -> bool:
    """Return True only if both SUPABASE_URL and SUPABASE_KEY are present in env.

    Reads fresh from os.environ so it stays correct even if the variables are
    set or unset after this module is first imported. Used by callers as a
    gate on whether DB-writing code paths should run at all.
    """
    return bool(os.environ.get("SUPABASE_URL")) and bool(os.environ.get("SUPABASE_KEY"))


supabase = (
    create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
        options=ClientOptions(httpx_client=httpx.Client()),
    )
    if create_client is not None and has_supabase_creds()
    else None
)


def _norm(s: str) -> str:
    """Normalize a string by stripping whitespace and converting to lowercase."""
    return (s or "").strip().lower()


def load_cite(
    site_name: str,
    vuln_table: str = "vulnerabilities",
    noise_table: str | None = "noise",
) -> list[dict[str, str]]:
    """Query articles by source, optionally including noise entries for deduplication.

    Args:
        site_name: Source name to filter articles by.
        vuln_table: Vulnerabilities table name (default: "vulnerabilities").
        noise_table: Noise table name to include; None to exclude noise entries.

    Returns:
        List of articles with keys "title" and "content".
    """
    vuln_data = (
        supabase.table(vuln_table)
        .select("title,content")
        .eq("source_name", site_name)
        .execute()
        .data
    )
    if noise_table:
        noise_data = (
            supabase.table(noise_table)
            .select("title,body_preview")
            .eq("source_name", site_name)
            .execute()
            .data
        )
        vuln_data += [
            {"title": r["title"], "content": r["body_preview"]} for r in noise_data
        ]
    return vuln_data


def is_known_db(
    site_query: list[dict[str, str]], title: str, body_snippet: str
) -> bool:
    """Check if an article exists in the query results (for deduplication).

    Performs case-insensitive title matching and checks if the body snippet exists
    in the article content.

    Args:
        site_query: Query results from load_cite.
        title: Article title to match.
        body_snippet: Text snippet to find in the article content.

    Returns:
        True if a matching article is found, False otherwise.
    """
    return any(
        _norm(row["title"]) == _norm(title) and body_snippet in (row["content"] or "")
        for row in site_query
    )


def insert_vuln(
    vuln: Vulnerability,
    embedding: list[float] | None = None,
    table: str = "vulnerabilities",
) -> dict:
    """Insert a vulnerability article into the database.

    Args:
        vuln: Vulnerability object to insert.
        embedding: Optional 384-dim embedding to write to the ``embedding``
            column. Pass the value produced by src/dedup.py so the new row is
            usable for future nearest-neighbor dedup lookups.
        table: Table name (default: "vulnerabilities").

    Returns:
        Inserted record with generated ID and metadata.
    """
    payload = vuln.to_dict()
    payload.pop("id", None)  # let Postgres generate it
    if embedding is not None:
        payload["embedding"] = embedding
    response = supabase.table(table).insert(payload).execute()
    LOGGER.debug(
        "Inserted vulnerability '%s' into table %s with ID %s",
        vuln.title,
        table,
        response.data[0].get("id"),
    )
    return response.data[0]


def insert_duplicate(
    vuln: Vulnerability, embedding: list[float], foreign_key: str
) -> dict:
    """Insert a duplicated vulnerability article into the 'duplicates' table

    Args:
        vuln: Vulnerability object to insert.
        embedding: 384-dim embedding from the value produced by src/dedup.py
        foreign_key: foreig key of the ORIGINAL Vulnerability object

    Returns:
        Inserted record with generated ID and metadata.
    """
    payload = vuln.to_dict()
    payload.pop("id", None)  # let Postgres generate it
    payload["embedding"] = embedding
    payload["original_vulnerability_id"] = foreign_key
    response = supabase.table("duplicates").insert(payload).execute()
    return response.data[0]


def find_nearest_vulnerability(
    embedding: list[float],
) -> tuple[str, str, float] | None:
    """
    Return the nearest existing vulnerability by cosine distance (if any).
    Calls the "match_vulnerability" Postgres RPC (from src/config/dedup_rpc.sql)
    because PostgREST does not expose pgvector's operator (like "<=>").

    Returns:
        id: UUID of closet Vulnerability
        subsector: We want to make sure the subsectors match
        distance: Distance used to determine outcome
    Or:
        "None" when table is empty / no row have embeddings
    """
    resp = supabase.rpc("match_vulnerability", {"query_embedding": embedding}).execute()
    rows = resp.data or []
    if not rows:
        return None
    r = rows[0]
    return (r["id"], r["subsector"], float(r["distance"]))


def insert_noise(
    source_name: str,
    title: str,
    url: str,
    reason: str,
    body_preview: str,
    date_accessed: str,
    table: str = "noise",
) -> dict:
    """Insert a noise (non-relevant) article into the exclusion list.

    Args:
        source_name: Article source name.
        title: Article title.
        url: Article URL.
        reason: Reason for marking as noise (from LLM).
        body_preview: Preview of the article content.
        date_accessed: ISO timestamp when the article was accessed.
        table: Table name (default: "noise").

    Returns:
        Inserted record with generated ID and metadata.
    """
    payload = {
        "source_name": source_name,
        "title": title,
        "url": url,
        "reason": reason,
        "body_preview": body_preview,
        "date_accessed": date_accessed,
    }
    response = supabase.table(table).insert(payload).execute()
    LOGGER.debug(
        "Inserted noise article '%s' into table %s with ID %s",
        title,
        table,
        response.data[0].get("id"),
    )
    return response.data[0]


def push_lablr(
    raw_record: dict,
) -> None:
    """ """
    rec = {
        "id": raw_record["id"],
        "reviewed": False,
        "reviewer": _next_person(),
        "reclassified": False,
        "vulnerability": False,
    }
    try:
        supabase.table("lablr").insert(rec).execute()
    except Exception as e:
        LOGGER.warning("Failed to push lablr row for id %s: %s", raw_record["id"], e)


def push_vulnerabilities(
    records: list[dict],
    table: str = "vulnerability",
) -> int:
    """
    Bulk-insert vulnerability dicts into Supabase.

    Expects records already shaped like ``Vulnerability.to_dict()`` (including
    ``id``). Used by the orchestrator after normalize + local dedup.

    Args:
        records: Vulnerability dicts to insert.
        table: Destination table (default: ``vulnerability``).

    Returns:
        Number of records submitted for insert.
    """
    if not records:
        return 0
    if supabase is None:
        raise RuntimeError("Supabase client not configured")

    _counter = 0
    for rec in records:
        try:
            response = supabase.table(table).insert(rec).execute()
            push_lablr(rec)

        except Exception as e:
            _counter += 1
            LOGGER.warning("We trying to insert this: % ", rec)

    # supabase.table(table).insert(records).execute()
    LOGGER.info("Pushed %s records to %s", len(records), table)
    if _counter > 0:
        LOGGER.warning("Did not push %s records (failed)", _counter)

    return len(records)
