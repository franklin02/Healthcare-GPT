"""Configured HTML scraper runner for healthcare disruption sources.

Each site keeps its own selectors and pagination defaults in ``HTML_SITES``.
The runner accepts optional pagination overrides so the orchestrator can expand
or shrink HTML runs without changing source configuration.
"""

import datetime
import time
import uuid
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from src.classes import SUBSECTOR_DATA_CLASSES, Vulnerability
from src.cli_reporter import CliReporter, PipelineStats
from src.logging_utils import get_file_logger
from src.shared_utils import (
    ai_check_validation,
    check_valid_file,
    ensure_model_available,
    extract_fields,
    get_config_int,
    get_page,
    is_known_article,
    MissingSubsectorFieldsError,
    model_unavailable_error,
    NOISE_CSV_HEADER,
    prepend_json_sources,
    prepend_noise_csv,
    prepend_vuln_csv,
    VULN_CSV_HEADER,
    _PROJECT_ROOT,
)

LOGGER = get_file_logger(__name__, _PROJECT_ROOT / "data" / "logs" / "scooper.log")

VULN_CSV_PATH = _PROJECT_ROOT / "data" / "vulnerabilities" / "scooper_vuln.csv"
NOISE_CSV_PATH = _PROJECT_ROOT / "data" / "noise" / "scooper_noise.csv"
RAW_CSV_PATH = _PROJECT_ROOT / "data" / "raw" / "scooper_raw.csv"
RAW_CSV_HEADER = [
    "date_accessed",
    "date_published",
    "source_name",
    "title",
    "link",
    "body",
]

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

SITE_NAMES = [s["name"] for s in HTML_SITES]


def _load_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    """
    Returns:
        The CSV loaded as a string DataFrame, or an empty one with the given
        columns when the file is missing or has no rows yet.
    """
    if path.exists():
        try:
            return pd.read_csv(path, dtype=str).fillna("")
        except pd.errors.EmptyDataError:
            return pd.DataFrame(columns=columns)
    return pd.DataFrame(columns=columns)


def _parse_listing(site_config, page_url) -> list[dict[str, str]]:
    """
    Fetch one listing page and return its ``{title, link}`` entries.

    Only the listing page itself is fetched — article pages are not visited,
    so callers can run the known-article check before paying for any body
    fetches.
    """
    response = get_page(page_url)
    soup = BeautifulSoup(response.content, "html.parser")

    m = site_config["map"]
    link_elements = soup.select(m["container"])
    if not link_elements:
        LOGGER.warning(
            "container '%s' matched 0 elements on %s; check HTML_SITES config",
            m["container"],
            page_url,
        )
        return []

    seen_urls = set()
    entries = []
    for el in link_elements:
        if m.get("link_selector"):
            a_tag = el.select_one(m["link_selector"])
        else:
            a_tag = el if el.name == "a" else el.select_one("a[href]")

        if not a_tag or not a_tag.get("href"):
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
            entries.append({"title": title_text, "link": href})

    return entries


def _fetch_article(site_config, entry) -> dict[str, str] | None:
    """
    Fetch one article page and return its full body and published date.

    Returns ``None`` when the body selector matches nothing or the fetch
    fails, so callers can skip the article and keep crawling.
    """
    m = site_config["map"]
    try:
        article_resp = get_page(entry["link"])
        article_soup = BeautifulSoup(article_resp.content, "html.parser")

        body_el = article_soup.select_one(m["body_selector"])
        if not body_el:
            LOGGER.warning(
                "body_selector '%s' matched nothing on %s; skipping article",
                m["body_selector"],
                entry["link"],
            )
            return None

        date_selector = m.get("date_selector", "")
        date_el = article_soup.select_one(date_selector) if date_selector else None
        date = date_el.get("datetime", "") if date_el else ""
        body = body_el.get_text(separator=" ", strip=True)
    except Exception as e:
        LOGGER.warning("Could not fetch article body at %s: %s", entry["link"], e)
        return None

    time.sleep(0.25)
    return {"body": body, "date": date}


