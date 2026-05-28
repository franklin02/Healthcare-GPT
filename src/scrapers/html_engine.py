"""Configured HTML scraper runner for healthcare disruption sources.

Each site keeps its own selectors and pagination defaults in ``HTML_SITES``.
The runner accepts optional pagination overrides so the orchestrator can expand
or shrink HTML runs without changing source configuration.
"""

import time
import datetime
import uuid
from pathlib import Path
import sys as _sys
from urllib.parse import urlparse
from bs4 import BeautifulSoup

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

from src.shared_utils import (  # noqa: E402
    AI_MODEL,
    ai_check_validation,
    extract_fields,
    get_page,
    check_valid_file,
    is_known_article,
    prepend_vuln_csv,
    prepend_noise_csv,
    prepend_json_sources,
)  # noqa: E402
from src.classes import Vulnerability, SUBSECTOR_DATA_CLASSES  # noqa: E402
from src.cli_reporter import CliReporter, PipelineStats  # noqa: E402
from src.logging_utils import get_file_logger  # noqa: E402

LOG_FILE = _PROJECT_ROOT / "data" / "logs" / "html_engine.log"
LOGGER = get_file_logger(__name__, LOG_FILE)


def _live_site_status(
    reporter: CliReporter,
    site_name: str,
    page: int,
    stats: PipelineStats,
) -> None:
    """Update the per-site sticky counter line (no-op in verbose mode)."""
    if reporter.verbose:
        return
    reporter.tick(
        site_name,
        page=page,
        processed=stats.processed,
        validated=stats.validated,
        rejected=stats.rejected,
        skipped=stats.skipped,
    )


def _bert_status() -> str:
    """Return a human-readable description of the optional BERT pre-filter."""
    try:
        from src.GDELT.BERT_filter import describe_model

        model_id, device_label = describe_model()
        return f"BERT pre-filter: {model_id} using {device_label}"
    except Exception:
        return "BERT pre-filter: enabled"


try:
    from src.supabase_function import (
        load_cite,
        is_known_db,
        insert_vuln,
        insert_noise,
        has_supabase_creds,
    )

    SUPABASE_AVAILABLE = has_supabase_creds()
    if not SUPABASE_AVAILABLE:
        LOGGER.warning("SUPABASE_URL or SUPABASE_KEY missing; DB writes disabled")
except Exception as e:
    LOGGER.warning("Supabase unavailable, DB writes disabled: %s", e)
    SUPABASE_AVAILABLE = False


SUBSECTOR_FIELDS = [
    "drug_shortage",
    "medical_device_shortage",
    "cyber_attack",
    "natural_disaster",
    "other",
]
HTML_SITES = [
    {
        "name": "CyberScoop",
        "url": "https://cyberscoop.com/?s=&topic=healthcare&content-type=",
        "pagination_url": "https://cyberscoop.com/page/{page}/?s=&topic=healthcare&content-type=",
        "map": {
            "container": "li.search-results__item",
            "title": None,
            "link_selector": "a.post-item__title-link",
            "body_selector": "div.single-article__content",
            "date_selector": "time[datetime]",
            "starting_page": 1,
            "cap": 10,
        },
    },
    {
        "name": "StateScoop",
        "url": "https://statescoop.com/search/healthcare/page/1/",
        "pagination_url": "https://statescoop.com/search/healthcare/page/{page}/",
        "map": {
            "container": "article.post-item",
            "title": None,
            "link_selector": "a.post-item__title-link",
            "body_selector": "div.single-article__content",
            "date_selector": "time[datetime]",
            "starting_page": 1,
            "cap": 7,
        },
    },
    {
        "name": "FedScoop",
        "url": "https://fedscoop.com/search/healthcare/",
        "pagination_url": "https://fedscoop.com/search/healthcare/page/{page}/",
        "map": {
            "container": "article.post-item",
            "title": None,
            "link_selector": "a.post-item__title-link",
            "body_selector": "div.single-article__content",
            "date_selector": "time[datetime]",
            "starting_page": 1,
            "cap": 18,
        },
    },
    # {
    #     "name": "MedicalNewsToday",
    #     "url": "https://www.medicalnewstoday.com/news",
    #     "pagination_url": "https://www.medicalnewstoday.com/news",  # this cite doesnt have pagination
    #     "map": {
    #         "container": "ol li",
    #         "title": None,
    #         "link_selector": "a:has(h2)",
    #         "body_selector": "article.article-body",
    #         "date_selector": "",
    #         "starting_page": 1,
    #         "cap": 1,
    #     },
    # },
    {
        "name": "AHA",
        "url": "https://www.aha.org/news",
        "pagination_url": "https://www.aha.org/news?page=%2C{page}",
        "map": {
            "container": "section.views-latest-feed div.views-row",
            "title": None,
            "link_selector": "div.views-field-title span.field-content a",
            "body_selector": "article .body",
            "date_selector": "time[datetime]",
            "starting_page": 0,
            "cap": 10,
        },
    },
    {
        "name": "HealthIT_News",
        "url": "https://www.techtarget.com/news/health-it",
        "pagination_url": "https://www.techtarget.com/news/health-it/page/{page}",
        "map": {
            "container": "div.topic-related-item-info",
            "title": None,
            "link_selector": "h3 a",
            "body_selector": "article#content-columns",
            "date_selector": "",
            "starting_page": 1,
            "cap": 9,
        },
    },
]


