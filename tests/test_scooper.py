import csv
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import src.scrapers.scooper as scooper

VALID_SUBSECTOR = scooper.SUBSECTOR_FIELDS[0]


@pytest.fixture(autouse=True)
def _disable_supabase(monkeypatch):
    monkeypatch.setattr(scooper, "SUPABASE_AVAILABLE", False)


@pytest.fixture(autouse=True)
def _isolated_csvs(monkeypatch, tmp_path):
    """Point all three CSV paths at tmp files so reads/writes never touch the
    real corpus. Individual tests seed these paths as needed."""
    monkeypatch.setattr(scooper, "RAW_CSV_PATH", tmp_path / "raw.csv")
    monkeypatch.setattr(scooper, "VULN_CSV_PATH", tmp_path / "vuln.csv")
    monkeypatch.setattr(scooper, "NOISE_CSV_PATH", tmp_path / "noise.csv")


@pytest.fixture
def mock_ensure_model_available():
    with patch("src.scrapers.scooper.ensure_model_available") as mock:
        yield mock


@pytest.fixture
def mock_ai_check_validation():
    with patch("src.scrapers.scooper.ai_check_validation") as mock:
        yield mock


@pytest.fixture
def mock_extract_fields():
    with patch("src.scrapers.scooper.extract_fields") as mock:
        yield mock


@pytest.fixture
def mock_unseen_df():
    with patch("src.scrapers.scooper._unseen_df") as mock:
        yield mock


@pytest.fixture
def mock_get_page():
    with patch("src.scrapers.scooper.get_page") as mock:
        yield mock


@pytest.fixture
def mock_time_sleep():
    with patch("src.scrapers.scooper.time.sleep") as mock:
        yield mock


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _raw_frame(rows):
    """Build a raw-shaped DataFrame (datetime ``date`` column) for patching
    ``_unseen_df``."""
    df = pd.DataFrame(rows, columns=scooper.RAW_CSV_HEADER)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def _write_raw(rows):
    """Write a raw CSV at the (monkeypatched) raw path for ``_unseen_df`` tests."""
    pd.DataFrame(rows, columns=scooper.RAW_CSV_HEADER).to_csv(
        scooper.RAW_CSV_PATH, index=False
    )


def _resp(html: str):
    """Fake ``get_page`` response exposing ``.content`` for BeautifulSoup."""
    return MagicMock(content=html)


# --------------------------------------------------------------------------- #
# _unseen_df
# --------------------------------------------------------------------------- #
def test_unseen_df_returns_all_when_nothing_classified():
    _write_raw(
        [
            {
                "source_name": "CyberScoop",
                "title": "A",
                "link": "u1",
                "body": "b1",
                "date": "2026-01-01",
            },
            {
                "source_name": "AHA",
                "title": "B",
                "link": "u2",
                "body": "b2",
                "date": "2026-01-02",
            },
        ]
    )

    out = scooper._unseen_df()

    assert len(out) == 2
    assert set(out["title"]) == {"A", "B"}


def test_unseen_df_removes_rows_already_in_vuln_and_noise():
    _write_raw(
        [
            {
                "source_name": "CyberScoop",
                "title": "A",
                "link": "u1",
                "body": "b1",
                "date": "2026-01-01",
            },
            {
                "source_name": "AHA",
                "title": "B",
                "link": "u2",
                "body": "b2",
                "date": "2026-01-02",
            },
            {
                "source_name": "FedScoop",
                "title": "C",
                "link": "u3",
                "body": "b3",
                "date": "2026-01-03",
            },
        ]
    )
    # A is already a vuln, B is already noise — both should drop out.
    pd.DataFrame([{"source_name": "CyberScoop", "title": "A"}]).to_csv(
        scooper.VULN_CSV_PATH, index=False
    )
    pd.DataFrame([{"source_name": "AHA", "title": "B"}]).to_csv(
        scooper.NOISE_CSV_PATH, index=False
    )

    out = scooper._unseen_df()

    assert list(out["title"]) == ["C"]


# --------------------------------------------------------------------------- #
# _setup_cvs / _update_csv
# --------------------------------------------------------------------------- #
def test_setup_cvs_creates_all_three_with_headers():
    scooper._setup_cvs()

    for path, header in (
        (scooper.RAW_CSV_PATH, scooper.RAW_CSV_HEADER),
        (scooper.VULN_CSV_PATH, scooper.VULN_CSV_HEADER),
        (scooper.NOISE_CSV_PATH, scooper.NOISE_CSV_HEADER),
    ):
        assert path.exists()
        with path.open(newline="") as f:
            assert next(csv.reader(f)) == header


def test_update_csv_appends_rows_with_date_format():
    scooper._setup_cvs()  # writes the raw header once
    df = _raw_frame(
        [
            {
                "source_name": "CyberScoop",
                "title": "A",
                "link": "u1",
                "body": "b1",
                "date": "2026-01-01",
            },
        ]
    )

    scooper._update_raw_csv(df)

    out = pd.read_csv(scooper.RAW_CSV_PATH)
    assert len(out) == 1  # header not duplicated
    assert list(out.columns) == scooper.RAW_CSV_HEADER
    assert out.iloc[0]["date"] == "2026-01-01"  # YYYY-MM-DD, no time


