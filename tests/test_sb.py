import os
import uuid

import pytest
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not (SUPABASE_URL and SUPABASE_KEY):
    pytest.skip(
        "SUPABASE_URL / SUPABASE_KEY not configured",
        allow_module_level=True,
    )

from src import supabase_function as sb
from src.classes import Vulnerability

TEST_TABLE = "test_table"
TEMP_SOURCE = "_pytest_temp_"
IMPOSSIBLE_UUID = "00000000-0000-0000-0000-000000000000"
SEED_SOURCE = (
    "CyberScoop"  # already present in test_table per src/config/test_table.sql
)


def _make_vuln(
    title: str = "pytest article",
    source_name: str = TEMP_SOURCE,
    direct_link: str | None = None,
    subsector: str = "cyber_attack",
    content: str = "pytest content body",
) -> Vulnerability:
    return Vulnerability(
        id="",
        title=title,
        source_name=source_name,
        direct_link=direct_link or f"https://example.test/{uuid.uuid4()}",
        subsector=subsector,
        date_accessed="2026-01-01 00:00+00",
        date_published="2026-01-01",
        content=content,
    )


@pytest.fixture(scope="module", autouse=True)
def restore_test_table_state():
    """Snapshot test_table before any test runs, restore on teardown."""
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    baseline = client.table(TEST_TABLE).select("*").execute().data
    yield
    client.table(TEST_TABLE).delete().neq("id", IMPOSSIBLE_UUID).execute()
    for row in baseline:
        client.table(TEST_TABLE).insert(row).execute()


class TestNorm:
    """Test suite for _norm helper."""

    def test_norm_strips_whitespace(self):
        """Leading/trailing whitespace is removed."""
        assert sb._norm("  Hello  ") == "hello"

    def test_norm_lowercases(self):
        """Mixed-case input is lowercased."""
        assert sb._norm("CyberScoop") == "cyberscoop"

    def test_norm_handles_none(self):
        """None input returns empty string."""
        assert sb._norm(None) == ""

    def test_norm_handles_empty_string(self):
        """Empty string input returns empty string."""
        assert sb._norm("") == ""


class TestIsKnownArticle:
    """Pure-logic tests for is_known_article — no DB needed."""

    def test_matches_title_and_body_snippet(self):
        """Returns True when title matches and body snippet is contained in content."""
        rows = [{"title": "Some Article", "content": "lorem ipsum dolor sit amet"}]
        assert sb.is_known_article(rows, "Some Article", "ipsum") is True

    def test_returns_false_on_title_mismatch(self):
        """Returns False when title does not match any row."""
        rows = [{"title": "Some Article", "content": "lorem ipsum dolor"}]
        assert sb.is_known_article(rows, "Different Title", "ipsum") is False

    def test_returns_false_on_missing_body_snippet(self):
        """Returns False when title matches but body snippet is not in content."""
        rows = [{"title": "Some Article", "content": "lorem ipsum dolor"}]
        assert sb.is_known_article(rows, "Some Article", "nonexistent") is False

    def test_handles_none_content_in_row(self):
        """Rows with None content do not raise; the search just doesn't match them."""
        rows = [{"title": "Some Article", "content": None}]
        assert sb.is_known_article(rows, "Some Article", "anything") is False

    def test_empty_site_query_returns_false(self):
        """Empty site_query always returns False."""
        assert sb.is_known_article([], "Some Article", "ipsum") is False

    def test_normalizes_title_casing(self):
        """Title comparison ignores case and surrounding whitespace."""
        rows = [{"title": "  Some Article  ", "content": "lorem ipsum"}]
        assert sb.is_known_article(rows, "SOME ARTICLE", "ipsum") is True


class TestLoadCite:
    """Integration tests for load_cite against test_table (noise side skipped)."""

    def test_load_cite_returns_known_source(self):
        """Querying a source present in test_table returns at least one row."""
        rows = sb.load_cite(SEED_SOURCE, vuln_table=TEST_TABLE, noise_table=None)
        assert len(rows) > 0

    def test_load_cite_unknown_source_returns_empty(self):
        """Querying a source not present returns an empty list."""
        rows = sb.load_cite(
            f"_no_such_source_{uuid.uuid4()}",
            vuln_table=TEST_TABLE,
            noise_table=None,
        )
        assert rows == []

    def test_load_cite_keys_are_title_and_content(self):
        """Each returned row exposes title and content keys."""
        rows = sb.load_cite(SEED_SOURCE, vuln_table=TEST_TABLE, noise_table=None)
        for row in rows:
            assert set(row.keys()) == {"title", "content"}


class TestInsertVuln:
    """Integration tests for insert_vuln against test_table."""

    def test_insert_vuln_writes_and_returns_row(self):
        """insert_vuln returns the inserted row with the fields we passed."""
        vuln = _make_vuln(title="insert returns row test")
        result = sb.insert_vuln(vuln, table=TEST_TABLE)
        assert result["title"] == "insert returns row test"
        assert result["source_name"] == TEMP_SOURCE

    def test_insert_vuln_db_generates_id(self):
        """The DB generates a uuid even though we did not pass one."""
        vuln = _make_vuln(title="db generates id test")
        result = sb.insert_vuln(vuln, table=TEST_TABLE)
        assert result["id"]
        # uuid.UUID raises if the string isn't a valid uuid
        uuid.UUID(result["id"])

    def test_insert_vuln_round_trip(self):
        """A freshly inserted row is visible via load_cite immediately after."""
        unique_source = f"{TEMP_SOURCE}{uuid.uuid4()}"
        vuln = _make_vuln(
            title="round trip test",
            source_name=unique_source,
            content="round trip content body",
        )
        sb.insert_vuln(vuln, table=TEST_TABLE)
        rows = sb.load_cite(unique_source, vuln_table=TEST_TABLE, noise_table=None)
        assert len(rows) == 1
        assert rows[0]["title"] == "round trip test"
        assert rows[0]["content"] == "round trip content body"
