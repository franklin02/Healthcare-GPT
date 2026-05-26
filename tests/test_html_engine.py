from src.scrapers import html_engine


def test_run_html_scraper_checks_model_before_article_fetch(monkeypatch):
    """HTML scraper should check Ollama model availability before fetching pages."""
    events = []
    site = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {"starting_page": 1, "cap": 1},
    }

    monkeypatch.setattr(
        html_engine,
        "ensure_model_available",
        lambda: events.append("check"),
    )
    monkeypatch.setattr(
        html_engine,
        "check_valid_file",
        lambda site_name: events.append("check_file"),
    )
    monkeypatch.setattr(
        html_engine,
        "fetch_html_page",
        lambda site_config, page_url: events.append("fetch") or ([], False),
    )
    monkeypatch.setattr(html_engine, "prepend_vuln_csv", lambda *args: None)
    monkeypatch.setattr(html_engine, "prepend_noise_csv", lambda *args: None)
    monkeypatch.setattr(html_engine, "prepend_json_sources", lambda *args: None)

    html_engine.run_html_scraper(site)

    assert events == ["check", "check_file", "fetch"]
