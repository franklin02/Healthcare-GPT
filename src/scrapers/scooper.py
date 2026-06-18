"""
Scooper scraper that

Functions
    - '_bert_status()' : returns a human-readable description of the optional BERT pre-filter
    - '_unseen_df()' : returns a raw df of unclassified data
    - '_setup_csv' : helper function that set ups CSVs and makes sure they exist,
    mostly used on the first run
    - '_scrape_page' : scrapes 1 individual page of a cite. This has catching logic
    that stops once we hit a known cite
    - '_raw_data' : Calls '_scrape_page' and iterates through all pages in a cite.
    This mends everything into 1 DataFrame
    - '_update_raw_csv' : Helper function that updates the Raw CSV with new found information
    - 'setup_scooper' : function to be called from the orchastrator level. This set up
    the scraper by scrapping before hand, and leaving the classifying step (most expensive)
    to be determined by the user
    - 'run_scooper' : classifies all the raw data by handing it off DataFrames to `run_scooper`
    - '_process_site' : takes a dataframe and goes row by row to classify the entire df
"""

import argparse
import csv
import datetime
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import pandas as pd
from bs4 import BeautifulSoup

from ..classes import SUBSECTOR_DATA_CLASSES, Vulnerability
from ..cli_reporter import CliReporter, PipelineStats
from ..logging_utils import get_file_logger
from ..shared_utils import (
    MissingSubsectorFieldsError,
    ai_check_validation,
    ensure_model_available,
    extract_fields,
    get_config_bool,
    get_config_date,
    get_config_int,
    get_page,
    _PROJECT_ROOT,
    update_csv,
    update_json,
    df_dup,
)

LOGGER = get_file_logger(__name__, _PROJECT_ROOT / "data" / "logs" / "scooper.log")

VULN_CSV_PATH = _PROJECT_ROOT / "data" / "vulnerabilities" / "scooper_vuln.csv"
VULN_CSV_HEADER = [
    "source_name",
    "title",
    "direct_link",
    "subsector",
    "date_accessed",  # sb and local should handle this differntly, empty for now
    "date_published",
    "exec_summary",
]
NOISE_CSV_PATH = _PROJECT_ROOT / "data" / "noise" / "scooper_noise.csv"
NOISE_CSV_HEADER = ["source_name", "title", "link", "reason", "body_preview", "date"]
RAW_CSV_PATH = _PROJECT_ROOT / "data" / "raw" / "scooper_raw.csv"
RAW_CSV_HEADER = ["source_name", "title", "link", "body", "date"]
SCOOPER_JSON_PATH = _PROJECT_ROOT / "data" / "processed" / "scooper.json"
SCOOPER_JSON_PATH = _PROJECT_ROOT / "data" / "processed" / "scooper.json"

try:
    from src.supabase_function import has_supabase_creds

    SUPABASE_AVAILABLE = has_supabase_creds()
    if not SUPABASE_AVAILABLE:
        LOGGER.warning("SUPABASE_URL or SUPABASE_KEY missing; DB writes disabled")
except Exception as e:
    LOGGER.warning("Supabase unavailable, DB writes disabled: %s", e)
    SUPABASE_AVAILABLE = False


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
            "date_selector": "div.main-article-author-date span",
            "starting_page": 1,
            "cap": 9,
        },
    },
]


SITE_NAMES = [s["name"] for s in HTML_SITES]


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