def fetch_html_page(
    site_config,
    page_url,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
):
    """Fetch one listing page and return article payloads plus a stop flag.

    The listing page is parsed with the site's configured selectors, then each
    candidate link is fetched to collect article body text and publication date.
    The stop flag is set when a previously processed article is encountered so
    pagination can end early.
    """
    reporter = reporter or CliReporter(verbose=True)
    response = get_page(page_url)
    soup = BeautifulSoup(response.content, "html.parser")

    m = site_config["map"]
    link_elements = soup.select(m["container"])
    if not link_elements:
        reporter.warn(
            f"container '{m['container']}' matched 0 elements on {page_url}; "
            "check HTML_SITES config",
            stats,
        )
        return [], False

    # Creates a set of valid articles with their respective links
    seen_urls = set()
    raw_links = []
    for el in link_elements:
        if m.get("link_selector"):
            a_tag = el.select_one(m["link_selector"])
        else:
            a_tag = el if el.name == "a" else el.select_one("a[href]")

        if not a_tag:
            reporter.warn(
                f"link_selector '{m.get('link_selector')}' found no anchor "
                f"in a '{m['container']}' item",
                stats,
            )
            continue

        if not a_tag.get("href"):
            continue

        href = a_tag["href"]
        if not href.startswith("http"):
            parsed = urlparse(site_config["url"])
            href = f"{parsed.scheme}://{parsed.netloc}{href}"

        if href in seen_urls:
            continue
        seen_urls.add(href)

        title_text = ""
        if m.get("title"):
            title_el = el.select_one(m["title"])
            title_text = title_el.get_text(strip=True) if title_el else ""
        if not title_text:
            title_text = a_tag.get_text(strip=True)

        if title_text:
            raw_links.append({"title": title_text, "link": href})

    body_selector = m[
        "body_selector"
    ]  # NOTE:  this may need to be moved up to the for loop
    date_selector = m.get("date_selector", "")

    # For each article found, we go to that specific link and grab the body and date (if applicable)
    articles = []
    stop = False
    for entry in raw_links:
        try:
            article_resp = get_page(entry["link"])
            article_soup = BeautifulSoup(article_resp.content, "html.parser")

            body_el = article_soup.select_one(body_selector)
            if not body_el:
                reporter.warn(
                    f"body_selector '{body_selector}' matched nothing on "
                    f"{entry['link']}; skipping article",
                    stats,
                )
                if stats is not None:
                    stats.skipped += 1
                time.sleep(0.5)
                continue

            body = body_el.get_text(separator=" ", strip=True)

            if is_known_article(site_config["name"], entry["title"], body):
                reporter.detail(
                    f"[FINISH] Reached known article on {site_config['name']}: "
                    f"{entry['title']!r}"
                )
                stop = True
                break

            date_el = article_soup.select_one(date_selector) if date_selector else None
            date = date_el.get("datetime", "") if date_el else ""

            time.sleep(0.5)
        except Exception as e:
            reporter.warn(
                f"Could not fetch article body at {entry['link']}: {e}", stats
            )
            if stats is not None:
                stats.skipped += 1
            continue

        articles.append(
            {
                "title": entry["title"],
                "link": entry["link"],
                "body": body,
                "date": date,
            }
        )

    return articles, stop


