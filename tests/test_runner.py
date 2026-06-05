import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, mock_open
from datetime import datetime
import pytest

import src.GDELT.runner as runner
from src.classes import Vulnerability
from src.cli_reporter import PipelineStats


@pytest.fixture(autouse=True)
def skip_model_availability_check():
    """Skip model availability check during tests."""
    with patch("src.GDELT.runner.ensure_model_available"):
        yield


def _make_vuln(
    id_value: str = "abc123",
    subsector: str = "drug_shortage",
    direct_link: str = "https://example.com/1",
    source_name: str = "test",
    title: str = "Test Article",
) -> Vulnerability:
    return Vulnerability(
        id=id_value,
        title=title,
        source_name=source_name,
        direct_link=direct_link,
        subsector=subsector,
        date_accessed="2024-01-01 00:00",
        date_published="2023-05-15",
        content="content",
    )


class TestStableId:
    """Tests for the stable_id function."""

    def test_stable_id_returns_16_char_hash(self):
        """stable_id should return a 16-character hash string."""
        url = "https://example.com/article"
        result = runner.stable_id(url)
        assert isinstance(result, str)
        assert len(result) == 16

    def test_stable_id_consistent(self):
        """stable_id should return the same hash for the same URL."""
        url = "https://example.com/article"
        result1 = runner.stable_id(url)
        result2 = runner.stable_id(url)
        assert result1 == result2

    def test_stable_id_different_urls(self):
        """stable_id should return different hashes for different URLs."""
        url1 = "https://example.com/article1"
        url2 = "https://example.com/article2"
        result1 = runner.stable_id(url1)
        result2 = runner.stable_id(url2)
        assert result1 != result2

    def test_stable_id_hexadecimal(self):
        """stable_id should return a valid hexadecimal string."""
        url = "https://example.com/test"
        result = runner.stable_id(url)
        try:
            int(result, 16)  # Should not raise
            assert True
        except ValueError:
            assert False, "stable_id result is not a valid hexadecimal string"


class TestFmtDt:
    """Tests for the fmt_dt function."""

    def test_fmt_dt_yyyymmddhhmmss_format(self):
        """fmt_dt should handle YYYYMMDDHHMMSS format."""
        result = runner.fmt_dt("20230515123045")
        assert result == "2023-05-15 12:30"

    def test_fmt_dt_iso_format_with_z(self):
        """fmt_dt should handle ISO format with Z."""
        result = runner.fmt_dt("2023-05-15T12:30:45Z")
        assert result == "2023-05-15 12:30"

    def test_fmt_dt_iso_format_with_offset(self):
        """fmt_dt should handle ISO format with timezone offset."""
        result = runner.fmt_dt("2023-05-15T12:30:45+00:00")
        assert result == "2023-05-15 12:30"

    def test_fmt_dt_invalid_format_returns_original(self):
        """fmt_dt should return original string if format is invalid."""
        invalid_dt = "not a date"
        result = runner.fmt_dt(invalid_dt)
        assert result == invalid_dt

    def test_fmt_dt_empty_string(self):
        """fmt_dt should handle empty string."""
        result = runner.fmt_dt("")
        assert result == ""


class TestSaveJson:
    """Tests for the save_json function."""

    def test_save_json_creates_file(self):
        """save_json should create a JSON file with correct data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            data = {"key": "value", "number": 42}
            runner.save_json(path, data)

            assert path.exists()
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded == data

    def test_save_json_with_nested_data(self):
        """save_json should handle nested dictionaries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            data = {"nested": {"key": "value"}, "list": [1, 2, 3]}
            runner.save_json(path, data)

            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded == data

    def test_save_json_with_unicode(self):
        """save_json should handle Unicode characters correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            data = {"text": "Hello 世界 🌍"}
            runner.save_json(path, data)

            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "世界" in content
            loaded = json.load(open(path, encoding="utf-8"))
            assert loaded["text"] == "Hello 世界 🌍"

    def test_save_json_pretty_prints(self):
        """save_json should format JSON with indentation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            data = {"key": "value"}
            runner.save_json(path, data)

            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # Check that it's indented (contains newlines and spaces)
            assert "\n" in content