def _unseen_df() -> pd.DataFrame:
    """
    Total raw data - (vuln + noise) = data not analyzed

    Returns:
        the raw rows not yet classified into the vulnerability or noise CSVs. Rows are
        matched on (source_name, title) — the same identity used to dedupe while
        scraping. With nothing classified yet, all raw rows are returned.
    """
    key = ["source_name", "title"]
    raw_df = pd.read_csv(RAW_CSV_PATH, parse_dates=["date"])

    # Collect the (source_name, title) of everything already classified. The
    # vuln/noise CSVs may not exist or be empty yet, in which case nothing is
    # filtered out and every raw row counts as unseen.
    seen_frames = []
    for path in (VULN_CSV_PATH, NOISE_CSV_PATH):
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if not df.empty and set(key).issubset(df.columns):
            seen_frames.append(df[key])

    if not seen_frames:
        return raw_df

    seen = pd.concat(seen_frames, ignore_index=True).drop_duplicates()

    # Left anti-join: keep only raw rows whose key has no match in `seen`.
    merged = raw_df.merge(seen, on=key, how="left", indicator=True)
    unseen = merged.loc[merged["_merge"] == "left_only", raw_df.columns]
    return unseen.reset_index(drop=True)


def _setup_cvs() -> None:
    """
    Sets up all CSV paths on new runs
    """
    path_to_header = {
        VULN_CSV_PATH: VULN_CSV_HEADER,
        NOISE_CSV_PATH: NOISE_CSV_HEADER,
        RAW_CSV_PATH: RAW_CSV_HEADER,
    }
    for path, header in path_to_header.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() and header:
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(header)


def _update_raw_csv(df: pd.DataFrame) -> None:
    """
    Append newly scraped raw rows to the raw CSV.

    The header is written once by `_setup_cvs`, so rows are appended without a
    header. Columns are aligned to RAW_CSV_HEADER before writing.
    """
    if df.empty:
        return
    df[RAW_CSV_HEADER].to_csv(
        RAW_CSV_PATH, mode="a", header=False, index=False, date_format="%Y-%m-%d"
    )


def _scrape_page(
    site_config,
    page_url,
    raw_df: pd.DataFrame,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    sb_only: bool = False,  # TODO: nothing changes this flag, rn its fine since no one has sb but needs a fix
) -> tuple[pd.DataFrame, bool]:
    """
    Fetch one listing page and return article payloads plus a stop flag.

    The listing page is parsed with the site's configured selectors, then each
    candidate link is fetched to collect article body text and publication date.
    The stop flag is set when a previously processed article is encountered so
    pagination can end early.

    Returns:
        - DataFrame of new/unseen raw data
        - A bool, True when we encountered a past article and should stop
    """
    local_only = not sb_only
    response = get_page(page_url)
    soup = BeautifulSoup(response.content, "html.parser")
    m = site_config["map"]
    link_elements = soup.select(m["container"])

    if not link_elements:
        if reporter:
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
        return pd.DataFrame(columns=RAW_CSV_HEADER), False

    # Creates a set of valid articles with their respective links
    seen_urls = set()
    raw_links = []

    # This for loop iterates through a pagination site and grabs all
    # valid articles and put them in a set
    for el in link_elements:
        if m.get("link_selector"):
            a_tag = el.select_one(m["link_selector"])
        else:
            a_tag = el if el.name == "a" else el.select_one("a[href]")

        if not a_tag:
            LOGGER.warning(
                f"link_selector '{m.get('link_selector')}' found no anchor "
                f"in a '{m['container']}' item",
            )
            if reporter:
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

    # For each article found, we go to that specific link and grab the body and date
    articles = []
    stop = False
    for entry in raw_links:
        try:
            article_resp = get_page(entry["link"])
            article_soup = BeautifulSoup(article_resp.content, "html.parser")

            body_el = article_soup.select_one(body_selector)
            if not body_el:
                LOGGER.warning(
                    f"body_selector '{body_selector}' matched nothing on "
                    f"{entry['link']}; skipping article",
                )
                if reporter:
                    reporter.warn(
                        f"body_selector '{body_selector}' matched nothing on "
                        f"{entry['link']}; skipping article",
                        stats,
                    )
                if stats is not None:
                    stats.skipped += 1
                continue

            body = body_el.get_text(separator=" ", strip=True)

            if local_only:
                if (
                    (raw_df["source_name"] == site_config["name"])
                    & (raw_df["title"] == entry["title"])
                ).any():
                    if reporter:
                        reporter.detail(
                            f"[FINISH] Reached known article on {site_config['name']}: "
                            f"{entry['title']!r}"
                        )
                    LOGGER.info(
                        "[FINISH] Reached known article on %s: %r",
                        site_config["name"],
                        entry["title"],
                    )
                    stop = True
                    break
            if sb_only:
                """
                TODO: current Supabase logic wont work, we need a work around. since no one really has
                supabse i will put this off for later.
                """

            date_el = article_soup.select_one(date_selector) if date_selector else None
            raw_date = date_el.get("datetime", "") if date_el else ""
            date = pd.to_datetime(raw_date, errors="coerce")
            if pd.notna(date):
                date = date.normalize()

            time.sleep(0.25)
        except Exception as e:
            if reporter:
                reporter.warn(
                    f"Could not fetch article body at {entry['link']}: {e}", stats
                )
            LOGGER.warning("Error fetching article body:%s", e)
            if stats is not None:
                stats.skipped += 1
            continue

        articles.append(
            {
                "source_name": site_config["name"],
                "title": entry["title"],
                "link": entry["link"],
                "body": body,
                "date": date,
            }
        )

    articles_df = pd.DataFrame(articles, columns=RAW_CSV_HEADER)
    LOGGER.info("Fetched %d articles from %s", len(articles_df), page_url)
    return articles_df, stop


