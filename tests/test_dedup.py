"""Unit tests for src/dedup.py.

Pure logic only. Stubs ``embed_fingerprint`` so the test path never imports
sentence-transformers, and stubs the lookup callables so it never talks to
Supabase.
"""

from __future__ import annotations

import pytest

from src import dedup
from src.dedup import (
    Action,
    canonicalize_url,
    decide_action,
    dedupe_records,
    merge_records,
    normalize_title,
    record_to_fingerprint_text,
)


@pytest.fixture(autouse=True)
def _stub_embedding(monkeypatch):
    """Replace embed_fingerprint with a deterministic, model-free stub."""

    def fake_embed(text: str) -> list[float]:
        return [0.0] * 384

    monkeypatch.setattr(dedup, "embed_fingerprint", fake_embed)


class TestCanonicalizeUrl:
    def test_lowercases_host(self):
        assert (
            canonicalize_url("https://Example.COM/path") == "https://example.com/path"
        )

    def test_drops_query_and_fragment(self):
        assert (
            canonicalize_url("https://example.com/p?utm=x#frag")
            == "https://example.com/p"
        )

    def test_strips_trailing_slash(self):
        assert canonicalize_url("https://example.com/p/") == "https://example.com/p"

    def test_root_path(self):
        # path == "/" → stripped to "" so host is the canonical form
        assert canonicalize_url("https://example.com/") == "https://example.com"

    def test_empty(self):
        assert canonicalize_url("") == ""

    def test_preserves_case_in_path(self):
        # only host is case-normalized; path is identifier
        assert (
            canonicalize_url("https://example.com/Article/ABC")
            == "https://example.com/Article/ABC"
        )


class TestNormalizeTitle:
    def test_lowercases(self):
        assert normalize_title("Hospital HACKED") == "hospital hacked"

    def test_strips_punctuation(self):
        assert (
            normalize_title("Ransomware attack: 4 hospitals down!")
            == "ransomware attack 4 hospitals down"
        )

    def test_collapses_whitespace(self):
        assert normalize_title("a   b\tc\nd") == "a b c d"

    def test_empty(self):
        assert normalize_title("") == ""

    def test_only_punctuation(self):
        assert normalize_title("...!!!") == ""


class TestDecideAction:
    def test_no_neighbor_inserts(self):
        action = decide_action({"subsector": "cyber_attack"}, None, threshold=0.44)
        assert action.kind == "insert"

    def test_far_neighbor_inserts(self):
        # distance just above threshold → insert
        action = decide_action(
            {"subsector": "cyber_attack"},
            ("uuid-1", "cyber_attack", 0.45),
            threshold=0.44,
        )
        assert action.kind == "insert"

    def test_close_same_subsector_merges(self):
        action = decide_action(
            {"subsector": "cyber_attack"},
            ("uuid-1", "cyber_attack", 0.30),
            threshold=0.44,
        )
        assert action.kind == "merge_into"
        assert action.existing_id == "uuid-1"
        assert action.distance == 0.30

    def test_close_different_subsector_logs_conflict(self):
        action = decide_action(
            {"subsector": "cyber_attack"},
            ("uuid-2", "drug_shortage", 0.30),
            threshold=0.44,
        )
        assert action.kind == "log_conflict_and_insert"
        assert action.existing_id == "uuid-2"
        assert action.reason == "subsector_mismatch"

    def test_at_threshold_boundary_is_inclusive(self):
        # distance == threshold should count as a hit (<= in src/ingest.py:350)
        action = decide_action(
            {"subsector": "cyber_attack"},
            ("uuid-3", "cyber_attack", 0.44),
            threshold=0.44,
        )
        assert action.kind == "merge_into"

    def test_zero_distance_self_match_merges(self):
        action = decide_action(
            {"subsector": "natural_disaster"},
            ("uuid-4", "natural_disaster", 0.0),
            threshold=0.44,
        )
        assert action.kind == "merge_into"

    def test_subsector_match_is_case_insensitive(self):
        action = decide_action(
            {"subsector": "Cyber_Attack"},
            ("uuid-5", "cyber_attack", 0.20),
            threshold=0.44,
        )
        assert action.kind == "merge_into"


