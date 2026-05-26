import os
from dotenv import load_dotenv
from supabase import create_client
from src.classes import Vulnerability

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)



def _norm(s: str) -> str:
    """
    Returns a normalized string that is stripped of trailing white space and lowercase
    """
    return (s or "").strip().lower()

def load_cite(
    site_name: str,
    vuln_table: str = "vulnerabilities",
    noise_table: str | None = "noise",
) -> list[dict[str, str]]:
    """
    """
    vuln_data = supabase.table(vuln_table).select("title,content").eq("source_name", site_name).execute().data
    if noise_table:
        noise_data = supabase.table(noise_table).select("title,body_preview").eq("source_name", site_name).execute().data
        # rename body_preview -> content so callers don't care which table it came from
        vuln_data += [{"title": r["title"], "content": r["body_preview"]} for r in noise_data]
    return vuln_data


def is_known_article(site_query: list[dict[str, str]], title: str, body_snippet: str) -> bool:
    """
    """
    return any(
        _norm(row["title"]) == _norm(title)
        and body_snippet in (row["content"] or "")
        for row in site_query
    )



def insert_vuln(vuln: Vulnerability, table: str = "vulnerabilities") -> dict:
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