def _raw_data(
    known_df: pd.DataFrame,
    site_config,
    sb_only: bool = False,
    reporter: CliReporter | None = None,
) -> pd.DataFrame:
    """
    Scrapes new 'raw data' from a specific cite

    Args:
        known_df: Already existing raw data, used for caching

    Returns:
        dataframe with new data the known_df does not have

    """
    starting_page = get_config_int(
        "HTML_START_PAGE", site_config["map"]["starting_page"]
    )
    cap = site_config["map"]["cap"]
    current_page = starting_page

    stats = PipelineStats(site_config["name"])
    new_df = pd.DataFrame(columns=RAW_CSV_HEADER)
    while True:
        if cap != -1 and current_page > cap:
            if reporter:
                reporter.info(f"Reached page cap ({cap}) for {site_config['name']}")
            LOGGER.info(f"Reached page cap ({cap}) for {site_config['name']}")
            break

        if current_page == starting_page:
            page_url = site_config["url"]
        else:
            page_url = site_config.get("pagination_url").replace(
                "{page}", str(current_page)
            )

        try:
            articles, stop = _scrape_page(
                site_config,
                page_url,
                reporter=reporter,
                stats=stats,
                sb_only=sb_only,
                raw_df=known_df,
            )
        except KeyboardInterrupt:
            stats.paused = True
            if reporter:
                reporter.info(
                    f"HTML scraper paused by operator during {site_config['name']}; "
                    "flushing completed records."
                )
            LOGGER.info(
                "HTML scraper paused by operator while fetching %s page %d",
                site_config["name"],
                current_page,
            )
            break
        except Exception as e:
            LOGGER.warning(
                "Error fetching %s page %d (%s): %s",
                site_config["name"],
                current_page,
                page_url,
                e,
            )
            return new_df

        if articles.empty:
            LOGGER.warning(
                f"No articles found on page {current_page}; stopping pagination"
            )
            if reporter:
                reporter.warn(
                    f"No articles found on page {current_page}; stopping pagination",
                    stats,
                )
            break

        new_df = pd.concat([new_df, articles], ignore_index=True)
        stats.discovered += len(articles)

        if stats.paused:
            break

        if stop:
            break

        current_page += 1
        try:
            time.sleep(0.25)
        except KeyboardInterrupt:
            stats.paused = True
            LOGGER.info(
                "HTML scraper paused by operator between %s pages; "
                "flushing completed records.",
                site_config["name"],
            )
            break

    return new_df