class TestClearDirectory:
    """Tests for the clear_directory function."""

    def test_clear_directory_returns_without_error_for_missing_directory(self):
        """clear_directory should do nothing when the directory does not exist."""
        missing_directory = Path("/nonexistent/directory/for/testing")

        runner.clear_directory(missing_directory)

    def test_clear_directory_removes_files_and_subdirectories(self):
        """clear_directory should remove files and nested directories inside a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            file_path = directory / "seed.json"
            file_path.write_text("{}", encoding="utf-8")

            nested_directory = directory / "nested"
            nested_directory.mkdir()
            nested_file = nested_directory / "child.json"
            nested_file.write_text("{}", encoding="utf-8")

            runner.clear_directory(directory)

            assert directory.exists()
            assert list(directory.iterdir()) == []


class TestPersistRawSeeds:
    """Tests for the persist_raw_seeds function."""

    @patch("src.GDELT.runner.save_json")
    @patch("src.GDELT.runner.SEEDS_DIR", Path("/mock/seeds"))
    def test_persist_raw_seeds_saves_all_seeds(self, mock_save_json):
        """persist_raw_seeds should save each seed as a separate JSON file."""
        raw_seeds = [
            {"url": "https://example.com/1", "source": "test1"},
            {"url": "https://example.com/2", "source": "test2"},
        ]
        runner.persist_raw_seeds(raw_seeds)

        assert mock_save_json.call_count == 2

    @patch("src.GDELT.runner.save_json")
    @patch("src.GDELT.runner.SEEDS_DIR", Path("/mock/seeds"))
    def test_persist_raw_seeds_uses_stable_id(self, mock_save_json):
        """persist_raw_seeds should use stable_id for file naming."""
        raw_seeds = [{"url": "https://example.com/test"}]
        runner.persist_raw_seeds(raw_seeds)

        call_args = mock_save_json.call_args_list[0]
        path_arg = call_args[0][0]
        assert "seeds" in str(path_arg)
        assert ".json" in str(path_arg)


class TestDedupeRawSeeds:
    """Tests for raw seed URL deduplication."""

    def test_dedupe_raw_seeds_merges_subsector_labels_by_url(self):
        """dedupe_raw_seeds should keep one seed per URL and merge subsectors."""
        raw_seeds = [
            {
                "url": "https://example.com/shared",
                "source": "first",
                "subsector": "cyber_attack",
            },
            {
                "url": "https://example.com/shared",
                "source": "second",
                "subsector": "drug_shortage",
            },
            {
                "url": "https://example.com/unique",
                "source": "third",
                "subsector": "natural_disaster",
            },
        ]

        result = runner.dedupe_raw_seeds(raw_seeds)

        assert len(result) == 2
        assert result[0]["url"] == "https://example.com/shared"
        assert result[0]["source"] == "first"
        assert result[0]["subsector"] == "cyber_attack"
        assert result[0]["detected_subsectors"] == [
            "cyber_attack",
            "drug_shortage",
        ]
        assert result[1]["detected_subsectors"] == ["natural_disaster"]


class TestPersistStage:
    """Tests for the persist_stage function."""

    @patch("src.GDELT.runner.save_json")
    def test_persist_stage_calls_save_json(self, mock_save_json):
        """persist_stage should call save_json with correct path and data."""
        directory = Path("/mock/validated")
        article_id = "abc123"
        stage = "validated"
        url = "https://example.com/test"
        data = {"subsector": "drug_shortage", "fields": {}}

        runner.persist_stage(directory, article_id, stage, url, data)

        mock_save_json.assert_called_once()
        call_args = mock_save_json.call_args[0]
        assert call_args[0] == Path("/mock/validated/abc123.json")

    @patch("src.GDELT.runner.save_json")
    def test_persist_stage_creates_correct_record_structure(self, mock_save_json):
        """persist_stage should create correct record structure."""
        directory = Path("/mock/validated")
        article_id = "test_id"
        stage = "enriched"
        url = "https://example.com/article"
        data = {"subsector": "cyber_attack", "severity": "high"}

        runner.persist_stage(directory, article_id, stage, url, data)

        call_args = mock_save_json.call_args[0]
        record = call_args[1]

        assert record["id"] == "test_id"
        assert record["stage"] == "enriched"
        assert record["url"] == url
        assert record["record"] == data
        assert "saved_at" in record

    @patch("src.GDELT.runner.save_json")
    def test_persist_stage_saves_with_different_stages(self, mock_save_json):
        """persist_stage should handle different stage names."""
        directory = Path("/mock/dir")
        article_id = "id123"
        url = "https://example.com/test"
        data = {}

        stages = ["validated", "enriched", "processed"]
        for stage in stages:
            runner.persist_stage(directory, article_id, stage, url, data)

            last_call_record = mock_save_json.call_args_list[-1][0][1]
            assert last_call_record["stage"] == stage

    @patch("src.GDELT.runner.save_json")
    def test_persist_stage_preserves_data_integrity(self, mock_save_json):
        """persist_stage should preserve data dict exactly as provided."""
        directory = Path("/mock/dir")
        article_id = "id123"
        stage = "validated"
        url = "https://example.com/test"
        data = {
            "subsector": "drug_shortage",
            "drugs": ["medication1", "medication2"],
            "severity": "high",
            "nested": {"key": "value", "list": [1, 2, 3]},
        }

        runner.persist_stage(directory, article_id, stage, url, data)

        call_args = mock_save_json.call_args[0]
        record = call_args[1]
        assert record["record"] == data

    @patch("src.GDELT.runner.save_json")
    def test_persist_stage_includes_timestamp(self, mock_save_json):
        """persist_stage should include ISO format timestamp."""
        directory = Path("/mock/dir")
        article_id = "id123"
        stage = "validated"
        url = "https://example.com/test"
        data = {}

        runner.persist_stage(directory, article_id, stage, url, data)

        call_args = mock_save_json.call_args[0]
        record = call_args[1]

        # Check timestamp is in ISO format
        assert "saved_at" in record
        try:
            datetime.fromisoformat(record["saved_at"])
            assert True
        except ValueError:
            assert False, "saved_at is not in valid ISO format"


class TestStagedRecovery:
    """Tests for stitching staged GDELT files."""

    def test_load_staged_payloads_skips_malformed_files(self):
        """load_staged_payloads should load valid records and warn on bad files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            valid_record = {
                "id": "rec1",
                "direct_link": "https://example.com/1",
                "date_published": "20230515123045",
            }
            (directory / "valid.json").write_text(
                json.dumps({"record": valid_record}),
                encoding="utf-8",
            )
            (directory / "invalid.json").write_text(
                json.dumps({"missing": "record"}),
                encoding="utf-8",
            )
            reporter = Mock()

            with patch("src.GDELT.runner.ENRICHED_DIR", directory):
                records = runner.load_staged_payloads("enriched", reporter=reporter)

        assert records == [valid_record]
        reporter.warn.assert_called_once()

    def test_load_staged_payloads_rejects_invalid_stage(self):
        """load_staged_payloads should reject undocumented stages."""
        with pytest.raises(ValueError, match="Invalid stitch stage"):
            runner.load_staged_payloads("seed")

    def test_write_output_records_dedupes_existing_records_first(self):
        """write_output_records should append unique records and keep existing duplicates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.json"
            existing_record = {
                "id": "same-id",
                "title": "Existing",
                "direct_link": "https://example.com/same",
            }
            output_file.write_text(
                json.dumps({"sources": [existing_record]}),
                encoding="utf-8",
            )

            runner.write_output_records(
                [
                    {
                        "id": "same-id",
                        "title": "Recovered duplicate",
                        "direct_link": "https://example.com/same",
                    },
                    {
                        "id": "different-id",
                        "title": "Recovered direct link duplicate",
                        "direct_link": "https://example.com/same",
                    },
                    {
                        "id": "new-id",
                        "title": "Recovered new",
                        "direct_link": "https://example.com/new",
                        "date_published": "20230515123045",
                    },
                ],
                str(output_file),
                Mock(),
                PipelineStats("GDELT"),
            )

            result = json.loads(output_file.read_text(encoding="utf-8"))

        assert len(result["sources"]) == 2
        assert result["sources"][0]["title"] == "Existing"
        assert result["sources"][1]["id"] == "new-id"
        assert result["sources"][1]["date_published"] == "2023-05-15 12:30"

    def test_stitch_staged_records_uses_enriched_stage_without_pipeline_calls(self):
        """stitch_staged_records should recover enriched records without processing seeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            enriched_dir = tmp_path / "enriched"
            enriched_dir.mkdir()
            staged_file = enriched_dir / "record.json"
            staged_file.write_text(
                json.dumps(
                    {
                        "id": "rec1",
                        "stage": "enriched",
                        "url": "https://example.com/1",
                        "record": {
                            "id": "rec1",
                            "title": "Recovered",
                            "direct_link": "https://example.com/1",
                            "date_published": "20230515123045",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("src.GDELT.runner.ENRICHED_DIR", enriched_dir),
                patch("src.GDELT.runner.backfill_cyber_seeds") as mock_backfill,
                patch("src.GDELT.runner.process_seed") as mock_process_seed,
                patch("src.GDELT.runner.load_seen") as mock_load_seen,
                patch("src.GDELT.runner.save_seen") as mock_save_seen,
                patch("src.GDELT.runner.clear_directory") as mock_clear_directory,
            ):
                recovered = runner.stitch_staged_records(
                    output_path=str(tmp_path),
                    reporter=Mock(),
                )

            output_file = tmp_path / "GDELT.json"
            result = json.loads(output_file.read_text(encoding="utf-8"))
            assert staged_file.exists()

        assert recovered == [
            {
                "id": "rec1",
                "title": "Recovered",
                "direct_link": "https://example.com/1",
                "date_published": "2023-05-15 12:30",
            }
        ]
        assert len(result["sources"]) == 1
        assert result["sources"][0]["id"] == "rec1"
        mock_backfill.assert_not_called()
        mock_process_seed.assert_not_called()
        mock_load_seen.assert_not_called()
        mock_save_seen.assert_not_called()
        mock_clear_directory.assert_not_called()

    def test_stitch_staged_records_can_use_validated_stage(self):
        """stitch_staged_records should recover records from validated staging."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            validated_dir = tmp_path / "validated"
            validated_dir.mkdir()
            (validated_dir / "record.json").write_text(
                json.dumps(
                    {
                        "id": "rec1",
                        "stage": "validated",
                        "url": "https://example.com/1",
                        "record": {
                            "id": "rec1",
                            "title": "Recovered from validated",
                            "direct_link": "https://example.com/1",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("src.GDELT.runner.VALIDATED_DIR", validated_dir):
                recovered = runner.stitch_staged_records(
                    output_path=str(tmp_path),
                    stage="validated",
                )

            result = json.loads(
                (tmp_path / "GDELT.json").read_text(encoding="utf-8")
            )

        assert recovered[0]["title"] == "Recovered from validated"
        assert result["sources"][0]["title"] == "Recovered from validated"

    def test_stitch_staged_records_can_process_staged_seeds(self):
        """stitch_staged_records should replay staged seeds into usable output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            seeds_dir = tmp_path / "seeds"
            validated_dir = tmp_path / "validated"
            enriched_dir = tmp_path / "enriched"
            seeds_dir.mkdir()
            validated_dir.mkdir()
            enriched_dir.mkdir()
            seen_file = tmp_path / "seen_urls.json"
            completed_seed = {
                "url": "https://example.com/completed",
                "source": "test",
            }
            remaining_seed = {
                "url": "https://example.com/remaining",
                "source": "test",
            }
            (seeds_dir / "completed.json").write_text(
                json.dumps(
                    {
                        "id": "seed1",
                        "stage": "seed",
                        "url": completed_seed["url"],
                        "seed": completed_seed,
                    }
                ),
                encoding="utf-8",
            )
            (seeds_dir / "remaining.json").write_text(
                json.dumps(
                    {
                        "id": "seed2",
                        "stage": "seed",
                        "url": remaining_seed["url"],
                        "seed": remaining_seed,
                    }
                ),
                encoding="utf-8",
            )
            (enriched_dir / "completed.json").write_text(
                json.dumps(
                    {
                        "id": "rec1",
                        "stage": "enriched",
                        "url": completed_seed["url"],
                        "record": {
                            "id": "rec1",
                            "title": "Already enriched",
                            "direct_link": completed_seed["url"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            reporter = Mock()
            reporter.verbose = False

            with (
                patch("src.GDELT.runner.SEEDS_DIR", seeds_dir),
                patch("src.GDELT.runner.VALIDATED_DIR", validated_dir),
                patch("src.GDELT.runner.ENRICHED_DIR", enriched_dir),
                patch("src.GDELT.runner.backfill_cyber_seeds") as mock_backfill,
                patch("src.GDELT.runner.process_seed") as mock_process_seed,
                patch("src.GDELT.runner.clear_directory") as mock_clear_directory,
            ):
                mock_process_seed.return_value = _make_vuln(
                    id_value=runner.stable_id(remaining_seed["url"]),
                    direct_link=remaining_seed["url"],
                    title="Recovered from seed",
                )
                recovered = runner.stitch_staged_records(
                    output_path=str(tmp_path),
                    stage="seeds",
                    seen_urls_file=str(seen_file),
                    reporter=reporter,
                )

            result = json.loads(
                (tmp_path / "GDELT.json").read_text(encoding="utf-8")
            )
            completed_seed_exists = (seeds_dir / "completed.json").exists()
            remaining_seed_exists = (seeds_dir / "remaining.json").exists()

        assert [record["title"] for record in recovered] == [
            "Already enriched",
            "Recovered from seed",
        ]
        assert [record["title"] for record in result["sources"]] == [
            "Already enriched",
            "Recovered from seed",
        ]
        mock_process_seed.assert_called_once()
        assert mock_process_seed.call_args.args[0] == remaining_seed
        mock_backfill.assert_not_called()
        mock_clear_directory.assert_not_called()
        assert completed_seed_exists
        assert remaining_seed_exists


class TestLoadSeen:
    """Tests for the load_seen function."""

    def test_load_seen_returns_empty_set_if_file_not_exist(self):
        """load_seen should return empty set if file doesn't exist."""
        result = runner.load_seen(Path("/nonexistent/file.json"))
        assert result == set()

    def test_load_seen_loads_existing_urls(self):
        """load_seen should load URLs from existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "seen.json"
            urls = ["https://example.com/1", "https://example.com/2"]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(urls, f)

            result = runner.load_seen(path)
            assert result == set(urls)

    def test_load_seen_with_none_uses_default_path(self):
        """load_seen should use default path when None is passed."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = runner.load_seen(None)
            assert result == set()


class TestSaveSeen:
    """Tests for the save_seen function."""

    def test_save_seen_creates_file_with_sorted_urls(self):
        """save_seen should save URLs sorted and formatted as JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "seen.json"
            urls = {
                "https://example.com/3",
                "https://example.com/1",
                "https://example.com/2",
            }
            runner.save_seen(urls, path)

            assert path.exists()
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded == sorted(list(urls))

    def test_save_seen_with_none_uses_default_path(self):
        """save_seen should use default path when None is passed."""
        urls = {"https://example.com/1"}
        with patch("builtins.open", mock_open()):
            runner.save_seen(urls, None)


class TestProcessSeed:
    """Tests for the process_seed function."""

    @patch("src.GDELT.runner.get_body")
    @patch("src.GDELT.runner.ai_check_validation")
    def test_process_seed_already_seen(self, mock_ai_check, mock_get_body):
        """process_seed should return None if URL already seen."""
        seed = {"url": "https://example.com/test", "source": "test"}
        seen = {"https://example.com/test"}

        result = runner.process_seed(seed, seen)

        assert result is None
        mock_get_body.assert_not_called()
        mock_ai_check.assert_not_called()

    @patch("src.GDELT.runner.get_title")
    @patch("src.GDELT.runner.get_body")
    @patch("src.GDELT.runner.ai_check_validation")
    def test_process_seed_empty_body(
        self, mock_ai_check, mock_get_body, mock_get_title
    ):
        """process_seed should return None if body is empty, without fetching the title."""
        seed = {"url": "https://example.com/test", "source": "test"}
        seen = set()
        mock_get_body.return_value = ""

        result = runner.process_seed(seed, seen)

        assert result is None
        assert "https://example.com/test" not in seen  # Not added because body is empty
        mock_get_title.assert_not_called()

    @patch("src.GDELT.runner.get_title")
    @patch("src.GDELT.runner.get_body")
    @patch("src.GDELT.runner.ai_check_validation")
    def test_process_seed_not_a_disruption(
        self, mock_ai_check, mock_get_body, mock_get_title
    ):
        """process_seed should return None if not validated as disruption."""
        seed = {"url": "https://example.com/test", "source": "test"}
        seen = set()
        mock_get_body.return_value = "Some content"
        mock_get_title.return_value = "Test Article"
        mock_ai_check.return_value = (False, "not relevant")

        result = runner.process_seed(seed, seen)

        assert result is None
        assert "https://example.com/test" in seen

    @patch("src.GDELT.runner.extract_fields")
    @patch("src.GDELT.runner.get_title")
    @patch("src.GDELT.runner.get_body")
    @patch("src.GDELT.runner.ai_check_validation")
    def test_process_seed_valid_disruption(
        self, mock_ai_check, mock_get_body, mock_get_title, mock_extract_fields
    ):
        """process_seed should return a Vulnerability for valid disruption."""
        seed = {
            "url": "https://example.com/test",
            "source": "TestSource",
            "date": "2023-05-15",
        }
        seen = set()
        mock_get_body.return_value = "Content about drug shortage"
        mock_get_title.return_value = "Drug Shortage Confirmed"
        mock_ai_check.return_value = (True, "drug_shortage")
        mock_extract_fields.return_value = (
            {
                "exec_summary": "Shortage confirmed.",
                "geography_scope": "Midwest",
            },
            {"drug_name": "aspirin"},
        )

        result = runner.process_seed(seed, seen)

        assert result is not None
        assert result.subsector == "drug_shortage"
        assert result.source_name == "TestSource"
        assert result.direct_link == "https://example.com/test"
        assert result.title == "Drug Shortage Confirmed"
        assert result.exec_summary == "Shortage confirmed."
        assert result.geography_scope == "Midwest"
        assert result.subsector_data is not None
        assert result.subsector_data.drug_name == "aspirin"

    @patch("src.GDELT.runner.get_title")
    @patch("src.GDELT.runner.get_body")
    @patch("src.GDELT.runner.ai_check_validation")
    def test_process_seed_invalid_subsector(
        self, mock_ai_check, mock_get_body, mock_get_title
    ):
        """process_seed should skip if subsector is invalid."""
        seed = {"url": "https://example.com/test"}
        seen = set()
        mock_get_body.return_value = "Some content"
        mock_get_title.return_value = "Test Title"
        mock_ai_check.return_value = (True, "invalid_subsector")

        result = runner.process_seed(seed, seen)

        assert result is None
        assert "https://example.com/test" in seen

    @patch("src.GDELT.runner.extract_fields")
    @patch("src.GDELT.runner.get_title")
    @patch("src.GDELT.runner.get_body")
    @patch("src.GDELT.runner.ai_check_validation")
    def test_process_seed_all_valid_subsectors(
        self, mock_ai_check, mock_get_body, mock_get_title, mock_extract_fields
    ):
        """process_seed should accept all valid subsectors."""
        valid_subsectors = {
            "drug_shortage",
            "medical_device_shortage",
            "cyber_attack",
            "natural_disaster",
            "other",
        }
        mock_get_body.return_value = "Content"
        mock_get_title.return_value = "Test Title"
        mock_extract_fields.return_value = ({}, {})

        for subsector in valid_subsectors:
            seen = set()
            mock_ai_check.return_value = (True, subsector)
            result = runner.process_seed(
                {"url": f"https://example.com/{subsector}", "source": "test"},
                seen,
            )
            assert result is not None
            assert result.subsector == subsector

    @patch("src.GDELT.runner.extract_fields")
    @patch("src.GDELT.runner.get_title")
    @patch("src.GDELT.runner.get_body")
    @patch("src.GDELT.runner.ai_check_validation")
    def test_process_seed_uses_scraped_title_not_url(
        self, mock_ai_check, mock_get_body, mock_get_title, mock_extract_fields
    ):
        """process_seed should use the scraped page title, not the raw URL."""
        seed = {
            "url": "https://example.com/some/path/article",
            "source": "test",
            "date": "2023-05-15",
        }
        seen = set()
        mock_get_body.return_value = "Body content"
        mock_get_title.return_value = "Hospital Ransomware Attack Disrupts Services"
        mock_ai_check.return_value = (True, "cyber_attack")
        mock_extract_fields.return_value = ({}, {})

        result = runner.process_seed(seed, seen)

        assert result is not None
        assert result.title == "Hospital Ransomware Attack Disrupts Services"
        assert result.title != seed["url"]


class TestRun:
    """Tests for the main run function."""

    def test_run_checks_model_before_setup_or_seed_collection(self):
        """run should fail fast when the configured Ollama model is unavailable."""
        with (
            patch(
                "src.GDELT.runner.ensure_model_available",
                side_effect=runner.model_unavailable_error("model unavailable"),
            ) as mock_model_check,
            patch("src.GDELT.runner.LOGGER.error") as mock_log_error,
            patch("src.GDELT.runner.ensure_raw_dirs") as mock_ensure_dirs,
            patch("src.GDELT.runner.backfill_cyber_seeds") as mock_backfill,
        ):
            with pytest.raises(SystemExit):
                runner.run(num_files=1, limit=1, subsectors="all")

        mock_model_check.assert_called_once_with()
        mock_log_error.assert_called_once_with(
            "Model availability check failed: %s",
            mock_model_check.side_effect,
        )
        mock_ensure_dirs.assert_not_called()
        mock_backfill.assert_not_called()

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_returns_records(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should return list of validated records."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [
            {"url": "https://example.com/1", "source": "test"},
        ]
        mock_process_seed.return_value = _make_vuln(
            id_value="abc123",
            subsector="drug_shortage",
            direct_link="https://example.com/1",
            source_name="test",
            title="Test Article",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.run(
                num_files=1,
                limit=1,
                subsectors="all",
                output_path=tmpdir,
            )

        assert len(result) == 1
        assert isinstance(result[0], Vulnerability)
        assert result[0].subsector == "drug_shortage"

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_with_invalid_subsector(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should return empty list for invalid subsectors."""
        result = runner.run(
            num_files=1,
            limit=1,
            subsectors="invalid_subsector",
        )

        assert result == []

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_applies_limit(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should apply limit to seeds."""
        mock_load_seen.return_value = set()
        seeds = [
            {"url": f"https://example.com/{i}", "source": "test"} for i in range(5)
        ]
        mock_backfill.return_value = seeds
        mock_process_seed.return_value = None

        runner.run(num_files=1, limit=2, subsectors="all")

        # process_seed should only be called 2 times due to limit
        assert mock_process_seed.call_count == 2

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_outputs_to_default_location(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should write to default output location if not specified."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [{"url": "https://example.com/1"}]
        mock_process_seed.return_value = None

        with patch("builtins.open", mock_open()) as mock_file:
            runner.run(num_files=1, limit=1, subsectors="all", output_path=None)
            # Verify file operations were called
            mock_file.assert_called()

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_specific_subsector(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should filter to specific subsectors."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [{"url": "https://example.com/1"}]
        mock_process_seed.return_value = None

        runner.run(
            num_files=1,
            limit=1,
            subsectors="drug_shortage,cyber_attack",
        )

        # backfill_cyber_seeds should be called for each specified subsector
        assert mock_backfill.call_count == 2

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_dedupes_raw_seeds_across_subsectors(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should process duplicate URLs once across requested subsectors."""
        mock_load_seen.return_value = set()
        mock_backfill.side_effect = [
            [
                {
                    "url": "https://example.com/shared",
                    "source": "cyber source",
                    "subsector": "cyber_attack",
                }
            ],
            [
                {
                    "url": "https://example.com/shared",
                    "source": "drug source",
                    "subsector": "drug_shortage",
                }
            ],
        ]
        mock_process_seed.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            runner.run(
                num_files=1,
                limit=None,
                subsectors="cyber_attack,drug_shortage",
                output_path=tmpdir,
            )

        mock_persist_raw.assert_called_once()
        persisted_seeds = mock_persist_raw.call_args[0][0]
        assert len(persisted_seeds) == 1
        assert persisted_seeds[0]["url"] == "https://example.com/shared"
        assert persisted_seeds[0]["subsector"] == "cyber_attack"
        assert persisted_seeds[0]["detected_subsectors"] == [
            "cyber_attack",
            "drug_shortage",
        ]
        assert mock_process_seed.call_count == 1
        processed_seed = mock_process_seed.call_args[0][0]
        assert processed_seed["url"] == "https://example.com/shared"
        assert processed_seed["detected_subsectors"] == [
            "cyber_attack",
            "drug_shortage",
        ]

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_with_custom_seen_urls_file(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should load/save seen URLs from custom file."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            seen_file = Path(tmpdir) / "custom_seen.json"
            runner.run(
                num_files=1,
                limit=1,
                subsectors="all",
                seen_urls_file=str(seen_file),
            )

        # load_seen should be called with the custom file
        mock_load_seen.assert_called()
        mock_save_seen.assert_called()

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_merges_existing_dict_output(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should merge with existing output file containing dict with sources list."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [{"url": "https://example.com/new"}]
        mock_process_seed.return_value = _make_vuln(
            id_value="new_id",
            subsector="cyber_attack",
            direct_link="https://example.com/new",
            source_name="test",
            title="New",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.json"
            existing_data = {
                "sources": [
                    {
                        "id": "existing_id",
                        "title": "Existing",
                        "subsector": "drug_shortage",
                    }
                ]
            }
            with open(output_file, "w") as f:
                json.dump(existing_data, f)

            runner.run(
                num_files=1,
                limit=1,
                subsectors="all",
                output_path=str(output_file),
            )

            with open(output_file, "r") as f:
                result = json.load(f)
            assert len(result["sources"]) == 2

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_merges_existing_list_output(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should merge with existing output file that is a list."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [{"url": "https://example.com/new"}]
        mock_process_seed.return_value = _make_vuln(
            id_value="new_id",
            subsector="cyber_attack",
            direct_link="https://example.com/new",
            source_name="test",
            title="New",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.json"
            existing_data = [{"id": "existing_id", "title": "Existing"}]
            with open(output_file, "w") as f:
                json.dump(existing_data, f)

            runner.run(
                num_files=1,
                limit=1,
                subsectors="all",
                output_path=str(output_file),
            )

            with open(output_file, "r") as f:
                result = json.load(f)
            assert "sources" in result
            assert len(result["sources"]) == 2

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_replaces_unexpected_existing_output(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should replace unexpected existing output content with fresh records."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [{"url": "https://example.com/new"}]
        mock_process_seed.return_value = _make_vuln(
            id_value="new_id",
            subsector="cyber_attack",
            direct_link="https://example.com/new",
            source_name="test",
            title="New",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.json"
            existing_data = {"unexpected": "shape"}
            with open(output_file, "w") as f:
                json.dump(existing_data, f)

            runner.run(
                num_files=1,
                limit=1,
                subsectors="all",
                output_path=str(output_file),
            )

            with open(output_file, "r") as f:
                result = json.load(f)

            assert len(result["sources"]) == 1
            assert result["sources"][0]["id"] == "new_id"

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_creates_new_output_file(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
    ):
        """run should create new output file."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [{"url": "https://example.com/new"}]
        mock_process_seed.return_value = _make_vuln(
            id_value="new_id",
            subsector="cyber_attack",
            direct_link="https://example.com/new",
            source_name="test",
            title="New",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.json"
            assert not output_file.exists()

            runner.run(
                num_files=1,
                limit=1,
                subsectors="all",
                output_path=str(output_file),
            )

            assert output_file.exists()
            with open(output_file, "r") as f:
                result = json.load(f)
            assert "sources" in result
            assert len(result["sources"]) == 1

    def test_run_with_directory_path_as_seen_urls_file(self):
        """run should handle directory path for seen_urls_file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            seen_dir = Path(tmpdir)

            with (
                patch("src.GDELT.runner.backfill_cyber_seeds") as mock_backfill,
                patch("src.GDELT.runner.ensure_raw_dirs"),
                patch("src.GDELT.runner.load_seen") as mock_load,
                patch("src.GDELT.runner.save_seen"),
            ):
                mock_load.return_value = set()
                mock_backfill.return_value = []

                runner.run(
                    num_files=1,
                    limit=1,
                    subsectors="all",
                    seen_urls_file=str(seen_dir),
                )

                mock_load.assert_called()
                call_path = mock_load.call_args[0][0]
                assert call_path == seen_dir / "seen_urls.json"

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_default_output_is_compact(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
        capsys,
    ):
        """Default run output should show progress but not verbose item numbering."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [{"url": "https://example.com/1"}]
        mock_process_seed.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            runner.run(num_files=1, limit=1, subsectors="all", output_path=tmpdir)

        output = capsys.readouterr().out
        assert "Progress: [██████████] 100% GDELT articles (1/1)" in output
        assert "[1/1]" not in output

    @patch("src.GDELT.runner.save_seen")
    @patch("src.GDELT.runner.load_seen")
    @patch("src.GDELT.runner.persist_raw_seeds")
    @patch("src.GDELT.runner.backfill_cyber_seeds")
    @patch("src.GDELT.runner.process_seed")
    @patch("src.GDELT.runner.persist_stage")
    @patch("src.GDELT.runner.ensure_raw_dirs")
    def test_run_verbose_output_shows_detail(
        self,
        mock_ensure_dirs,
        mock_persist_stage,
        mock_process_seed,
        mock_backfill,
        mock_persist_raw,
        mock_load_seen,
        mock_save_seen,
        capsys,
    ):
        """Verbose run output should show the current per-item progress detail."""
        mock_load_seen.return_value = set()
        mock_backfill.return_value = [{"url": "https://example.com/1"}]
        mock_process_seed.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            runner.run(
                num_files=1,
                limit=1,
                subsectors="all",
                output_path=tmpdir,
                verbose=True,
            )

        output = capsys.readouterr().out
        assert "[1/1]" in output
        assert "Progress:" not in output

    def test_run_pause_writes_partial_output_and_preserves_seeds(self):
        """Ctrl-C during processing should write completed records and keep staging."""
        stats = PipelineStats("GDELT")
        seeds = [
            {"url": "https://example.com/1", "source": "test"},
            {"url": "https://example.com/2", "source": "test"},
        ]
        first_record = _make_vuln(
            id_value="first",
            direct_link="https://example.com/1",
            source_name="test",
            title="First Article",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("src.GDELT.runner.ensure_raw_dirs"),
                patch("src.GDELT.runner.load_seen", return_value=set()),
                patch("src.GDELT.runner.save_seen") as mock_save_seen,
                patch("src.GDELT.runner.persist_raw_seeds"),
                patch("src.GDELT.runner.backfill_cyber_seeds", return_value=seeds),
                patch(
                    "src.GDELT.runner.process_seed",
                    side_effect=[first_record, KeyboardInterrupt],
                ),
                patch("src.GDELT.runner.persist_stage"),
                patch("src.GDELT.runner.clear_directory") as mock_clear,
            ):
                result = runner.run(
                    num_files=1,
                    limit=2,
                    subsectors="all",
                    output_path=tmpdir,
                    stats=stats,
                )

            output_file = Path(tmpdir) / "GDELT.json"
            with open(output_file, "r", encoding="utf-8") as f:
                output = json.load(f)

        assert result == [first_record]
        assert output["sources"][0]["id"] == "first"
        assert stats.paused is True
        assert stats.output_records == 1
        mock_save_seen.assert_called_once()
        mock_clear.assert_not_called()

    def test_run_pause_before_records_reports_zero_output(self):
        """Ctrl-C before validation completes should still save state cleanly."""
        stats = PipelineStats("GDELT")
        seeds = [{"url": "https://example.com/1", "source": "test"}]

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("src.GDELT.runner.ensure_raw_dirs"),
                patch("src.GDELT.runner.load_seen", return_value=set()),
                patch("src.GDELT.runner.save_seen") as mock_save_seen,
                patch("src.GDELT.runner.persist_raw_seeds"),
                patch("src.GDELT.runner.backfill_cyber_seeds", return_value=seeds),
                patch(
                    "src.GDELT.runner.process_seed",
                    side_effect=KeyboardInterrupt,
                ),
                patch("src.GDELT.runner.persist_stage"),
                patch("src.GDELT.runner.clear_directory") as mock_clear,
            ):
                result = runner.run(
                    num_files=1,
                    limit=1,
                    subsectors="all",
                    output_path=tmpdir,
                    stats=stats,
                )

            output_file = Path(tmpdir) / "GDELT.json"
            with open(output_file, "r", encoding="utf-8") as f:
                output = json.load(f)

        assert result == []
        assert output == {"sources": []}
        assert stats.paused is True
        assert stats.output_records == 0
        mock_save_seen.assert_called_once()
        mock_clear.assert_not_called()


class TestEnsureRawDirs:
    """Tests for the ensure_raw_dirs function."""

    @patch("src.GDELT.runner.SEEDS_DIR")
    @patch("src.GDELT.runner.VALIDATED_DIR")
    @patch("src.GDELT.runner.ENRICHED_DIR")
    def test_ensure_raw_dirs_creates_all_directories(
        self, mock_enriched, mock_validated, mock_seeds
    ):
        """ensure_raw_dirs should create all required directories."""
        mock_seeds.mkdir = Mock()
        mock_validated.mkdir = Mock()
        mock_enriched.mkdir = Mock()

        runner.ensure_raw_dirs()

        mock_seeds.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_validated.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_enriched.mkdir.assert_called_once_with(parents=True, exist_ok=True)
