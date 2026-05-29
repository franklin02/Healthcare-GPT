import os
from dotenv import load_dotenv
from supabase import create_client
from src.classes import Vulnerability

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


def has_supabase_creds() -> bool:
    """Return True only if both SUPABASE_URL and SUPABASE_KEY are present in env.

    Reads fresh from os.environ so it stays correct even if the variables are
    set or unset after this module is first imported. Used by callers as a
    gate on whether DB-writing code paths should run at all.
    """
    return bool(os.environ.get("SUPABASE_URL")) and bool(os.environ.get("SUPABASE_KEY"))


# Module imports cleanly even when creds are missing 
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if has_supabase_creds() else None


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
    return response.data[0]


def insert_duplicate(
    vuln: Vulnerability,
    embedding: list[float],
    foreign_key: str
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


def find_vuln_by_canonical_url(
    canonical_url: str,
    table: str = "vulnerabilities",
) -> tuple[str, str] | None:
    """Exact-match lookup on direct_link. Cheap pre-filter for dedup."""
    if not has_supabase_creds() or not canonical_url:
        return None
    rows = (
        supabase.table(table)
        .select("id,subsector")
        .eq("direct_link", canonical_url)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    return (rows[0]["id"], rows[0]["subsector"])


def find_vuln_by_normalized_title(
    normalized_title: str,
    table: str = "vulnerabilities",
) -> tuple[str, str] | None:
    """Case-insensitive title lookup. Cheap pre-filter for dedup.

    Stored titles are not normalized, so we use ``ilike`` against the raw
    title; punctuation differences slip through this check, but the embedding
    path catches what this misses. A normalized-title generated column would
    tighten this — out of scope for the dedup PR (schema change).
    """
    if not has_supabase_creds() or not normalized_title:
        return None
    rows = (
        supabase.table(table)
        .select("id,subsector")
        .ilike("title", normalized_title)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    return (rows[0]["id"], rows[0]["subsector"])


def get_vuln_by_id(
    row_id: str,
    table: str = "vulnerabilities",
) -> dict | None:
    """Fetch a single vulnerability row by id. Returns dict or None.

    Used by the dedup merge path to load the existing row before deep-merging
    a new record into it. Returns None when creds are missing, ``row_id`` is
    empty, or no row matches.
    """
    rows = supabase.table(table).select("*").eq("id", row_id).limit(1).execute().data
    return rows[0] if rows else None


def update_vuln(
    row_id: str,
    payload: dict,
    embedding: list[float] | None = None,
    table: str = "vulnerabilities",
) -> dict | None:
    """UPDATE one vulnerability row by id with the given fields.

    ``payload`` should contain only the columns to overwrite. The caller is
    responsible for stripping immutable/server-managed fields (``id``,
    ``date_accessed``) before passing.
    """
    body = dict(payload)
    if embedding is not None:
        body["embedding"] = embedding
    resp = supabase.table(table).update(body).eq("id", row_id).execute()
    rows = resp.data or []
    return rows[0] if rows else None


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
    return response.data[0]