class TestDedupeRecordsEmbeddingPath:
    def test_no_neighbor_yields_insert(self):
        records = [
            {"id": "a", "subsector": "cyber_attack", "title": "T", "content": "c"}
        ]
        result = dedupe_records(records, find_neighbor=lambda emb: None)
        assert len(result) == 1
        _, action = result[0]
        assert action.kind == "insert"
        # embedding is carried through on insert
        assert action.embedding == [0.0] * 384

    def test_close_same_subsector_yields_merge(self):
        records = [
            {"id": "a", "subsector": "cyber_attack", "title": "T", "content": "c"}
        ]
        result = dedupe_records(
            records,
            find_neighbor=lambda emb: ("existing-1", "cyber_attack", 0.20),
        )
        _, action = result[0]
        assert action.kind == "merge_into"
        assert action.existing_id == "existing-1"

    def test_close_different_subsector_yields_conflict(self):
        records = [
            {"id": "a", "subsector": "cyber_attack", "title": "T", "content": "c"}
        ]
        result = dedupe_records(
            records,
            find_neighbor=lambda emb: ("existing-2", "drug_shortage", 0.10),
        )
        _, action = result[0]
        assert action.kind == "log_conflict_and_insert"
        assert action.reason == "subsector_mismatch"
        # conflict path still inserts, so embedding rides through
        assert action.embedding == [0.0] * 384

    def test_mixed_batch_routes_each_record(self):
        records = [
            {"id": "a", "subsector": "cyber_attack", "title": "A", "content": "x"},
            {"id": "b", "subsector": "drug_shortage", "title": "B", "content": "y"},
            {"id": "c", "subsector": "cyber_attack", "title": "C", "content": "z"},
        ]
        neighbors = {
            "a": None,  # insert
            "b": ("eb", "drug_shortage", 0.10),  # merge
            "c": ("ec", "natural_disaster", 0.20),  # conflict
        }
        # find_neighbor doesn't see the record id; we mirror the order by side-channeling
        order = iter(["a", "b", "c"])

        def find_neighbor(_emb):
            return neighbors[next(order)]

        kinds = [
            a.kind for _, a in dedupe_records(records, find_neighbor=find_neighbor)
        ]
        assert kinds == ["insert", "merge_into", "log_conflict_and_insert"]


class TestDedupeRecordsPrefilter:
    def test_url_exact_match_short_circuits(self):
        records = [
            {
                "id": "a",
                "subsector": "cyber_attack",
                "title": "T",
                "content": "c",
                "direct_link": "https://Example.com/x?utm=foo",
            }
        ]
        seen = {"https://example.com/x": ("existing-url", "cyber_attack")}

        def find_by_url(canon):
            return seen.get(canon)

        def boom_find_neighbor(_emb):
            raise AssertionError(
                "find_neighbor must not be called when pre-filter hits"
            )

        result = dedupe_records(
            records,
            find_neighbor=boom_find_neighbor,
            find_by_url=find_by_url,
        )
        _, action = result[0]
        assert action.kind == "merge_into"
        assert action.reason == "url_exact"
        assert action.existing_id == "existing-url"

    def test_title_exact_match_short_circuits(self):
        records = [
            {
                "id": "a",
                "subsector": "cyber_attack",
                "title": "Ransomware Attack: Hospital Down!",
                "content": "c",
                "direct_link": "https://example.com/x",
            }
        ]
        seen = {"ransomware attack hospital down": ("existing-title", "cyber_attack")}

        def find_by_title(norm):
            return seen.get(norm)

        def boom_find_neighbor(_emb):
            raise AssertionError(
                "find_neighbor must not be called when pre-filter hits"
            )

        result = dedupe_records(
            records,
            find_neighbor=boom_find_neighbor,
            find_by_url=lambda _u: None,
            find_by_title=find_by_title,
        )
        _, action = result[0]
        assert action.kind == "merge_into"
        assert action.reason == "title_exact"
        assert action.existing_id == "existing-title"

    def test_prefilter_miss_falls_through_to_embedding(self):
        records = [
            {
                "id": "a",
                "subsector": "cyber_attack",
                "title": "T",
                "content": "c",
                "direct_link": "https://example.com/x",
            }
        ]
        called = {"n": 0}

        def find_neighbor(_emb):
            called["n"] += 1
            return None

        dedupe_records(
            records,
            find_neighbor=find_neighbor,
            find_by_url=lambda _u: None,
            find_by_title=lambda _t: None,
        )
        assert called["n"] == 1