def setup_scooper(sb_only: bool = False, reporter: CliReporter | None = None) -> None:
    """
    Sets up the pipeline for future runs. This updates and scrapes all new data, saves it
    to a CSV, and makes sure things like paths exist for future runs.
    """
    _setup_cvs()
    ensure_model_available()

    if reporter:
        reporter.phase("Scraping HTML sites")

    results = []

    raw_df = pd.read_csv(RAW_CSV_PATH)
    if raw_df.empty:
        LOGGER.info("Empty or new CSV, running from scratch")  # delete me l8er

    with ThreadPoolExecutor(max_workers=len(SITE_NAMES)) as executor:
        futures = []
        for site in HTML_SITES:
            site_df = raw_df[raw_df["source_name"] == site["name"]]
            futures.append(
                executor.submit(_raw_data, site_df, site, sb_only, reporter)
            )  # add cite config l8er

        for future in as_completed(futures):
            results.append(future.result())

    new_raw_df = pd.concat(results, ignore_index=True)
    _update_raw_csv(new_raw_df)


def run_scooper(
    use_bert: bool = False,
    verbose: bool = False,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    sb_only: bool = False,
    site_split: bool = False,
) -> tuple[PipelineStats, list[Vulnerability], pd.DataFrame, pd.DataFrame]:
    """
    Runs the scooper using the raw and unclassified data. If `site_split` is True,
    it breaks off and makes a thread per unique site_name.

    Returns:
        - PipelineStats
        - list of new vulns (used to make JSON)
        - vulnerabilities dataframe (used to write CSV vulnerabilities)
        - noise dataframes (used to write CSV noise)
    """

    # contains raw and unclassified data
    df = _unseen_df()
    stats = stats or PipelineStats("Scooper")

    nat_df = df[df["date"].isna()]  # all rows that are Not a Time
    if start_date is not None:
        df = df[df["date"] <= pd.Timestamp(start_date)]
    if end_date is not None:
        df = df[df["date"] >= pd.Timestamp(end_date)]

    # nat_df should always be empty, but in case the scrapper gets an article without a date
    if start_date is not None or end_date is not None:
        df = pd.concat([df, nat_df], ignore_index=True)

    # Single pass over every unseen row.
    if not site_split:
        return _process_site(df, stats, use_bert, verbose, sb_only, reporter)

    # One thread per site. Partitions are disjoint (each row has exactly one
    # source_name), so a plain concat reassembles them — no cross-site dedup.
    sites = [name for name in df["source_name"].dropna().unique()]
    if not sites:
        return (
            stats,
            [],
            pd.DataFrame(columns=VULN_CSV_HEADER),
            pd.DataFrame(columns=NOISE_CSV_HEADER),
        )

    def _run_site(name: str):
        site = df[df["source_name"] == name]
        # each thread gets its own stats instance to avoid races
        return _process_site(
            site, PipelineStats("HTML"), use_bert, verbose, sb_only, reporter
        )

    vuln_list: list[Vulnerability] = []
    vuln_frames: list[pd.DataFrame] = []
    noise_frames: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=len(sites)) as executor:
        for site_stats, vl, vdf, ndf in executor.map(_run_site, sites):
            stats.merge(site_stats)
            vuln_list.extend(vl)
            vuln_frames.append(vdf)
            noise_frames.append(ndf)

    vuln_df = (
        pd.concat(vuln_frames, ignore_index=True)
        if vuln_frames
        else pd.DataFrame(columns=VULN_CSV_HEADER)
    )
    noise_df = (
        pd.concat(noise_frames, ignore_index=True)
        if noise_frames
        else pd.DataFrame(columns=NOISE_CSV_HEADER)
    )
    return stats, vuln_list, vuln_df, noise_df


