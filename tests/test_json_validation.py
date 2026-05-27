from __future__ import annotations

from tests.json_validation import validate_source


def _make_source(geography_scope: str) -> dict[str, str]:
    """Helper to create a source dict with a specified geography_scope for testing."""
    return {
        "id": "source-1",
        "title": "Example article",
        "source_name": "Example News",
        "direct_link": "https://example.com/article",
        "date_accessed": "2026-05-27",
        "date_published": "2026-05-26",
        "subsector": "none",
        "geography_scope": geography_scope,
    }


def test_validate_source_maps_city_to_state() -> None:
    """Test that a city name in geography_scope is correctly mapped to its state."""
    source = _make_source("Kansas City")

    errors = validate_source(source, 1)

    assert errors == []
    assert source["geography_scope"] == "Missouri"


def test_validate_source_maps_county_to_state() -> None:
    """Test that a county name in geography_scope is correctly mapped to its state."""
    source = _make_source("Ashtabula County")

    errors = validate_source(source, 1)

    assert errors == []
    assert source["geography_scope"] == "Ohio"


def test_validate_source_prefers_highest_population_city() -> None:
    """Multiple US cities named Nashville exist; the validator should pick
    # the one with the largest population (Tennessee)."""
    source = _make_source("Nashville")

    errors = validate_source(source, 1)

    assert errors == []
    assert source["geography_scope"] == "Tennessee"


def test_validate_source_respects_state_hint() -> None:
    """When a state hint is present (abbreviation or name), it should be used."""
    source = _make_source("Nashville, IL")

    errors = validate_source(source, 1)

    assert errors == []
    assert source["geography_scope"] == "Illinois"