class Scooper:
    """
    One raw-collection traversal of every configured site.

    Raw collection caches everything a site lists (title, link, full body,
    dates) with no LLM or noise/vuln judgment — classification happens later
    from the CSV. Each instance owns an optional date window so the
    orchestrator can run several instances in parallel, split by date.

    Stop semantics: a windowless instance (the normal incremental run) stops
    a site at the first article already present in the known corpus — pages
    list newest-first, so everything below it is already collected. A
    windowed instance instead *skips* known articles and stops at its date
    floor: pagination always starts at page 1, so the crawl must pass through
    newer, already-known territory to reach its window, and stopping at the
    first known hit there would end the crawl prematurely.
    """

    def __init__(
        self,
        known_df: pd.DataFrame,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ) -> None:
        """
        Args:
            known_df: The raw corpus (``RAW_CSV_HEADER`` columns); its
                ``(title, link)`` pairs per site are what "known" means.
            start_date: Newest article date to keep (inclusive ceiling), or
                ``None`` for no upper bound.
            end_date: Oldest article date to keep (inclusive floor), or
                ``None`` for no lower bound.
        """
        self.start_date = start_date
        self.end_date = end_date
        self.windowed = start_date is not None or end_date is not None
        self.known_keys: dict[str, set[tuple[str, str]]] = {
            site["name"]: set() for site in HTML_SITES
        }
        for source, title, link in zip(
            known_df["source_name"], known_df["title"], known_df["link"]
        ):
            self.known_keys.setdefault(source, set()).add((title, link))

    def raw_data(self) -> pd.DataFrame:
        """
        Traverse every configured site top to bottom, one thread per site.

        Returns:
            Only the NEW raw rows (``RAW_CSV_HEADER`` columns), never
            baseline + new — parallel instances returning the baseline would
            duplicate the corpus once per instance. The caller appends these
            to the corpus DataFrame and CSV.
        """
        rows: list[list[str]] = []
        with ThreadPoolExecutor(max_workers=len(HTML_SITES)) as executor:
            futures = {
                executor.submit(self._scrape_site, site): site["name"]
                for site in HTML_SITES
            }
            for future in as_completed(futures):
                site_name = futures[future]
                try:
                    site_rows = future.result()
                except Exception as e:
                    # Drop the site's partial rows: persisting a partial
                    # prefix would make the next run stop early at a known
                    # article and permanently miss the unscraped tail.
                    LOGGER.warning("Raw scrape failed for %s: %s", site_name, e)
                    continue
                LOGGER.info(
                    "Collected %d new raw row(s) from %s", len(site_rows), site_name
                )
                rows.extend(site_rows)
        return pd.DataFrame(rows, columns=RAW_CSV_HEADER)

    def _scrape_site(self, site_config) -> list[list[str]]:
        """
        Crawl one site's listing pages and return its new raw rows.
        """
        site_name = site_config["name"]
        known = self.known_keys.get(site_name, set())
        seen_this_run: set[tuple[str, str]] = set()
        rows: list[list[str]] = []

        starting_page = get_config_int(
            "HTML_START_PAGE", site_config["map"]["starting_page"]
        )
        cap = site_config["map"]["cap"]
        current_page = starting_page

        while True:
            if cap != -1 and current_page > cap:
                LOGGER.info("Reached page cap (%d) for %s", cap, site_name)
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
                entries = _parse_listing(site_config, page_url)
            except Exception as e:
                # Paginating past the last page often 404s; treat any listing
                # failure as the end of the site.
                LOGGER.warning(
                    "Error fetching %s page %d (%s): %s — treating as end of site",
                    site_name,
                    current_page,
                    page_url,
                    e,
                )
                break

            if not entries:
                LOGGER.info(
                    "No articles found on %s page %d; stopping pagination",
                    site_name,
                    current_page,
                )
                break

            hit_known = False
            reached_floor = False
            for entry in entries:
                key = (entry["title"], entry["link"])
                if key in seen_this_run:
                    continue
                seen_this_run.add(key)

                if key in known:
                    if self.windowed:
                        continue
                    LOGGER.info(
                        "Reached known article on %s: %r — stopping",
                        site_name,
                        entry["title"],
                    )
                    hit_known = True
                    break

                article = _fetch_article(site_config, entry)
                if article is None:
                    continue

                # Window filter: skip newer-than-start, stop at older-than-end;
                # undated articles are kept.
                pub_date = None
                if self.windowed and article["date"]:
                    try:
                        pub_date = datetime.date.fromisoformat(article["date"][:10])
                    except ValueError:
                        pub_date = None
                if pub_date is not None:
                    if self.start_date and pub_date > self.start_date:
                        continue
                    if self.end_date and pub_date < self.end_date:
                        reached_floor = True
                        break

                rows.append(
                    [
                        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        article["date"],
                        site_name,
                        entry["title"],
                        entry["link"],
                        article["body"],
                    ]
                )

            if hit_known or reached_floor:
                break

            current_page += 1
            time.sleep(0.25)

        return rows


