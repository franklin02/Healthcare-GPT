from unittest.mock import Mock, patch
import io
import zipfile
import pytest
import src.GDELT.gdelt_seeds as gdelt_seeds
from src.GDELT.sector_themes import SECTOR_THEMES


@pytest.fixture
def mock_get():
    with patch("src.GDELT.gdelt_seeds.requests.get") as mock:
        yield mock


@pytest.fixture
def mock_process():
    with patch("src.GDELT.gdelt_seeds.process_gkg_file") as mock:
        yield mock


class TestIsUsLocated:
    """Tests for is_us_located function."""

    def test_us_location_present(self):
        """Should return True when US location is in the string (GDELT format: number#name#code)."""
        assert gdelt_seeds.is_us_located("1#New York#US#123#40.7#74.0") is True
        assert gdelt_seeds.is_us_located("2#California#US#456#36.1#120.0") is True

    def test_multiple_locations_with_us(self):
        """Should return True when US is among multiple locations."""
        assert (
            gdelt_seeds.is_us_located(
                "1#London#GB#100#51.5#0.1;2#New York#US#200#40.7#74.0;3#Paris#FR#300#48.8#2.3"
            )
            is True
        )

    def test_non_us_location(self):
        """Should return False for non-US locations."""
        assert gdelt_seeds.is_us_located("1#London#GB#100#51.5#0.1") is False
        assert gdelt_seeds.is_us_located("2#Paris#FR#200#48.8#2.3") is False

    def test_multiple_non_us_locations(self):
        """Should return False when no US location is present."""
        assert (
            gdelt_seeds.is_us_located(
                "1#London#GB#100#51.5#0.1;2#Paris#FR#200#48.8#2.3"
            )
            is False
        )

    def test_empty_string(self):
        """Should return True for empty string."""
        assert gdelt_seeds.is_us_located("") is True

    def test_none_input(self):
        """Should return True for None input."""
        assert gdelt_seeds.is_us_located(None) is True

    def test_malformed_location_string_less_than_3_parts(self):
        """Should return True for malformed strings with less than 3 parts (default to True)."""
        assert gdelt_seeds.is_us_located("London#GB") is False

    def test_case_insensitive_us(self):
        """Should handle lowercase 'us'."""
        assert gdelt_seeds.is_us_located("1#New York#us#123#40.7#74.0") is True


class TestMatchesAnyTheme:
    """Tests for _matches_any_theme helper function."""

    def test_exact_theme_match(self):
        """Should match exact theme in set."""
        themes = {"CYBER_ATTACK", "RANSOMWARE"}
        assert gdelt_seeds._matches_any_theme("CYBER_ATTACK", themes) is True

    def test_partial_theme_match(self):
        """Should match partial theme."""
        themes = {"CYBER_ATTACK"}
        assert gdelt_seeds._matches_any_theme("MY_CYBER_ATTACK_EVENT", themes) is True

    def test_multiple_themes_with_match(self):
        """Should match when one of multiple themes matches."""
        themes = {"CYBER_ATTACK", "RANSOMWARE"}
        assert (
            gdelt_seeds._matches_any_theme("PHISHING;CYBER_ATTACK;MALWARE", themes)
            is True
        )

    def test_no_match(self):
        """Should return False when theme doesn't match."""
        themes = {"CYBER_ATTACK"}
        assert gdelt_seeds._matches_any_theme("SPORTS;GAMES", themes) is False

    def test_case_insensitive(self):
        """Should be case insensitive."""
        themes = {"CYBER_ATTACK"}
        assert gdelt_seeds._matches_any_theme("cyber_attack", themes) is True

    def test_none_input(self):
        """Should return False for None input."""
        themes = {"CYBER_ATTACK"}
        assert gdelt_seeds._matches_any_theme(None, themes) is False

    def test_non_string_input(self):
        """Should return False for non-string input."""
        themes = {"CYBER_ATTACK"}
        assert gdelt_seeds._matches_any_theme(123, themes) is False

    def test_empty_theme_set(self):
        """Should return False for empty theme set."""
        assert gdelt_seeds._matches_any_theme("CYBER_ATTACK", set()) is False


