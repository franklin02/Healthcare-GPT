"""Configured HTML scraper runner for healthcare disruption sources.

Each site keeps its own selectors and pagination defaults in ``HTML_SITES``.
The runner accepts optional pagination overrides so the orchestrator can expand
or shrink HTML runs without changing source configuration.
"""

import datetime
import time
import uuid
import argparse
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from src.classes import SUBSECTOR_DATA_CLASSES, Vulnerability
from src.cli_reporter import CliReporter, PipelineStats
from src.logging_utils import get_file_logger
from src.shared_utils import (
    AI_MODEL,
    NOISE_CSV_HEADER,
    VULN_CSV_HEADER,
    ai_check_validation,
    ensure_model_available,
    extract_fields,
    get_config_bool,
    get_config_date,
    get_config_int,
    get_page,
    model_unavailable_error,
    _PROJECT_ROOT,
)

LOGGER = get_file_logger(__name__, _PROJECT_ROOT / "data" / "logs" / "scooper.log")

try:
    from src.dedup import handle_vuln
    from src.supabase_function import (
        load_cite,
        is_known_db,
        insert_noise,
        has_supabase_creds,
    )

    SUPABASE_AVAILABLE = has_supabase_creds()
    if not SUPABASE_AVAILABLE:
        LOGGER.warning("SUPABASE_URL or SUPABASE_KEY missing; DB writes disabled")
except Exception as e:
    LOGGER.warning("Supabase unavailable, DB writes disabled: %s", e)
    SUPABASE_AVAILABLE = False


# Consolidated local corpus: one vuln CSV and one noise CSV for all sites. scooper
# only READS these; the orchestrator owns writing them back.
VULN_CSV_PATH = _PROJECT_ROOT / "data" / "vulnerabilities" / "vulnerabilities.csv"
NOISE_CSV_PATH = _PROJECT_ROOT / "data" / "noise" / "noise.csv"


def _load_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    """Read a consolidated CSV into a DataFrame.

    Returns an empty DataFrame with the expected ``columns`` when the file does
    not exist yet (first run) or contains no rows, so callers always get the
    expected schema.
    """
    if path.exists():
        try:
            return pd.read_csv(path, dtype=str).fillna("")
        except pd.errors.EmptyDataError:
            return pd.DataFrame(columns=columns)
    return pd.DataFrame(columns=columns)


def _build_seen_keys(
    vuln_df: pd.DataFrame, noise_df: pd.DataFrame
) -> set[tuple[str, str]]:
    """Build an O(1) ``(source_name, link)`` lookup set from both corpora.

    Vuln rows key on ``direct_link`` and noise rows on ``url`` (the column names
    differ across the two CSV schemas but both hold the article link).
    """
    keys = set(zip(vuln_df["source_name"], vuln_df["direct_link"]))
    keys |= set(zip(noise_df["source_name"], noise_df["url"]))
    return keys


def _assemble_df(
    loaded_df: pd.DataFrame, new_rows: list[list[str]], columns: list[str]
) -> pd.DataFrame:
    """Append newly collected rows to the loaded DataFrame (loaded first)."""
    if not new_rows:
        return loaded_df
    return pd.concat(
        [loaded_df, pd.DataFrame(new_rows, columns=columns)],
        ignore_index=True,
    )


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
        LOGGER.info("BERT pre-filter enabled: %s on %s", model_id, device_label)
        return f"BERT pre-filter: {model_id} using {device_label}"
    except Exception:
        LOGGER.warning(
            "Could not load BERT filter for status description", exc_info=True
        )
        return "BERT pre-filter: enabled"