class TestRecordToFingerprintText:
    def test_includes_title_subsector_content(self):
        out = record_to_fingerprint_text(
            {
                "title": "Hospital hacked",
                "subsector": "cyber_attack",
                "content": "A " * 200,
            }
        )
        assert "Hospital hacked" in out
        assert "cyber_attack" in out
        assert "A " in out

    def test_trims_long_content(self):
        out = record_to_fingerprint_text(
            {"title": "T", "subsector": "s", "content": "x" * 5000}
        )
        # content slice is 1000 chars; other parts add a small amount
        assert len(out) < 1100

    def test_includes_subsector_data_values(self):
        out = record_to_fingerprint_text(
            {
                "title": "T",
                "subsector": "drug_shortage",
                "content": "",
                "subsector_data": {
                    "drug_name": "amoxicillin",
                    "manufacturer": "Pfizer",
                    "empty": "",
                    "blanks": [],
                },
            }
        )
        assert "amoxicillin" in out
        assert "Pfizer" in out
        # empty/blank fields are skipped
        assert "empty" not in out

    def test_missing_fields_dont_crash(self):
        # all fields optional
        assert record_to_fingerprint_text({}) == ""


class TestActionDataclass:
    def test_default_fields_are_none(self):
        a = Action("insert")
        assert a.existing_id is None
        assert a.reason is None
        assert a.distance is None
        assert a.embedding is None

    def test_frozen(self):
        a = Action("insert")
        with pytest.raises(Exception):
            a.kind = "merge_into"  # type: ignore[misc]


