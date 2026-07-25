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
            supabase.table(table).insert(rec).execute()
            push_lablr(rec)

        except Exception as e:
            _counter += 1
            LOGGER.warning("Exception %  ", e)

    # supabase.table(table).insert(records).execute()
    LOGGER.info("Pushed %s records to %s", len(records), table)
    if _counter > 0:
        LOGGER.warning("Did not push %s records (failed)", _counter)

    return len(records)
