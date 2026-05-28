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


# Module imports cleanly even when creds are missing — callers must gate every
# DB operation behind has_supabase_creds() (the helper functions below will
# AttributeError on None if invoked without creds, by design).
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


def insert_vuln(vuln: Vulnerability, table: str = "vulnerabilities") -> dict:
    """Insert a vulnerability article into the database.

    Args:
        vuln: Vulnerability object to insert.
        table: Table name (default: "vulnerabilities").

    Returns:
        Inserted record with generated ID and metadata.
    """
    payload = vuln.to_dict()
    payload.pop("id", None)  # let Postgres generate it
    response = supabase.table(table).insert(payload).execute()
    return response.data[0]


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
