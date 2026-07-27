import os
import uuid

import pytest

dotenv = pytest.importorskip("dotenv")
supabase = pytest.importorskip("supabase")

dotenv.load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not (SUPABASE_URL and SUPABASE_KEY):
    pytest.skip(
        "SUPABASE_URL / SUPABASE_KEY not configured",
        allow_module_level=True,
    )

from src import supabase_function as sb  # noqa: E402
from src.classes import Vulnerability  # noqa: E402

TEST_TABLE = "test_table"
TEMP_SOURCE = "_pytest_temp_"
IMPOSSIBLE_UUID = "00000000-0000-0000-0000-000000000000"
SEED_SOURCE = (
    "CyberScoop"  # already present in test_table per src/config/test_table.sql
)

# Defense-in-depth: the write guard below permits whatever TEST_TABLE names, so
# pin it to the designated sandbox table. If this constant ever gets pointed at a
# production table the guard would happily allow prod writes, so fail loudly here.
assert TEST_TABLE == "test_table", "TEST_TABLE must be the sandbox table"
assert TEST_TABLE not in {"vulnerabilities", "noise", "duplicates"}


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


class _TestTableOnlyClient:
    """Proxy around the live Supabase client that only permits the sandbox table.

    Every supabase_function helper (insert_vuln, insert_noise, load_cite,
    insert_duplicate) reaches the database through the module-global
    ``sb.supabase`` client via ``.table(name)``. By swapping that client for this
    proxy for the duration of the test module, any attempt to touch a table other
    than TEST_TABLE — including the production defaults "vulnerabilities" /
    "noise" / "duplicates" when a ``table=`` argument is forgotten — raises
    immediately instead of writing to the live project. ``.rpc`` is blocked too
    since these tests never exercise a live RPC. The surface is intentionally
    minimal (only ``.table`` and ``.rpc``) so there is no bypass via other
    client methods.
    """

    def __init__(self, real, allowed_table: str):
        self._real = real
        self._allowed_table = allowed_table

    def table(self, name: str):
        if name != self._allowed_table:
            raise RuntimeError(
                f"test_sb.py blocked write to non-sandbox table {name!r}; "
                f"only {self._allowed_table!r} is allowed"
            )
        return self._real.table(name)

    def rpc(self, *args, **kwargs):
        raise RuntimeError("test_sb.py blocked a live Supabase RPC call")


@pytest.fixture(scope="module", autouse=True)
def _guard_supabase_writes():
    """Confine every supabase_function write in this module to the sandbox table."""
    real = sb.supabase
    sb.supabase = _TestTableOnlyClient(real, TEST_TABLE)
    try:
        yield
    finally:
        sb.supabase = real


@pytest.fixture(scope="module", autouse=True)
def restore_test_table_state(_guard_supabase_writes):
    """Snapshot test_table before any test runs, restore on teardown.

    Runs inside ``_guard_supabase_writes`` (declared as a dependency so the guard
    installs first and tears down last), so the snapshot/wipe/restore all go
    through the guarded client and the destructive ``delete()`` can only ever hit
    the sandbox table.
    """
    client = sb.supabase
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
    """Pure-logic tests for is_known_db — no DB needed."""

    def test_matches_title_and_body_snippet(self):
        """Returns True when title matches and body snippet is contained in content."""
        rows = [{"title": "Some Article", "content": "lorem ipsum dolor sit amet"}]
        assert sb.is_known_db(rows, "Some Article", "ipsum") is True

    def test_returns_false_on_title_mismatch(self):
        """Returns False when title does not match any row."""
        rows = [{"title": "Some Article", "content": "lorem ipsum dolor"}]
        assert sb.is_known_db(rows, "Different Title", "ipsum") is False

    def test_returns_false_on_missing_body_snippet(self):
        """Returns False when title matches but body snippet is not in content."""
        rows = [{"title": "Some Article", "content": "lorem ipsum dolor"}]
        assert sb.is_known_db(rows, "Some Article", "nonexistent") is False

    def test_handles_none_content_in_row(self):
        """Rows with None content do not raise; the search just doesn't match them."""
        rows = [{"title": "Some Article", "content": None}]
        assert sb.is_known_db(rows, "Some Article", "anything") is False

    def test_empty_site_query_returns_false(self):
        """Empty site_query always returns False."""
        assert sb.is_known_db([], "Some Article", "ipsum") is False

    def test_normalizes_title_casing(self):
        """Title comparison ignores case and surrounding whitespace."""
        rows = [{"title": "  Some Article  ", "content": "lorem ipsum"}]
        assert sb.is_known_db(rows, "SOME ARTICLE", "ipsum") is True


class TestLoadCite:
    """Integration tests for load_cite against test_table (noise side skipped)."""

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


class TestWriteGuard:
    """The guard must block any write that targets a non-sandbox table."""

    def test_guard_blocks_production_table(self):
        """insert_vuln without table= defaults to 'vulnerabilities' and is blocked."""
        vuln = _make_vuln(title="guard blocks prod test")
        with pytest.raises(RuntimeError):
            sb.insert_vuln(vuln)  # defaults to table="vulnerabilities"