def setup_scooper() -> pd.DataFrame:
    """
    One-time setup plus a windowless raw-collection run over all sites.

    Creates the output directories, seeds the three consolidated CSVs (raw,
    vuln, noise) with their headers when missing, loads the raw corpus, runs
    one windowless ``Scooper`` over every configured site, and appends any
    new rows to both the in-memory DataFrame and ``scooper_raw.csv`` so the
    next run sees them.

    Returns:
        The up-to-date raw corpus DataFrame (baseline plus this run's rows).
    """
    for path, header in (
        (RAW_CSV_PATH, RAW_CSV_HEADER),
        (VULN_CSV_PATH, VULN_CSV_HEADER),
        (NOISE_CSV_PATH, NOISE_CSV_HEADER),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            pd.DataFrame(columns=header).to_csv(path, index=False)

    raw_df = _load_csv(RAW_CSV_PATH, RAW_CSV_HEADER)
    if raw_df.empty:
        print("Empty or new raw CSV, running from scratch")

    scooper = Scooper(known_df=raw_df)
    new_df = scooper.raw_data()
    if new_df.empty:
        print("No new articles found")
        return raw_df

    # Rows are persisted only after the full traversal finishes; writing
    # mid-run could leave a partial site prefix in the CSV, which would make
    # the next run stop early and permanently miss the unscraped tail.
    new_df.to_csv(RAW_CSV_PATH, mode="a", header=False, index=False)
    raw_df = pd.concat([raw_df, new_df], ignore_index=True)
    print(
        f"Collected {len(new_df)} new article(s): "
        f"{new_df['source_name'].value_counts().to_dict()}"
    )
    LOGGER.info("Appended %d new raw row(s) to %s", len(new_df), RAW_CSV_PATH)
    return raw_df


def flush_html_outputs(
    site_name: str,
    new_rows: list[list[str]],
    new_noise_rows: list[list[str]],
    new_vulns: list[Vulnerability],
    # reporter: CliReporter,
    stats: PipelineStats,
) -> None:
    """
    Flush buffered HTML scraper outputs to the configured destination helpers.

    HTML site runs accumulate accepted vulnerability rows, rejected/noise rows,
    and JSON-ready vulnerability objects in memory while a site is processed.
    This helper provides one shared write path for normal completion and
    graceful interrupt handling so a paused run does not lose buffered work.

    Parameters:
        site_name: Configured HTML source name used to select destination files.
        new_rows: Vulnerability CSV rows collected during the current site run.
        new_noise_rows: Rejected/noise CSV rows collected during the current
            site run.
        new_vulns: Vulnerability objects collected during the current site run.
        reporter: Reporter used to print the flush summary.
        stats: Pipeline statistics updated with the number of flushed
            vulnerability records.

    NOTE: only used when reading or writing locally
    """
    # reporter.finish_line()
    prepend_vuln_csv(site_name, new_rows)
    prepend_noise_csv(site_name, new_noise_rows)
    prepend_json_sources(site_name, new_vulns)
    stats.output_records += len(new_vulns)
    # reporter.info(
    #     f"Finished {site_name}: {len(new_vulns)} vuln(s), "
    #     f"{len(new_noise_rows)} rejected"
    # )
    LOGGER.info(
        "Finished %s: %d vuln(s), %d rejected",
        site_name,
        len(new_vulns),
        len(new_noise_rows),
    )


def fetch_html_page(
    site_config,
    page_url,
    # reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    sb_only: bool = False,
):
    """
    Fetch one listing page and return article payloads plus a stop flag.

    The listing page is parsed with the site's configured selectors, then each
    candidate link is fetched to collect article body text and publication date.
    The stop flag is set when a previously processed article is encountered so
    pagination can end early.
    """
    # reporter = reporter or CliReporter(verbose=True)
    response = get_page(page_url)
    soup = BeautifulSoup(response.content, "html.parser")

    m = site_config["map"]
    link_elements = soup.select(m["container"])
    if not link_elements:
        # reporter.warn(
        #     f"container '{m['container']}' matched 0 elements on {page_url}; "
        #     "check HTML_SITES config",
        #     stats,
        # )
        LOGGER.warning(
            "container '%s' matched 0 elements on %s; check HTML_SITES config",
            m["container"],
            page_url,
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
            # reporter.warn(
            #     f"link_selector '{m.get('link_selector')}' found no anchor "
            #     f"in a '{m['container']}' item",
            #     stats,
            # )
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
    stop = False
    for entry in raw_links:
        try:
            article_resp = get_page(entry["link"])
            article_soup = BeautifulSoup(article_resp.content, "html.parser")

            body_el = article_soup.select_one(body_selector)
            if not body_el:
                # reporter.warn(
                #     f"body_selector '{body_selector}' matched nothing on "
                #     f"{entry['link']}; skipping article",
                #     stats,
                # )
                if stats is not None:
                    stats.skipped += 1
                continue

            body = body_el.get_text(separator=" ", strip=True)

            if not sb_only:  # local dedup check only applies to local mode
                if is_known_article(site_config["name"], entry["title"], body):
                    # reporter.detail(
                    #     f"[FINISH] Reached known article on {site_config['name']}: "
                    #     f"{entry['title']!r}"
                    # )
                    stop = True
                    break

            date_el = article_soup.select_one(date_selector) if date_selector else None
            date = date_el.get("datetime", "") if date_el else ""

            time.sleep(0.25)
        except Exception as e:
            # reporter.warn(
            #     f"Could not fetch article body at {entry['link']}: {e}", stats
            # )
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
    return articles, stop


def run_html_scraper(
    site_config,
    use_bert: bool = False,
    verbose: bool = False,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    # reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    sb_only: bool = False,
) -> PipelineStats:
    """
    Run one configured HTML scraper and return its run statistics.

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
        The populated ``PipelineStats`` for the site.

    Interrupt behavior:
        Pressing ``Ctrl-C`` during page fetch, article validation, extraction,
        or inter-page delay marks the site stats as paused, flushes any buffered
        outputs through ``flush_html_outputs``, and returns the stats object to
        the orchestrator so remaining sites can be skipped.
    """
    # local_reporter = reporter is None
    # reporter = reporter or CliReporter(verbose=verbose)
    stats = stats or PipelineStats(site_config["name"])
    stats.sites_scanned += 1
    # reporter.phase(f"HTML scraper: {site_config['name']}")
    # reporter.status(f"LLM model: {AI_MODEL}")
    # if use_bert:
    # reporter.status(_bert_status())
    try:
        ensure_model_available()
    except model_unavailable_error as exc:
        LOGGER.error("Model availability check failed: %s", exc)
        raise
    # Resolve the effective mode before any file/DB work: if sb_only was
    # requested without creds, fall back to local so the guards below pick the
    # right side (and check_valid_file seeds the dirs the local writers need).
    if not SUPABASE_AVAILABLE and sb_only:
        sb_only = False
        LOGGER.warning(
            "sb_only was selected, but no Supabase keys are found; "
            "falling back to local-only writes."
        )

    # Local mode only: seed the per-site corpus dirs/files. In sb_only mode we
    # never touch the local corpus, so skip this entirely.
    if not sb_only:
        check_valid_file(site_config["name"])

    db_known: list[dict[str, str]] = []
    if SUPABASE_AVAILABLE and sb_only:
        try:
            db_known = load_cite(site_config["name"])
        except Exception as e:
            message = f"load_cite failed for {site_config['name']}: {e}"
            LOGGER.warning(message)
            # reporter.warn(message, stats)

    starting_page = get_config_int(
        "HTML_START_PAGE", site_config["map"]["starting_page"]
    )

    cap = site_config["map"]["cap"]
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
            # reporter.info(f"Reached page cap ({cap}) for {site_config['name']}")
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
                stats=stats,
                sb_only=sb_only,
                # site_config, page_url, reporter=reporter, stats=stats, sb_only=sb_only
            )
        except KeyboardInterrupt:
            stats.paused = True
            # reporter.finish_line()
            # reporter.info(
            #     f"HTML scraper paused by operator during {site_config['name']}; "
            #     "flushing completed records."
            # )
            LOGGER.info(
                "HTML scraper paused by operator while fetching %s page %d",
                site_config["name"],
                current_page,
            )
            break
        except Exception as e:
            # reporter.error(
            #     f"Fetching {site_config['name']} page {current_page} ({page_url}): {e}",
            #     stats,
            # )
            LOGGER.warning(
                "Error fetching %s page %d (%s): %s",
                site_config["name"],
                current_page,
                page_url,
                e,
            )
            return stats

        if not articles:
            # reporter.warn(
            #     f"No articles found on page {current_page}; stopping pagination",
            #     stats,
            # )
            break

        stats.discovered += len(articles)
        reached_floor = False

        for article_index, article in enumerate(articles, start=1):
            body_snippet = (article["body"] or "")[:250].replace("\n", " ")
            stats.processed += 1
            # if reporter.verbose:
            #     reporter.detail(
            #         f"[{article_index}/{len(articles)}] {article['title'][:90]}"
            #     )

            # Date-window filter: skip newer-than-start, stop at older-than-end.
            if (start_date or end_date) and article.get("date"):
                try:
                    pub_date = datetime.date.fromisoformat(article["date"][:10])
                except ValueError:
                    pub_date = None
                if pub_date is not None:
                    if start_date and pub_date > start_date:
                        stats.skipped += 1
                        # reporter.detail(
                        #     f"[      SKIP-DATE] Newer than {start_date}: {article['title']}"
                        # )
                        _live_site_status(
                            # reporter, site_config["name"], current_page, stats
                            site_config["name"],
                            current_page,
                            stats,
                        )
                        continue
                    if end_date and pub_date < end_date:
                        # reporter.detail(
                        #     f"[FINISH] Reached article older than {end_date} on "
                        #     f"{site_config['name']}: {article['title']!r}"
                        # )
                        reached_floor = True
                        break

            if SUPABASE_AVAILABLE and db_known and sb_only:
                try:
                    if is_known_db(db_known, article["title"], body_snippet):
                        stats.skipped += 1
                        # reporter.detail(
                        #     f"      [SKIP-DB] Already in Supabase: {article['title']}"
                        # )
                        _live_site_status(site_config["name"], current_page, stats)
                        continue
                except Exception as e:
                    # reporter.warn(f"is_known_db check failed: {e}", stats)
                    LOGGER.warning("is_known_db check failed: %s", e)

            try:
                is_threat, detail = ai_check_validation(
                    article["title"], article["body"]
                )
                if is_threat:
                    if detail not in SUBSECTOR_FIELDS:
                        LOGGER.warning(
                            f"[WARNING] Unrecognized subsector '{detail}' — skipping: {article['title']}"
                        )
                        stats.skipped += 1
                        continue
                    try:
                        sector_data, ss_data = extract_fields(
                            detail, article["title"], article["body"]
                        )
                    except MissingSubsectorFieldsError as exc:
                        stats.skipped += 1
                        # reporter.warn(
                        #     f"Skipping extraction for {article['title']!r}: {exc}",
                        #     stats,
                        # )
                        LOGGER.warning(
                            "Skipping extraction for %s: %s", article["title"], exc
                        )
                        continue
                    stats.validated += 1

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
                            # handle_vuln(vuln, reporter=reporter, stats=stats)
                            handle_vuln(vuln, stats=stats)
                            stats.output_records += 1
                        except Exception as e:
                            LOGGER.warning(
                                "handle_vuln failed for %s: %s", vuln.title, e
                            )
                    else:
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
            except KeyboardInterrupt:
                stats.paused = True
                # reporter.finish_line()
                # reporter.info(
                #     f"HTML scraper paused by operator during {site_config['name']}; "
                #     "flushing completed records."
                # )
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

        if stop or reached_floor:
            break

        current_page += 1
        try:
            time.sleep(0.25)
        except KeyboardInterrupt:
            stats.paused = True
            # reporter.finish_line()
            # reporter.info(
            #     f"HTML scraper paused by operator during {site_config['name']}; "
            #     "flushing completed records."
            # )
            LOGGER.info(
                "HTML scraper paused by operator between %s pages",
                site_config["name"],
            )
            break

    if not sb_only:
        flush_html_outputs(
            site_config["name"],
            new_rows,
            new_noise_rows,
            new_vulns,
            # reporter,
            stats,
        )
    else:
        # reporter.finish_line()
        # reporter.info(f"Finished {site_config['name']}: {stats.output_records} vuln(s)")
        LOGGER.info(
            "Finished %s (Supabase): %d vuln(s)",
            site_config["name"],
            stats.output_records,
        )
    # if local_reporter:
    #     reporter.summary(stats)
    return stats


if __name__ == "__main__":
    setup_scooper()