def test_update_csv_empty_df_is_noop():
    scooper._setup_cvs()
    before = scooper.RAW_CSV_PATH.read_text()

    scooper._update_raw_csv(pd.DataFrame(columns=scooper.RAW_CSV_HEADER))

    assert scooper.RAW_CSV_PATH.read_text() == before


# --------------------------------------------------------------------------- #
# run_scooper
# --------------------------------------------------------------------------- #
def test_run_scooper_counts_validated_and_rejected(
    mock_unseen_df,
    mock_ai_check_validation,
    mock_extract_fields,
):
    """One threat + one noise article updates the counters and both frames."""
    df = _raw_frame(
        [
            {
                "source_name": "CyberScoop",
                "title": "Breach",
                "link": "u1",
                "body": "confirmed breach",
                "date": "2026-01-01",
            },
            {
                "source_name": "AHA",
                "title": "Policy",
                "link": "u2",
                "body": "not a disruption",
                "date": "2026-01-02",
            },
        ]
    )

    mock_unseen_df.return_value = df
    mock_ai_check_validation.side_effect = [
        (True, VALID_SUBSECTOR),
        (False, "No impact"),
    ]
    mock_extract_fields.return_value = ({"exec_summary": "Breach confirmed"}, {})

    stats, vuln_list, vuln_df, noise_df = scooper.run_scooper()

    assert len(vuln_list) == 1
    assert len(vuln_df) == 1
    assert len(noise_df) == 1
    # subsector_data was wrapped in its dataclass, so serialization succeeds
    # (a raw dict would raise AttributeError here).
    assert vuln_list[0].to_dict()["subsector"] == VALID_SUBSECTOR


def test_run_scooper_skips_unrecognized_subsector(
    mock_unseen_df,
    mock_ai_check_validation,
    mock_extract_fields,
):
    df = _raw_frame(
        [
            {
                "source_name": "X",
                "title": "T",
                "link": "u",
                "body": "b",
                "date": "2026-01-01",
            }
        ]
    )

    mock_unseen_df.return_value = df
    mock_ai_check_validation.return_value = (True, "not_a_real_subsector")

    stats, vuln_list, _, _ = scooper.run_scooper()

    assert stats.validated == 0
    assert stats.skipped == 1
    assert vuln_list == []
    mock_extract_fields.assert_not_called()


def test_run_scooper_skips_when_subsector_fields_missing(
    mock_unseen_df,
    mock_ai_check_validation,
    mock_extract_fields,
):
    df = _raw_frame(
        [
            {
                "source_name": "X",
                "title": "T",
                "link": "u",
                "body": "b",
                "date": "2026-01-01",
            }
        ]
    )

    mock_unseen_df.return_value = df
    mock_ai_check_validation.return_value = (True, VALID_SUBSECTOR)
    mock_extract_fields.side_effect = scooper.MissingSubsectorFieldsError("no fields")

    stats, vuln_list, _, _ = scooper.run_scooper()

    assert stats.validated == 0
    assert stats.skipped == 1
    assert vuln_list == []


# --------------------------------------------------------------------------- #
# _scrape_page
# --------------------------------------------------------------------------- #
SITE_CONFIG = {
    "name": "TestSite",
    "url": "https://example.com",
    "map": {
        "container": "li.item",
        "title": None,
        "link_selector": "a",
        "body_selector": "div.body",
        "date_selector": "time[datetime]",
        "starting_page": 1,
        "cap": 1,
    },
}
LISTING_HTML = (
    "<ul><li class='item'>"
    "<a href='https://example.com/article-1'>Hospital breach</a>"
    "</li></ul>"
)
ARTICLE_HTML = (
    "<div class='body'>Full article body here</div>"
    "<time datetime='2026-01-01'>Jan 1</time>"
)


def test_scrape_page_parses_article(mock_get_page, mock_time_sleep):
    raw_df = pd.DataFrame(columns=scooper.RAW_CSV_HEADER)

    mock_get_page.side_effect = [_resp(LISTING_HTML), _resp(ARTICLE_HTML)]

    articles_df, stop = scooper._scrape_page(
        SITE_CONFIG, SITE_CONFIG["url"], raw_df=raw_df
    )

    assert stop is False
    assert len(articles_df) == 1
    row = articles_df.iloc[0]
    assert row["source_name"] == "TestSite"
    assert row["title"] == "Hospital breach"
    assert row["link"] == "https://example.com/article-1"
    assert "Full article body" in row["body"]
    assert row["date"] == pd.Timestamp("2026-01-01")


def test_scrape_page_stops_on_known_article(mock_get_page, mock_time_sleep):
    # raw_df already contains this (source_name, title) -> stop, exclude it.
    raw_df = pd.DataFrame(
        [
            {
                "source_name": "TestSite",
                "title": "Hospital breach",
                "link": "x",
                "body": "x",
                "date": pd.NaT,
            }
        ],
        columns=scooper.RAW_CSV_HEADER,
    )

    mock_get_page.side_effect = [_resp(LISTING_HTML), _resp(ARTICLE_HTML)]

    articles_df, stop = scooper._scrape_page(
        SITE_CONFIG, SITE_CONFIG["url"], raw_df=raw_df
    )

    assert stop is True
    assert len(articles_df) == 0