def save_results(
    vuln_list: list[Vulnerability],
    vuln_dfs: list[pd.DataFrame],
    noise_dfs: list[pd.DataFrame],
    sb_only: bool = False,
) -> None:
    """
    Persist classified scooper results. Dedups across the given frames, appends
    to the vuln/noise CSVs, and writes the JSON corpus.

    Accepts lists of frames so it serves both the single-pass/site-split route
    (wrap one frame in a list) and the date-split route (one frame per window).
    Empty frames/lists are handled gracefully. When `sb_only` is set, local CSV
    writes are skipped (Supabase path TODO); the JSON corpus is always written.

    Args:
        vuln_list: validated vulnerabilities used to build the JSON corpus
        vuln_dfs: list of vulnerability frames to dedupe + append to the vuln CSV
        noise_dfs: list of noise frames to dedupe + append to the noise CSV
        sb_only: skip local CSV writes when True
    """
    if not sb_only:
        clean_vuls, _ = df_dup(vuln_dfs)
        clean_noise, _ = df_dup(noise_dfs)
        update_csv(clean_vuls, VULN_CSV_PATH)
        update_csv(clean_noise, NOISE_CSV_PATH)
    else:
        print("SB stuff goes here? ")  # TODO implement sb here
    SCOOPER_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    update_json(vuln_list, str(SCOOPER_JSON_PATH))