def run_html_scraper(
    site_config,
    use_bert: bool = False,
    verbose: bool = False,
    starting_page: int | None = None,
    page_cap: int | None = None,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
) -> PipelineStats:
    """Run one configured HTML scraper and return its run statistics.

    Args:
        site_config: One entry from ``HTML_SITES`` containing URL, selector,
            and pagination configuration.
        use_bert: Whether to report that the optional BERT pre-filter is
            enabled for this run.
        verbose: Whether to print per-article progress details when a reporter
            is not supplied.
        starting_page: Optional override for the site's configured first page.
            ``None`` preserves the site's default.
        page_cap: Optional override for the site's configured maximum page.
            ``None`` preserves the site's default; ``-1`` means unlimited.
        reporter: Optional shared CLI reporter supplied by the orchestrator.
        stats: Optional stats object to update for the site.

    Returns:
        The populated ``PipelineStats`` for the site.
    """
    local_reporter = reporter is None
    reporter = reporter or CliReporter(verbose=verbose)
    stats = stats or PipelineStats(site_config["name"])
    stats.sites_scanned += 1
    reporter.phase(f"HTML scraper: {site_config['name']}")
    reporter.status(f"LLM model: {AI_MODEL}")
    if use_bert:
        reporter.status(_bert_status())
    check_valid_file(site_config["name"])

    db_known: list[dict[str, str]] = []
    if SUPABASE_AVAILABLE:
        try:
            db_known = load_cite(site_config["name"])
        except Exception as e:
            reporter.warn(f"load_cite failed for {site_config['name']}: {e}", stats)

    starting_page = (
        starting_page
        if starting_page is not None
        else site_config["map"]["starting_page"]
    )
    cap = page_cap if page_cap is not None else site_config["map"]["cap"]
    current_page = starting_page

    """
    Buffer this run's new vulns + CSV rows so we can prepend them in one shot
    at the end. Order in these lists is newest-first because pagination
    progresses oldest-page-last and each page lists articles newest-first.
    """
    new_vulns: list[Vulnerability] = []
    new_rows: list[list[str]] = []
    new_noise_rows: list[list[str]] = []

    while True:
        if cap != -1 and current_page > cap:
            reporter.info(f"Reached page cap ({cap}) for {site_config['name']}")
            break

        if current_page == starting_page:
            page_url = site_config["url"]
        else:
            pagination_url = site_config.get("pagination_url")
            if pagination_url:
                page_url = pagination_url.replace("{page}", str(current_page))
            else:
                page_param = site_config["map"].get("page_param", "page")
                sep = "&" if "?" in site_config["url"] else "?"
                page_url = f"{site_config['url']}{sep}{page_param}={current_page}"

        try:
            articles, stop = fetch_html_page(
                site_config,
                page_url,
                reporter=reporter,
                stats=stats,
            )
        except Exception as e:
            reporter.error(
                f"Fetching {site_config['name']} page {current_page} ({page_url}): {e}",
                stats,
            )
            return stats

        if not articles:
            reporter.warn(
                f"No articles found on page {current_page}; stopping pagination",
                stats,
            )
            break

        stats.discovered += len(articles)
        for article_index, article in enumerate(articles, start=1):
            body_snippet = (article["body"] or "")[:250].replace("\n", " ")
            stats.processed += 1
            if reporter.verbose:
                reporter.detail(
                    f"[{article_index}/{len(articles)}] {article['title'][:90]}"
                )

            if SUPABASE_AVAILABLE and db_known:
                try:
                    if is_known_db(db_known, article["title"], body_snippet):
                        stats.skipped += 1
                        reporter.detail(
                            f"[SKIP-DB] Already in Supabase: {article['title']}"
                        )
                        _live_site_status(
                            reporter, site_config["name"], current_page, stats
                        )
                        continue
                except Exception as e:
                    reporter.warn(f"is_known_db check failed: {e}", stats)

            try:
                is_threat, detail = ai_check_validation(
                    article["title"], article["body"]
                )
                if is_threat:
                    if detail not in SUBSECTOR_FIELDS:
                        print(
                            f"[WARNING] Unrecognized subsector '{detail}' — skipping: {article['title']}"
                        )
                        continue
                    stats.validated += 1
                    sector_data, ss_data = extract_fields(
                        detail, article["title"], article["body"]
                    )

                    # Wrap the raw dict from the LLM in the matching SubsectorData
                    # subclass so Vulnerability.to_dict() can call .to_dict() on it.
                    subsector_cls = SUBSECTOR_DATA_CLASSES.get(detail)
                    subsector_data = (
                        subsector_cls.from_dict(ss_data) if subsector_cls else None
                    )

                    vuln = Vulnerability(
                        id=str(uuid.uuid4()),
                        title=article["title"],
                        source_name=site_config["name"],
                        direct_link=article["link"],
                        subsector=detail,
                        date_accessed=datetime.datetime.now().strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        date_published=article.get("date", ""),
                        content=article["body"],
                        exec_summary=sector_data.get("exec_summary") or "",
                        geography_scope=sector_data.get("geography_scope"),
                        start_date=sector_data.get("start_date"),
                        end_date=sector_data.get("end_date"),
                        resilience_or_mitigation_observed=sector_data.get(
                            "resilience_or_mitigation_observed"
                        ),
                        subsector_data=subsector_data,
                    )

                    content_preview = (vuln.content or "")[:250].replace("\n", " ")
                    new_rows.append(
                        [
                            vuln.date_accessed,
                            vuln.date_published,
                            vuln.source_name,
                            vuln.subsector,
                            vuln.title,
                            vuln.direct_link,
                            vuln.exec_summary,
                            content_preview,
                        ]
                    )
                    new_vulns.append(vuln)
                    stats.validated += 1
                    print(f"[VALID] ({vuln.subsector}): {vuln.title}")

                    if SUPABASE_AVAILABLE:
                        try:
                            insert_vuln(vuln)
                        except Exception as e:
                            print(
                                f"[WARNING] insert_vuln failed for {vuln.title!r}: {e}"
                            )
                else:
                    stats.rejected += 1
                    body_preview = (article["body"] or "")[:250].replace("\n", " ")
                    new_noise_rows.append(
                        [
                            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                            site_config["name"],
                            article["title"],
                            article["link"],
                            detail,
                            body_preview,
                        ]
                    )

                    if SUPABASE_AVAILABLE:
                        try:
                            insert_noise(
                                source_name=site_config["name"],
                                title=article["title"],
                                url=article["link"],
                                reason=detail,
                                body_preview=body_preview,
                                date_accessed=datetime.datetime.now().strftime(
                                    "%Y-%m-%d %H:%M"
                                ),
                            )
                        except Exception as e:
                            print(
                                f"[WARNING] insert_noise failed for {article['title']!r}: {e}"
                            )
            except Exception as e:
                print(
                    f"[WARNING] Validation failed for {article.get('title', 'unknown')!r}: {e}"
                )
                continue

        if stop:
            break

        current_page += 1
        time.sleep(0.5)

    reporter.finish_line()
    prepend_vuln_csv(site_config["name"], new_rows)
    prepend_noise_csv(site_config["name"], new_noise_rows)
    prepend_json_sources(site_config["name"], new_vulns)
    stats.output_records += len(new_vulns)
    reporter.info(
        f"Finished {site_config['name']}: "
        f"{len(new_vulns)} vuln(s), {len(new_noise_rows)} rejected"
    )
    if local_reporter:
        reporter.summary(stats)
    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="HTML scraper for healthcare news sites"
    )
    parser.add_argument(
        "--use-bert",
        action="store_true",
        default=False,
        help="Run BERT pre-filter before LLM validation to skip unrelated articles early",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show detailed per-article scraper output",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=None,
        help="Override configured starting page for every HTML site",
    )
    parser.add_argument(
        "--page-cap",
        type=int,
        default=None,
        help="Override configured max page number for every HTML site (-1 for unlimited)",
    )
    args = parser.parse_args()

    for site in HTML_SITES:
        run_html_scraper(
            site,
            use_bert=args.use_bert,
            verbose=args.verbose,
            starting_page=args.start_page,
            page_cap=args.page_cap,
        )