def _gkg_zip_from_rows(*rows: str) -> bytes:
    """Build a minimal GDELT GKG zip from tab-separated CSV rows."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("test.csv", "".join(rows))
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def _gkg_row(themes: str, url: str = "https://hospital.com/cyber") -> str:
    """Build a minimal GDELT GKG row with specified themes and URL."""
    return (
        f"1\t20230515123045\t2\tBBC\t{url}\t5\t6\t"
        f"{themes}\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
    )


class TestSectorThemeRegistry:
    """Tests for the sector theme registry."""

    def test_health_and_test_sectors_registered(self):
        """Should have 'health' and 'test' sectors registered."""
        assert "health" in SECTOR_THEMES
        assert "test" in SECTOR_THEMES

    def test_sector_entries_are_sector_and_subsector_pairs(self):
        """Each sector entry should be a tuple of (sector_themes, subsector_themes)."""
        for sector, config in SECTOR_THEMES.items():
            sector_themes, subsector_themes = config
            assert sector_themes
            assert subsector_themes
            assert all(isinstance(themes, set) for themes in subsector_themes.values())


class TestThemesMatchBySector:
    """Tests for sector-scoped themes_match."""

    def test_health_requires_sector_and_subsector_themes(self):
        """Should require both sector and subsector themes for health sector."""
        assert gdelt_seeds.themes_match("HEALTHCARE;CYBER_ATTACK", "health") is True
        assert gdelt_seeds.themes_match("HEALTHCARE", "health") is False
        assert gdelt_seeds.themes_match("CYBER_ATTACK", "health") is False

    def test_test_sector_requires_sector_and_subsector_themes(self):
        """Should require both sector and subsector themes for test sector."""
        assert (
            gdelt_seeds.themes_match("TEST_THEME_1;TEST_SUBSECTOR_1_THEME_1", "test")
            is True
        )
        assert gdelt_seeds.themes_match("TEST_THEME_1", "test") is False
        assert gdelt_seeds.themes_match("TEST_SUBSECTOR_1_THEME_1", "test") is False

    def test_health_themes_do_not_match_test_sector(self):
        """Should not match health themes under test sector."""
        assert gdelt_seeds.themes_match("HEALTHCARE;CYBER_ATTACK", "test") is False

    def test_test_themes_do_not_match_health_sector(self):
        """Should not match test themes under health sector."""
        assert (
            gdelt_seeds.themes_match("TEST_THEME_1;TEST_SUBSECTOR_1_THEME_1", "health")
            is False
        )

    def test_unknown_sector_defaults_to_health(self):
        """Should default to health sector when unknown sector is provided."""
        assert gdelt_seeds.themes_match("HEALTHCARE;SHORTAGE", "unknown") is True
        assert (
            gdelt_seeds.themes_match("TEST_THEME_1;TEST_SUBSECTOR_1_THEME_1", "unknown")
            is False
        )

    def test_none_sector_defaults_to_health(self):
        """Should default to health sector when no sector is provided."""
        assert gdelt_seeds.themes_match("HEALTHCARE;SHORTAGE", None) is True


class TestDetectSubsectorBySector:
    """Tests for sector-scoped subsector detection."""

    def test_test_sector_detects_matching_subsector(self):
        assert (
            gdelt_seeds.detect_subsector(
                "TEST_THEME_2;TEST_SUBSECTOR_2_THEME_1", "test"
            )
            == "subsector_2"
        )

    def test_test_sector_detects_multiple_subsectors(self):
        themes = "TEST_THEME_1;TEST_SUBSECTOR_1_THEME_1;TEST_SUBSECTOR_2_THEME_2"
        assert gdelt_seeds.detect_subsectors(themes, "test") == [
            "subsector_1",
            "subsector_2",
        ]

    def test_health_subsector_not_detected_under_test_sector(self):
        """Should not detect health subsector under test sector."""
        assert gdelt_seeds.detect_subsector("HEALTHCARE;CYBER_ATTACK", "test") is None

    def test_test_subsector_not_detected_under_health_sector(self):
        """Should not detect test subsector under health sector."""
        assert (
            gdelt_seeds.detect_subsector(
                "TEST_THEME_1;TEST_SUBSECTOR_1_THEME_1", "health"
            )
            is None
        )


class TestProcessGkgFileBySector:
    """Tests for sector argument in process_gkg_file."""

    def test_test_sector_filters_rows_by_test_themes(self, mock_get):
        csv_content = (
            _gkg_row("TEST_THEME_1;TEST_SUBSECTOR_1_THEME_1")
            + _gkg_row("HEALTHCARE;CYBER_ATTACK")
            + _gkg_row("TEST_THEME_2")
        )
        response = Mock()
        response.content = _gkg_zip_from_rows(csv_content)
        response.raise_for_status = Mock()
        mock_get.return_value = response

        seeds, total = gdelt_seeds.process_gkg_file(
            "http://example.com/test.zip", sector="test"
        )

        assert total == 3
        assert len(seeds) == 1
        assert seeds[0]["subsector"] == "subsector_1"
        assert "TEST_SUBSECTOR_1_THEME_1" in seeds[0]["themes"]

    def test_health_sector_ignores_test_sector_rows(self, mock_get):
        """Should ignore test sector rows when processing health sector."""
        csv_content = _gkg_row("TEST_THEME_1;TEST_SUBSECTOR_1_THEME_1") + _gkg_row(
            "HEALTHCARE;CYBER_ATTACK"
        )
        response = Mock()
        response.content = _gkg_zip_from_rows(csv_content)
        response.raise_for_status = Mock()
        mock_get.return_value = response

        seeds, total = gdelt_seeds.process_gkg_file(
            "http://example.com/test.zip", sector="health"
        )

        assert total == 2
        assert len(seeds) == 1
        assert seeds[0]["subsector"] == "cyber_attack"


class TestBackfillSeedsBySector:
    """Tests for sector argument in backfill_seeds."""

    def test_backfill_forwards_sector_to_process_gkg_file(self, mock_get, mock_process):
        master_list = (
            "20230515000000 200 http://example.com/20230515000000.gkg.csv.zip\n"
        )
        response = Mock()
        response.text = master_list
        response.raise_for_status = Mock()
        mock_get.return_value = response
        mock_process.return_value = ([], 0)

        gdelt_seeds.backfill_seeds(num_files=1, sector="test")

        mock_process.assert_called_once()
        assert mock_process.call_args.kwargs["sector"] == "test"


class TestDetectSubsector:
    """Tests for detect_subsector function."""

    def test_detect_cyber_attack(self):
        """Should detect cyber_attack subsector."""
        assert gdelt_seeds.detect_subsector("HEALTHCARE;CYBER_ATTACK") == "cyber_attack"

    def test_detect_drug_shortage(self):
        """Should detect drug_shortage subsector."""
        assert gdelt_seeds.detect_subsector("HEALTHCARE;SHORTAGE") == "drug_shortage"

    def test_detect_medical_device_shortage(self):
        """Should detect medical_device_shortage subsector."""
        assert (
            gdelt_seeds.detect_subsector("HEALTHCARE;MEDICAL_EQUIPMENT")
            == "medical_device_shortage"
        )

    def test_detect_natural_disaster(self):
        """Should detect natural_disaster subsector."""
        assert (
            gdelt_seeds.detect_subsector("HEALTHCARE;NATURAL_DISASTER")
            == "natural_disaster"
        )

    def test_no_health_theme_returns_none(self):
        """Should return None if no HEALTH_THEMES present."""
        assert gdelt_seeds.detect_subsector("CYBER_ATTACK;SPORTS") is None

    def test_no_matching_subsector_returns_none(self):
        """Should return None if no matching subsector theme."""
        assert gdelt_seeds.detect_subsector("HEALTHCARE;SPORTS") is None

    def test_none_input_returns_none(self):
        """Should return None for None input."""
        assert gdelt_seeds.detect_subsector(None) is None


class TestThemesMatchNoise:
    """Tests for themes_match_noise function."""

    def test_sports_noise(self):
        """Should detect SPORTS noise."""
        assert gdelt_seeds.themes_match_noise("SPORTS") is True

    def test_games_esports_noise(self):
        """Should detect GAMES_ESPORTS noise."""
        assert gdelt_seeds.themes_match_noise("GAMES_ESPORTS") is True

    def test_env_noise(self):
        """Should detect ENV_ prefixed noise."""
        assert gdelt_seeds.themes_match_noise("ENV_CLIMATE") is True

    def test_tourism_noise(self):
        """Should detect TOURISM noise."""
        assert gdelt_seeds.themes_match_noise("TOURISM") is True

    def test_education_noise(self):
        """Should detect EDUCATION_UNIVERSITY noise."""
        assert gdelt_seeds.themes_match_noise("EDUCATION_UNIVERSITY") is True

    def test_multiple_themes_with_noise(self):
        """Should detect noise in multiple themes."""
        assert gdelt_seeds.themes_match_noise("HEALTHCARE;SPORTS;CYBER_ATTACK") is True

    def test_no_noise(self):
        """Should return False for non-noise themes."""
        assert gdelt_seeds.themes_match_noise("HEALTHCARE;CYBER_ATTACK") is False

    def test_none_input(self):
        """Should return False for None input."""
        assert gdelt_seeds.themes_match_noise(None) is False

    def test_non_string_input(self):
        """Should return False for non-string input."""
        assert gdelt_seeds.themes_match_noise(123) is False


class TestUrlPassesQuality:
    """Tests for url_passes_quality function."""

    def test_valid_us_hospital_url(self):
        """Should pass valid US hospital URL."""
        assert (
            gdelt_seeds.url_passes_quality("https://hospital.com/cyber/breach-detected")
            is True
        )

    def test_valid_us_healthcare_url(self):
        """Should pass valid US healthcare URL."""
        assert (
            gdelt_seeds.url_passes_quality("https://healthcare.org/security/ransomware")
            is True
        )

    def test_blocked_tld_russia(self):
        """Should reject .ru domain."""
        assert gdelt_seeds.url_passes_quality("https://hospital.ru/cyber") is False

    def test_blocked_tld_china(self):
        """Should reject .cn domain."""
        assert gdelt_seeds.url_passes_quality("https://hospital.cn/cyber") is False

    def test_non_us_tld(self):
        """Should reject non-US TLD."""
        assert gdelt_seeds.url_passes_quality("https://hospital.jp/cyber") is False

    def test_missing_required_pattern(self):
        """Should reject URL without required pattern."""
        assert (
            gdelt_seeds.url_passes_quality("https://example.com/general/info") is False
        )

    def test_deny_pattern_sports(self):
        """Should reject URL with sports pattern."""
        assert (
            gdelt_seeds.url_passes_quality("https://hospital.com/sports/event") is False
        )

    def test_deny_pattern_weather(self):
        """Should reject URL with weather pattern."""
        assert (
            gdelt_seeds.url_passes_quality("https://hospital.com/weather/forecast")
            is False
        )

    def test_non_http_url(self):
        """Should reject non-HTTP URL."""
        assert gdelt_seeds.url_passes_quality("ftp://hospital.com/cyber") is False

    def test_none_input(self):
        """Should return False for None input."""
        assert gdelt_seeds.url_passes_quality(None) is False

    def test_empty_string(self):
        """Should return False for empty string."""
        assert gdelt_seeds.url_passes_quality("") is False

    def test_gov_domain(self):
        """Should accept .gov domain."""
        assert (
            gdelt_seeds.url_passes_quality("https://hospital.gov/cyber/security")
            is True
        )

    def test_edu_domain(self):
        """Should accept .edu domain."""
        assert (
            gdelt_seeds.url_passes_quality("https://hospital.edu/medical/hack") is True
        )


class TestNormalizeDateBound:
    """Tests for _normalize_date_bound function."""

    def test_yyyymmdd_format_start(self):
        """Should normalize YYYYMMDD to start of day."""
        result = gdelt_seeds._normalize_date_bound("20230515", end=False)
        assert result == "20230515000000"

    def test_yyyymmdd_format_end(self):
        """Should normalize YYYYMMDD to end of day."""
        result = gdelt_seeds._normalize_date_bound("20230515", end=True)
        assert result == "20230515235959"

    def test_yyyymmddhhmiss_format(self):
        """Should pass through YYYYMMDDHHMISS format."""
        result = gdelt_seeds._normalize_date_bound("20230515123045")
        assert result == "20230515123045"

    def test_iso_date_format(self):
        """Should normalize ISO date format."""
        result = gdelt_seeds._normalize_date_bound("2023-05-15")
        assert result == "20230515000000"

    def test_iso_datetime_format(self):
        """Should normalize ISO datetime format."""
        result = gdelt_seeds._normalize_date_bound("2023-05-15T12:30:45")
        assert result == "20230515123045"

    def test_iso_format_with_z(self):
        """Should handle ISO format with Z timezone."""
        result = gdelt_seeds._normalize_date_bound("2023-05-15T12:30:45Z")
        assert result == "20230515123045"

    def test_none_input(self):
        """Should return None for None input."""
        assert gdelt_seeds._normalize_date_bound(None) is None

    def test_empty_string(self):
        """Should return None for empty string."""
        assert gdelt_seeds._normalize_date_bound("") is None

    def test_invalid_format(self):
        """Should return original value for unrecognized format."""
        result = gdelt_seeds._normalize_date_bound("invalid")
        assert result == "invalid"


class TestProcessGkgFile:
    """Tests for process_gkg_file function."""

    def test_successful_processing(self, mock_get):
        """Should successfully process a valid GKG file."""
        # Create mock CSV content
        csv_content = (
            "1\t20230515123045\t2\tBBC\thttps://hospital.com/cyber\t5\t6\t"
            "HEALTHCARE;CYBER_ATTACK\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
        )

        # Create mock zip file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("test.csv", csv_content)
        zip_buffer.seek(0)

        mock_response = Mock()
        mock_response.content = zip_buffer.getvalue()
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        seeds, total = gdelt_seeds.process_gkg_file("http://example.com/test.zip")

        assert isinstance(seeds, list)
        assert total > 0

    def test_download_failed(self, mock_get):
        """Should return empty list on download failure."""
        mock_get.side_effect = Exception("Network error")

        seeds, total = gdelt_seeds.process_gkg_file("http://invalid.com/test.zip")

        assert seeds == []
        assert total == 0

    def test_insufficient_columns(self, mock_get):
        """Should skip file with insufficient columns."""
        csv_content = "1\t2\t3\t4\t5\n"  # Only 5 columns, need 16

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("test.csv", csv_content)
        zip_buffer.seek(0)

        mock_response = Mock()
        mock_response.content = zip_buffer.getvalue()
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        seeds, total = gdelt_seeds.process_gkg_file("http://example.com/test.zip")

        assert seeds == []


class TestBackfillCyberSeeds:
    """Tests for backfill_cyber_seeds function."""

    def test_successful_backfill(self, mock_get, mock_process):
        """Should successfully backfill seeds."""
        # Mock master file list
        master_list = (
            "20230514120000 100 http://example.com/20230514120000.gkg.csv.zip\n"
            "20230515000000 200 http://example.com/20230515000000.gkg.csv.zip\n"
        )

        response = Mock()
        response.text = master_list
        response.raise_for_status = Mock()
        mock_get.return_value = response

        mock_seeds = [
            {
                "date": "20230515",
                "source": "BBC",
                "url": "https://hospital.com/cyber",
                "themes": "HEALTHCARE;CYBER_ATTACK",
            }
        ]
        mock_process.return_value = (mock_seeds, 100)

        result = gdelt_seeds.backfill_seeds(num_files=2)

        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]["url"] == "https://hospital.com/cyber"
        mock_get.assert_called()
        mock_process.assert_called()

    def test_date_range_filtering(self, mock_get, mock_process):
        """Should filter by date range."""
        master_list = (
            "20230510000000 100 http://example.com/20230510000000.gkg.csv.zip\n"
            "20230515000000 200 http://example.com/20230515000000.gkg.csv.zip\n"
            "20230520000000 300 http://example.com/20230520000000.gkg.csv.zip\n"
        )

        response = Mock()
        response.text = master_list
        response.raise_for_status = Mock()
        mock_get.return_value = response

        mock_process.return_value = ([], 0)

        result = gdelt_seeds.backfill_seeds(
            num_files=3, start_date="20230515", end_date="20230515"
        )

        assert isinstance(result, list)
        mock_process.assert_called_once()


class TestProcessGkgFileFilters:
    """Tests for filter logic in process_gkg_file."""

    def test_subsector_and_noise_filter(self, mock_get):
        """Should filter by subsector themes and exclude noise."""
        csv_content = (
            "1\t20230515123045\t2\tBBC\thttps://hospital.com/cyber\t5\t6\t"
            "CYBER_ATTACK;HEALTHCARE\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
            "2\t20230515123046\t3\tCNN\thttps://example.com/news\t5\t6\t"
            "SPORTS;ENTERTAINMENT\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
        )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("test.csv", csv_content)
        zip_buffer.seek(0)

        response = Mock()
        response.content = zip_buffer.getvalue()
        response.raise_for_status = Mock()
        mock_get.return_value = response

        seeds, total = gdelt_seeds.process_gkg_file("http://example.com/test.zip")

        assert total == 2
        assert len(seeds) == 1
        assert "CYBER_ATTACK" in seeds[0]["themes"]

    def test_all_subsector_seeds_include_detected_subsectors(self, mock_get):
        """subsector=all seeds should preserve all GDELT-detected subsectors."""
        csv_content = (
            "1\t20230515123045\t2\tBBC\thttps://hospital.com/cyber-shortage\t5\t6\t"
            "HEALTHCARE;CYBER_ATTACK;SHORTAGE\t8\t1#New York#US#123#40.7#74.0\t"
            "10\t11\t12\t13\t14\t15\n"
        )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("test.csv", csv_content)
        zip_buffer.seek(0)

        response = Mock()
        response.content = zip_buffer.getvalue()
        response.raise_for_status = Mock()
        mock_get.return_value = response

        seeds, total = gdelt_seeds.process_gkg_file("http://example.com/test.zip")

        assert total == 1
        assert len(seeds) == 1
        assert seeds[0]["subsector"] == "drug_shortage"
        assert seeds[0]["detected_subsectors"] == [
            "drug_shortage",
            "cyber_attack",
        ]

    def test_us_location_filter(self, mock_get):
        """Should filter by US location."""
        csv_content = (
            "1\t20230515123045\t2\tBBC\thttps://hospital.com/cyber\t5\t6\t"
            "CYBER_ATTACK;HEALTHCARE\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
            "2\t20230515123046\t3\tCNN\thttps://example.com/attack\t5\t6\t"
            "CYBER_ATTACK;HEALTHCARE\t8\t1#London#GB#123#51.5#0.1\t10\t11\t12\t13\t14\t15\n"
        )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("test.csv", csv_content)
        zip_buffer.seek(0)

        response = Mock()
        response.content = zip_buffer.getvalue()
        response.raise_for_status = Mock()
        mock_get.return_value = response

        seeds, total = gdelt_seeds.process_gkg_file("http://example.com/test.zip")

        assert total == 2
        assert len(seeds) == 1
        assert seeds[0]["source"] == "BBC"

    def test_all_rows_filtered_by_theme(self, mock_get):
        """Should return empty when all rows filtered by theme filter."""
        csv_content = (
            "1\t20230515123045\t2\tBBC\thttps://hospital.com/cyber\t5\t6\t"
            "SPORTS;ENTERTAINMENT\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
            "2\t20230515123046\t3\tCNN\thttps://example.com/attack\t5\t6\t"
            "GAMES;TOURISM\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
        )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("test.csv", csv_content)
        zip_buffer.seek(0)

        response = Mock()
        response.content = zip_buffer.getvalue()
        response.raise_for_status = Mock()
        mock_get.return_value = response

        seeds, total = gdelt_seeds.process_gkg_file("http://example.com/test.zip")

        assert total == 2
        assert len(seeds) == 0

    def test_all_rows_filtered_by_location(self, mock_get):
        """Should return empty when all rows filtered by location filter."""
        csv_content = (
            "1\t20230515123045\t2\tBBC\thttps://hospital.com/cyber\t5\t6\t"
            "CYBER_ATTACK;HEALTHCARE\t8\t1#London#GB#123#51.5#0.1\t10\t11\t12\t13\t14\t15\n"
            "2\t20230515123046\t3\tCNN\thttps://example.com/attack\t5\t6\t"
            "CYBER_ATTACK;HEALTHCARE\t8\t1#Paris#FR#123#48.8#2.3\t10\t11\t12\t13\t14\t15\n"
        )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("test.csv", csv_content)
        zip_buffer.seek(0)

        response = Mock()
        response.content = zip_buffer.getvalue()
        response.raise_for_status = Mock()
        mock_get.return_value = response

        seeds, total = gdelt_seeds.process_gkg_file("http://example.com/test.zip")

        # Both rows should pass theme filter but be filtered out by location filter
        assert total == 2
        assert len(seeds) == 0

    def test_all_rows_filtered_by_url_quality(self, mock_get):
        """Should return empty when all rows filtered by URL quality filter."""
        csv_content = (
            "1\t20230515123045\t2\tBBC\thttp://bit.ly/short1\t5\t6\t"
            "CYBER_ATTACK;HEALTHCARE\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
            "2\t20230515123046\t3\tCNN\thttp://tinyurl.com/short2\t5\t6\t"
            "CYBER_ATTACK;HEALTHCARE\t8\t1#New York#US#123#40.7#74.0\t10\t11\t12\t13\t14\t15\n"
        )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("test.csv", csv_content)
        zip_buffer.seek(0)

        response = Mock()
        response.content = zip_buffer.getvalue()
        response.raise_for_status = Mock()
        mock_get.return_value = response

        seeds, total = gdelt_seeds.process_gkg_file("http://example.com/test.zip")

        # Both rows pass theme and location filters but fail URL quality (shorteners and non-US TLD)
        assert total == 2
        assert len(seeds) == 0


class TestConstantDefinitions:
    """Tests for constant definitions."""

    def test_us_tlds(self):
        """Should have US_TLDS defined."""
        assert isinstance(gdelt_seeds.US_TLDS, set)
        assert ".com" in gdelt_seeds.US_TLDS
        assert ".gov" in gdelt_seeds.US_TLDS

    def test_blocked_tlds(self):
        """Should have BLOCKED_TLDS defined."""
        assert isinstance(gdelt_seeds.BLOCKED_TLDS, set)
        assert ".ru" in gdelt_seeds.BLOCKED_TLDS
        assert ".cn" in gdelt_seeds.BLOCKED_TLDS