class TestMergeRecords:
    def _base(self, **overrides):
        rec = {
            "id": "old-id",
            "title": "Old title",
            "source_name": "OldSource",
            "direct_link": "https://old.example/x",
            "subsector": "cyber_attack",
            "date_accessed": "2026-01-01 10:00",
            "date_published": "2025-12-31 09:00",
            "content": "Old body content",
            "exec_summary": "Old summary",
            "subsector_data": {},
        }
        rec.update(overrides)
        return rec

    def test_id_and_subsector_kept_from_existing(self):
        existing = self._base()
        new = self._base(id="new-id", subsector="drug_shortage")
        merged = merge_records(existing, new)
        assert merged["id"] == "old-id"
        # subsector precondition is that they match; we keep existing's
        assert merged["subsector"] == "cyber_attack"

    def test_concat_fields_use_separator_when_distinct(self):
        existing = self._base(
            title="Old title", content="Old body", exec_summary="Old summary"
        )
        new = self._base(
            title="New title", content="New body", exec_summary="New summary"
        )
        merged = merge_records(existing, new)
        sep = "\n\n---\n\n"
        assert merged["title"] == f"Old title{sep}New title"
        assert merged["content"] == f"Old body{sep}New body"
        assert merged["exec_summary"] == f"Old summary{sep}New summary"

    def test_concat_fields_no_duplication_on_identical_values(self):
        existing = self._base(title="Same", content="Same body")
        new = self._base(title="Same", content="Same body")
        merged = merge_records(existing, new)
        # identical strings short-circuit in _concat → no separator
        assert merged["title"] == "Same"
        assert merged["content"] == "Same body"

    def test_join_fields_combine_distinct_values(self):
        existing = self._base(source_name="StationA", direct_link="https://a.example/x")
        new = self._base(source_name="StationB", direct_link="https://b.example/y")
        merged = merge_records(existing, new)
        assert merged["source_name"] == "StationA | StationB"
        assert merged["direct_link"] == "https://a.example/x | https://b.example/y"

    def test_join_fields_no_duplication_on_identical_values(self):
        existing = self._base(source_name="S", direct_link="https://x")
        new = self._base(source_name="S", direct_link="https://x")
        merged = merge_records(existing, new)
        assert merged["source_name"] == "S"
        assert merged["direct_link"] == "https://x"

    def test_dates_resolve_to_lexically_latest(self):
        existing = self._base(
            date_accessed="2026-01-01 10:00", date_published="2025-06-15 09:00"
        )
        new = self._base(
            date_accessed="2026-03-15 14:00", date_published="2025-03-10 08:00"
        )
        merged = merge_records(existing, new)
        assert merged["date_accessed"] == "2026-03-15 14:00"
        assert merged["date_published"] == "2025-06-15 09:00"

    def test_dates_empty_existing_yields_new(self):
        existing = self._base(date_accessed="", date_published="")
        new = self._base(
            date_accessed="2026-05-01 12:00", date_published="2026-04-01 12:00"
        )
        merged = merge_records(existing, new)
        assert merged["date_accessed"] == "2026-05-01 12:00"
        assert merged["date_published"] == "2026-04-01 12:00"

    def test_subsector_data_list_field_extended_and_deduped(self):
        existing = self._base(subsector_data={"drugs": ["amoxicillin", "lisinopril"]})
        new = self._base(subsector_data={"drugs": ["lisinopril", "metformin"]})
        merged = merge_records(existing, new)
        assert merged["subsector_data"]["drugs"] == [
            "amoxicillin",
            "lisinopril",
            "metformin",
        ]

    def test_subsector_data_scalar_favors_existing(self):
        existing = self._base(subsector_data={"manufacturer": "Pfizer"})
        new = self._base(subsector_data={"manufacturer": "Moderna"})
        merged = merge_records(existing, new)
        # scalar with non-empty existing → existing wins
        assert merged["subsector_data"]["manufacturer"] == "Pfizer"

    def test_subsector_data_scalar_falls_through_when_existing_empty(self):
        existing = self._base(
            subsector_data={"manufacturer": "", "vendor": None, "items": []}
        )
        new = self._base(
            subsector_data={
                "manufacturer": "Pfizer",
                "vendor": "Acme",
                "items": ["x"],
            }
        )
        merged = merge_records(existing, new)
        assert merged["subsector_data"]["manufacturer"] == "Pfizer"
        assert merged["subsector_data"]["vendor"] == "Acme"
        assert merged["subsector_data"]["items"] == ["x"]

    def test_unknown_field_in_only_existing_carries_through(self):
        existing = self._base()
        existing["geography_scope"] = "US-Northeast"
        new = self._base()
        merged = merge_records(existing, new)
        assert merged["geography_scope"] == "US-Northeast"

    def test_unknown_field_in_only_new_carries_through(self):
        existing = self._base()
        new = self._base()
        new["risk_level"] = "high"
        merged = merge_records(existing, new)
        assert merged["risk_level"] == "high"

    def test_unknown_list_field_in_both_extended_and_deduped(self):
        existing = self._base()
        existing["tags"] = ["ransomware", "healthcare"]
        new = self._base()
        new["tags"] = ["healthcare", "phishing"]
        merged = merge_records(existing, new)
        assert merged["tags"] == ["ransomware", "healthcare", "phishing"]

    def test_idempotence(self):
        a = self._base(
            subsector_data={"drugs": ["amoxicillin"], "manufacturer": "Pfizer"},
        )
        a["geography_scope"] = "US"
        merged = merge_records(a, a)
        for k, v in a.items():
            assert merged[k] == v
