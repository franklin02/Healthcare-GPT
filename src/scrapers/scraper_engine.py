'''
This file is going to be the main scraper engine for all the scrapers.

'''
import requests, time
from bs4 import BeautifulSoup


AI_URL = "http://localhost:11434/api/generate"
AI_MODEL = "llama3.2"


def run_scraper(site_name, feed_link):
    print(f"--- Scraping for {site_name} has started ---")
    
    try:
        response = requests.get(feed_link, timeout=10)
        feed_soup = BeautifulSoup(response.content, "lxml-xml")
        items = feed_soup.find_all('item')
        #print(feed_soup.prettify())

    except Exception as e:
        print(f"Error fetching feed for {site_name}: {e}")
        return

    for item in items:

        title = item.title.text

        full_body = item.find('encoded').text
        body = BeautifulSoup(full_body, "lxml").get_text(separator=" ",strip="True")

        print(f"TITLE: {title}")
        print(f"BODY PREVIEW: {body[:100]}...")

'''
This function is used to call an AI model (current Ollama) to check
if the article we parsed presents a risk to the healthcare industry.
It expects 2 arguments: the title and the body of the article which 
at this point should be already parsed and cleaned.
'''
def ai_check(title, body) -> bool:
    prompt = f"""\
        TO be determined...

        TITLE: {title}
        EXCERPT: {body}

        Answer (YES or NO only):
    """

    try:
        resp = requests.post(
            AI_URL,
            json={"model": AI_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "Could not reach Ollama. Make sure it is running: ollama serve"
        )

    if resp.status_code != 200:
        print(f"Error: {resp.status_code}")
        return False

    answer = resp.json().get("response", "").strip().upper()
    return answer.startswith("YES")


# Site configurations: Just the URL and the CSS tags
SITES_TO_SCRAPE = [
    {
        "name": "CyberScoop",
        "feed_link": "https://cyberscoop.com/news/healthcare/feed/",

        "title": "h3.post-item__title", 
        "date": "time.post-item__date",
        "body": "div.post-item__excerpt p" 
    },
    # {
    #     "name": "FedScoop",
    #     "feed_link": "https://fedscoop.com/feed/",

    #     "title": "h3.post-item__title", 
    #     "date": "time.post-item__date",
    #     "body": "div.post-item__excerpt p"
    # },
    # {
    #     "name": "HHS_ASPR",
    #     "feed_link": "https://aspr.hhs.gov/newsroom/Pages/RSS-Feed.aspx",

    #     "title": "h3.post-item__title", 
    #     "date": "time.post-item__date",
    #     "body": "div.post-item__excerpt p"
    # }
]

# Run everything in one go
for site in SITES_TO_SCRAPE:
    run_scraper(site['name'], site['feed_link'])