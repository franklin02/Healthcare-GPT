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

    Every supabase_function helper (insert_vuln, insert_duplicate,
    push_vulnerabilities) reaches the database through the module-global
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


class TestWriteGuard:
    """The guard must block any write that targets a non-sandbox table."""

    def test_guard_blocks_production_table(self):
        """insert_vuln without table= defaults to 'vulnerabilities' and is blocked."""
        vuln = _make_vuln(title="guard blocks prod test")
        with pytest.raises(RuntimeError):
            sb.insert_vuln(vuln)  # defaults to table="vulnerabilities"