SUBSECTOR_FIELDS = list(SUBSECTOR_DATA_CLASSES.keys())


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
    {
        "name": "MedicalNewsToday",
        "url": "https://www.medicalnewstoday.com/news",
        "pagination_url": "https://www.medicalnewstoday.com/news",  # this cite doesnt have pagination
        "map": {
            "container": "ol li",
            "title": None,
            "link_selector": "a:has(h2)",
            "body_selector": "article.article-body",
            "date_selector": "",
            "starting_page": 1,
            "cap": 1,
        },
    },
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
    """
    Fetch one listing page and return its article payloads.

    The listing page is parsed with the site's configured selectors, then each
    candidate link is fetched to collect article body text and publication date.
    Dedup against the local corpus happens in ``run_html_scraper`` (set-based),
    so this function performs no known-article check and never short-circuits.
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
        LOGGER.warning(
            "container '%s' matched 0 elements on %s; check HTML_SITES config",
            m["container"],
            page_url,
        )
        return []

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

    body_selector = m["body_selector"]
    date_selector = m.get("date_selector", "")

    # For each article found, we go to that specific link and grab the body and date (if applicable)
    articles = []
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
                continue

            body = body_el.get_text(separator=" ", strip=True)

            date_el = article_soup.select_one(date_selector) if date_selector else None
            date = date_el.get("datetime", "") if date_el else ""

            time.sleep(0.25)
        except Exception as e:
            reporter.warn(
                f"Could not fetch article body at {entry['link']}: {e}", stats
            )
            LOGGER.warning("Error fetching article body:%s", e)
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

    LOGGER.info("Fetched %d articles from %s", len(articles), page_url)
    return articles


def run_html_scraper(
    site_config,
    use_bert: bool = False,
    verbose: bool = False,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    sb_only: bool = False,
) -> tuple[PipelineStats, pd.DataFrame, pd.DataFrame]:
    """
    Run one configured HTML scraper and return its stats and corpus DataFrames.

    Args:
        site_config: One entry from ``HTML_SITES`` containing URL, selector,
            and pagination configuration.
        use_bert: Whether to report that the optional BERT pre-filter is
            enabled for this run.
        verbose: Whether to print per-article progress details when a reporter
            is not supplied.
        start_date: Newest article date to keep (inclusive, ceiling). Pages list
            newest-first, so articles published after this date are skipped while
            crawling continues toward the window. ``None`` means no upper bound.
        end_date: Oldest article date to keep (inclusive, floor). When an article
            published before this date is reached, crawling stops because nothing
            older qualifies. ``None`` means no lower bound.
        reporter: Optional shared CLI reporter supplied by the orchestrator.
        stats: Optional stats object to update for the site.

    Returns:
        A tuple of the populated ``PipelineStats`` and two in-memory DataFrames
        (vuln, noise): the loaded consolidated CSVs plus any rows collected this
        run. In sb_only mode the DataFrames are just the loaded corpus (or empty)
        with no new local rows. scooper never writes files; the orchestrator
        persists the returned DataFrames.

    Interrupt behavior:
        Pressing ``Ctrl-C`` during page fetch, article validation, extraction,
        or inter-page delay marks the site stats as paused and returns the
        collected DataFrames so the orchestrator can skip remaining sites. No
        flush is needed because results are held in memory.
    """
    local_reporter = reporter is None
    reporter = reporter or CliReporter(verbose=verbose)
    stats = stats or PipelineStats(site_config["name"])
    stats.sites_scanned += 1
    reporter.phase(f"HTML scraper: {site_config['name']}")
    reporter.status(f"LLM model: {AI_MODEL}")
    if use_bert:
        reporter.status(_bert_status())
    try:
        ensure_model_available()
    except model_unavailable_error as exc:
        LOGGER.error("Model availability check failed: %s", exc)
        raise
    # Resolve the effective mode before any file/DB work: if sb_only was
    # requested without creds, fall back to local so the guards below pick the
    # right side.
    if not SUPABASE_AVAILABLE and sb_only:
        sb_only = False
        LOGGER.warning(
            "sb_only was selected, but no Supabase keys are found; "
            "falling back to local-only writes."
        )

    # Load the consolidated corpus into memory and build the O(1) dedup set.
    # This is read-only: in sb_only mode the DataFrames are returned untouched.
    loaded_vuln_df = _load_csv(VULN_CSV_PATH, VULN_CSV_HEADER)
    loaded_noise_df = _load_csv(NOISE_CSV_PATH, NOISE_CSV_HEADER)
    seen_keys = _build_seen_keys(loaded_vuln_df, loaded_noise_df)

    db_known: list[dict[str, str]] = []
    if SUPABASE_AVAILABLE and sb_only:
        try:
            db_known = load_cite(site_config["name"])
        except Exception as e:
            message = f"load_cite failed for {site_config['name']}: {e}"
            LOGGER.warning(message)
            reporter.warn(message, stats)

    starting_page = get_config_int(
        "HTML_START_PAGE", site_config["map"]["starting_page"]
    )

    cap = site_config["map"]["cap"]
    current_page = starting_page

    # Buffer this run's accepted/rejected rows; they are appended to the loaded
    # DataFrames once the run finishes (set-based dedup makes order irrelevant).
    new_vuln_rows: list[list[str]] = []
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
            articles = fetch_html_page(
                site_config, page_url, reporter=reporter, stats=stats
            )
        except KeyboardInterrupt:
            stats.paused = True
            reporter.finish_line()
            reporter.info(
                f"HTML scraper paused by operator during {site_config['name']}."
            )
            LOGGER.info(
                "HTML scraper paused by operator while fetching %s page %d",
                site_config["name"],
                current_page,
            )
            break
        except Exception as e:
            reporter.error(
                f"Fetching {site_config['name']} page {current_page} ({page_url}): {e}",
                stats,
            )
            LOGGER.warning(
                "Error fetching %s page %d (%s): %s",
                site_config["name"],
                current_page,
                page_url,
                e,
            )
            return (
                stats,
                _assemble_df(loaded_vuln_df, new_vuln_rows, VULN_CSV_HEADER),
                _assemble_df(loaded_noise_df, new_noise_rows, NOISE_CSV_HEADER),
            )

        if not articles:
            reporter.warn(
                f"No articles found on page {current_page}; stopping pagination",
                stats,
            )
            break

        stats.discovered += len(articles)
        reached_floor = False

        for article_index, article in enumerate(articles, start=1):
            body_snippet = (article["body"] or "")[:250].replace("\n", " ")
            article_key = (site_config["name"], article["link"])
            stats.processed += 1
            if reporter.verbose:
                reporter.detail(
                    f"[{article_index}/{len(articles)}] {article['title'][:90]}"
                )

            # Date-window filter: skip newer-than-start, stop at older-than-end.
            if (start_date or end_date) and article.get("date"):
                try:
                    pub_date = datetime.date.fromisoformat(article["date"][:10])
                except ValueError:
                    pub_date = None
                if pub_date is not None:
                    if start_date and pub_date > start_date:
                        stats.skipped += 1
                        reporter.detail(
                            f"[      SKIP-DATE] Newer than {start_date}: {article['title']}"
                        )
                        _live_site_status(
                            reporter, site_config["name"], current_page, stats
                        )
                        continue
                    if end_date and pub_date < end_date:
                        reporter.detail(
                            f"[FINISH] Reached article older than {end_date} on "
                            f"{site_config['name']}: {article['title']!r}"
                        )
                        reached_floor = True
                        break

            if SUPABASE_AVAILABLE and db_known and sb_only:
                try:
                    if is_known_db(db_known, article["title"], body_snippet):
                        stats.skipped += 1
                        reporter.detail(
                            f"      [SKIP-DB] Already in Supabase: {article['title']}"
                        )
                        _live_site_status(
                            reporter, site_config["name"], current_page, stats
                        )
                        continue
                except Exception as e:
                    reporter.warn(f"is_known_db check failed: {e}", stats)
                    LOGGER.warning("is_known_db check failed: %s", e)

            # Local dedup: skip articles already in the consolidated corpus.
            # This is a SKIP, never a pagination stop.
            if not sb_only and article_key in seen_keys:
                stats.skipped += 1
                reporter.detail(
                    f"      [SKIP-SEEN] Already collected: {article['title']}"
                )
                _live_site_status(reporter, site_config["name"], current_page, stats)
                continue

            try:
                is_threat, detail = ai_check_validation(
                    article["title"], article["body"]
                )
                if is_threat:
                    if detail not in SUBSECTOR_FIELDS:
                        # LOGGER.warning(
                        #     f"[WARNING] Unrecognized subsector '{detail}' — skipping: {article['title']}"
                        # )
                        stats.skipped += 1
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

                    LOGGER.info("Validated article: %s", vuln.title)

                    if sb_only:
                        try:
                            handle_vuln(vuln, reporter=reporter, stats=stats)
                            stats.output_records += 1
                        except Exception as e:
                            LOGGER.warning(
                                "handle_vuln failed for %s: %s", vuln.title, e
                            )
                    else:
                        content_preview = (vuln.content or "")[:250].replace("\n", " ")
                        new_vuln_rows.append(
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
                        seen_keys.add(article_key)
                        stats.output_records += 1
                else:
                    stats.rejected += 1
                    body_preview = (article["body"] or "")[:250].replace("\n", " ")

                    if sb_only:
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
                            LOGGER.warning(
                                "insert_noise failed for %s: %s", article["title"], e
                            )
                    else:
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
                        seen_keys.add(article_key)
            except KeyboardInterrupt:
                stats.paused = True
                reporter.finish_line()
                reporter.info(
                    f"HTML scraper paused by operator during {site_config['name']}."
                )
                LOGGER.info(
                    "HTML scraper paused by operator while processing %s article %s",
                    site_config["name"],
                    article.get("link", "unknown"),
                )
                break
            except Exception as e:
                LOGGER.warning(
                    "Validation failed for %s: %s", article.get("title", "unknown"), e
                )
                continue

        if stats.paused:
            break

        if reached_floor:
            break

        current_page += 1
        try:
            time.sleep(0.25)
        except KeyboardInterrupt:
            stats.paused = True
            reporter.finish_line()
            reporter.info(
                f"HTML scraper paused by operator during {site_config['name']}."
            )
            LOGGER.info(
                "HTML scraper paused by operator between %s pages",
                site_config["name"],
            )
            break

    vuln_df = _assemble_df(loaded_vuln_df, new_vuln_rows, VULN_CSV_HEADER)
    noise_df = _assemble_df(loaded_noise_df, new_noise_rows, NOISE_CSV_HEADER)

    reporter.finish_line()
    if not sb_only:
        reporter.info(
            f"Finished {site_config['name']}: {len(new_vuln_rows)} vuln(s), "
            f"{len(new_noise_rows)} rejected"
        )
        LOGGER.info(
            "Finished %s: %d vuln(s), %d rejected",
            site_config["name"],
            len(new_vuln_rows),
            len(new_noise_rows),
        )
    else:
        reporter.info(f"Finished {site_config['name']}: {stats.output_records} vuln(s)")
        LOGGER.info(
            "Finished %s (Supabase): %d vuln(s)",
            site_config["name"],
            stats.output_records,
        )
    if local_reporter:
        reporter.summary(stats)
    return stats, vuln_df, noise_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="HTML scraper for healthcare news sites"
    )
    parser.add_argument(
        "--use-bert",
        action="store_true",
        default=get_config_bool("USE_BERT", False),
        help="Run BERT pre-filter before LLM validation to skip unrelated articles early",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=get_config_bool("VERBOSE", False),
        help="Show detailed per-article scraper output",
    )
    parser.add_argument(
        "--start-date",
        type=datetime.date.fromisoformat,
        default=get_config_date("HTML_START_DATE", None),
        metavar="YYYY-MM-DD",
        help="Newest article date to keep, newer articles are skipped (ceiling)",
    )
    parser.add_argument(
        "--end-date",
        type=datetime.date.fromisoformat,
        default=get_config_date("HTML_END_DATE", None),
        metavar="YYYY-MM-DD",
        help="Oldest article date to keep, crawling stops at older articles (floor)",
    )
    parser.add_argument(
        "--sb-only",
        action="store_true",
        default=get_config_bool("HTML_SB_ONLY", False),
        help="Use Supabase only, no local reads or writes",
    )

    args = parser.parse_args()

    for site in HTML_SITES:
        stats, vuln_df, noise_df = run_html_scraper(
            site,
            use_bert=args.use_bert,
            verbose=args.verbose,
            start_date=args.start_date,
            end_date=args.end_date,
            sb_only=args.sb_only,
        )
        print(
            f"{site['name']}: vuln_df={len(vuln_df)} rows, "
            f"noise_df={len(noise_df)} rows"
        )
        if stats.paused:
            break