def _process_site(
    df: pd.DataFrame,
    stats: PipelineStats,
    use_bert: bool = False,
    verbose: bool = False,
    sb_only: bool = False,
    reporter: CliReporter | None = None,
) -> tuple[PipelineStats, list[Vulnerability], pd.DataFrame, pd.DataFrame]:
    """
    Validate + extract every row in df, returning this slice's own result
    frames. Holds no shared state, so one instance can run per thread.

    Args:
        df: Raw and unprocessed dataframe to be classified
        stats:
        use_bert:
        verbose:
        sb_only

    Returns:
        - PipelineStats
        - list of new vulns (used to make JSON)
        - vulnerabilities dataframe (used to write CSV vulnerabilities)
        - noise dataframes (used to write CSV noise)
    """
    # list will be used to write JSON
    vuln_list: list[Vulnerability] = []

    # empty dfs with their correct shape
    vuln_df = pd.DataFrame(columns=VULN_CSV_HEADER)
    noise_df = pd.DataFrame(columns=NOISE_CSV_HEADER)

    for row in df.itertuples():
        stats.processed += 1

        source_name = row.source_name if isinstance(row.source_name, str) else ""
        title = row.title if isinstance(row.title, str) else ""
        body = row.body if isinstance(row.body, str) else ""
        link = row.link if isinstance(row.link, str) else ""
        date_published = row.date.strftime("%Y-%m-%d") if pd.notna(row.date) else ""

        if verbose:
            if not source_name:
                LOGGER.info(f"No source name found for this row: {row}")
            if not title:
                LOGGER.info(f"No title found for this row: {row}")
            if not body:
                LOGGER.info(f"No body found for this row: {row}")
            if not link:
                LOGGER.info(f"No link found for this row: {row}")
            if not date_published:
                LOGGER.info(f"No date found for this row: {row}")

        try:
            is_threat, detail = ai_check_validation(
                title, body, use_bert=use_bert, verbose=verbose
            )
            if is_threat:
                if detail not in SUBSECTOR_FIELDS:
                    if reporter:
                        reporter.warn(
                            f"Unrecognized subsector '{detail}' — skipping: {title}",
                            stats,
                        )
                    LOGGER.warning(
                        f"[WARNING] Unrecognized subsector '{detail}' — skipping: {title}"
                    )
                    stats.skipped += 1
                    continue
                try:
                    sector_data, ss_data = extract_fields(detail, title, body)
                except MissingSubsectorFieldsError as exc:
                    stats.skipped += 1
                    if reporter:
                        reporter.warn(f"Skipping extraction for {title}: {exc}", stats)
                    LOGGER.warning("Skipping extraction for %s: %s", title, exc)
                    continue
                stats.validated += 1

                cls = SUBSECTOR_DATA_CLASSES.get(detail)
                subsector_data = cls.from_dict(ss_data) if cls else None

                # build a vuln object
                vuln = Vulnerability(
                    id=str(uuid.uuid4()),
                    title=title,
                    source_name=source_name,
                    direct_link=link,
                    subsector=detail,
                    date_accessed=datetime.datetime.now().strftime("%Y-%m-%d"),
                    date_published=date_published,
                    content=body,
                    exec_summary=sector_data.get("exec_summary"),
                    geography_scope=sector_data.get("geography_scope"),
                    start_date=sector_data.get("start_date"),
                    end_date=sector_data.get("end_date"),
                    resilience_or_mitigation_observed=sector_data.get(
                        "resilience_or_mitigation_observed"
                    ),
                    subsector_data=subsector_data,
                )
                vuln_list.append(vuln)

                # this is validated, info is extracted
                if sb_only:
                    """
                    # TODO implement supabse logic here
                    """

                # local logic, might be redundant with vuln_list
                else:
                    vuln_df = pd.concat(
                        [
                            vuln_df,
                            pd.DataFrame(
                                [
                                    {
                                        col: val
                                        for col, val in zip(
                                            VULN_CSV_HEADER,
                                            [
                                                row.source_name,
                                                title,
                                                link,
                                                detail,
                                                datetime.datetime.now().strftime(
                                                    "%Y-%m-%d"
                                                ),
                                                date_published,
                                                sector_data.get("exec_summary") or "",
                                            ],
                                        )
                                    }
                                ]
                            ),
                        ],
                        ignore_index=True,
                    )
                    if reporter:
                        reporter.detail(f"[VULN] {detail}: {title}")
                    if verbose:
                        LOGGER.info(f"[VULN] {detail}: {title}")

            else:  # this is noise
                body_preview = body[:250].replace("\n", " ")
                noise_df = pd.concat(
                    [
                        noise_df,
                        pd.DataFrame(
                            [
                                {
                                    col: val
                                    for col, val in zip(
                                        NOISE_CSV_HEADER,
                                        [
                                            row.source_name,
                                            title,
                                            link,
                                            detail,
                                            body_preview,
                                            date_published,
                                        ],
                                    )
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
                stats.rejected += 1
                if reporter:
                    reporter.detail(f"[NOISE] {detail}: {title}")
                if verbose:
                    LOGGER.info(f"[NOISE] {detail}: {title}")

        except KeyboardInterrupt:
            stats.paused = True
            if reporter:
                reporter.info(
                    f"HTML scraper paused by operator while processing {link}"
                )
            LOGGER.info(
                "HTML scraper paused by operator while processing this article %s",
                link,
            )
            break
        except Exception as e:
            if reporter:
                reporter.warn(f"Validation failed for {title}: {e}", stats)
            LOGGER.warning("Validation failed for %s: %s", title, e)
            continue
    return stats, vuln_list, vuln_df, noise_df


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
    parser.add_argument(
        "--setup",
        action="store_true",
        default=False,
        help="Scrape all sites via setup_scooper() before classifying (first run)",
    )
    parser.add_argument(
        "--site_split",
        action="store_true",
        default=False,
        help="Gives a site its own thread to run parallel",
    )

    args = parser.parse_args()

    if args.setup:
        setup_scooper(sb_only=args.sb_only)

    stats, vuln_list, vuln_df, noise_df = run_scooper(
        use_bert=args.use_bert,
        verbose=args.verbose,
        start_date=args.start_date,
        end_date=args.end_date,
        sb_only=args.sb_only,
        site_split=args.site_split,
    )
    save_results(vuln_list, [vuln_df], [noise_df], sb_only=args.sb_only)
