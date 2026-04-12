'''
HOW TO USE: 

Each website should be a structured dictionary with the following keys:
- name: this will be used to name the folder where the output will be saved
- url: XML url of the feed (usually its the same as the website url)
- map: dictionary
    - container: the XML tag that contains each individual article/post (usually its always item)
    - title: look at the XML and find the tag that contains the title (usually title)
    - link: look at the XML and find the tag that contains the link (a good default is link but double check)
    - body: look at the XML and find the tag that contains the body (this one differs a lot)
    - starting_page: this should be determined by how the website url is structured
    - cap: should be int, if no cap is desired set to -1
'''
import json, uuid, datetime, csv
import requests, time
from pathlib import Path
from bs4 import BeautifulSoup

# to be changed later on 
AI_URL = "http://localhost:11434/api/generate"
AI_MODEL = "llama3.2"
VALID_DIR   = Path(__file__).parent.parent / "data" / "Ready_for_RAG"
NOISE_DIR = Path(__file__).parent.parent / "data" / "Noise"
VULNERABILITIES_DIR = Path(__file__).parent.parent / "data" / "Vulnerabilities"

VALID_SUBSECTORS = {"drug_shortage", "medical_device_shortage", "cyber_attack", "natural_disaster", "other"}
SITES_TO_SCRAPE = [
    {
        "name": "CyberScoop",
        "url": "https://cyberscoop.com/news/healthcare/feed/",
        "map": {
            "container": "item",
            "title": "title",
            "link": "link", 
            "body": "encoded",
            "starting_page": 1, 
            "cap": 21, 
        }
    },
    #     {
    #     "name": "StateScoop",
    #     "url": "https://statescoop.com/tag/healthcare/feed/",
    #     "map": {
    #         "container": "item",
    #         "title": "title",
    #         "link": "link", 
    #         "body": "encoded",
    #         "starting_page": 1, 
    #         "cap": -1, 
    #     }
    # },
    # {
    #     "name": "FedScoop",
    #     "url": "https://fedscoop.com/tag/healthcare/feed/",
    #     "map": {
    #         "container": "item",
    #         "title": "title",
    #         "link": "link", 
    #         "body": "encoded",
    #         "starting_page": 1, 
    #         "cap": -1, 
    #     }
    # },
    # {
    #     "name": "HealthITSecurity",
    #     "url": "https://healthitsecurity.com/feed",
    #     "method": "internal",
    #     "map": {
    #         "container": "item",
    #         "title": "title",
    #         "link": "link",
    #         "starting_page": 1, 
    #         "body": "encoded",
    #     },
    #     "cap": -1
    # },
    # {
    #     "name": "HHS_ASPR",
    #     "url": "https://aspr.hhs.gov/newsroom/Pages/RSS-Feed.aspx",
    #     "method": "external", # This feed ONLY gives snippets. We must visit the URL.
    #     "map": {
    #         "container": "item",
    #         "title": "title",
    #         "link": "link", 
    #         "body": "description", # This is just a fallback/preview
    #         "html_selector": "div.ms-rtestate-field p" # Use this when visiting the link
    #     },
    #     "cap": -1
    # },
]

def run_scraper(site_config):
    print(f"--- Scraping for {site_config['name']} has started ---")
    check_valid_file(site_config['name'])
    STARTING_PAGE = site_config['map']['starting_page']
    current_page = STARTING_PAGE # some cites start at 1, other at 0 

    while True:
        # get the url based on current page
        if current_page == STARTING_PAGE: url = site_config['url']
        else: url = f"{site_config['url']}?paged={current_page}"


        try:
            #check to see if we are still in bounds
            cap = site_config['map']['cap'] # -1 if no cap
            if cap != -1 and current_page > cap:
                print(f"Reached cap of {cap} for {site_config['name']}")
                break

            # Checks to see we are actually retrieving the content
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                print(f"Error fetching feed for {site_config['name']} on page {current_page}: {response.status_code}")
                break


            feed_soup = BeautifulSoup(response.content, "lxml-xml")
            container_name = site_config['map']['container']
            items = feed_soup.find_all(container_name)

            #checks to see there is actual content on the page 
            if not items:
                print(f"No items found on page {current_page}")
                break
            
            # logic starts here 
            for item in items:
                # Get the title and url
                title = item.find(site_config['map']['title']).text
                url = item.find(site_config['map']['link']).text

                # Get the body and clean it up 
                body_tag_name = site_config['map']['body']
                full_body = item.find(body_tag_name).text
                body = BeautifulSoup(full_body, "lxml").get_text(separator=" ",strip="True")

                # NOTE: Eventually we will need to see if the article is something we have already seen
                is_threat, detail = ai_check(title, body)
                if is_threat: 
                    json_output(site_config['name'], title, url, body, detail)

                else: invalid_article_output(site_config['name'], title, url, body, detail)
            
            current_page += 1
            time.sleep(1) # pause per page so we dont get banned by the server 
            

        except Exception as e:
            print(f"Error fetching feed for {site_config['name']}: {e}")
            return


'''
This function is used to call an AI model (current Ollama) to check
if the article we parsed presents a risk to the healthcare industry.
It expects 2 arguments: the title and the body of the article which 
at this point should be already parsed and cleaned.
'''
def ai_check(title, body) -> tuple[bool, str]:
    prompt = f"""
    [INST] <<SYS>>
    You are a Healthcare Crisis Auditor. Your task is to identify ACTIVE OPERATIONAL DISRUPTIONS in healthcare. 
    An operational disruption is defined as an event where the actual delivery of care is currently degraded, stopped, or physically prevented.

    CRITICAL EXCLUSIONS (Mark as NO):
    - "Potential" risks or "discovered vulnerabilities" that haven't been exploited yet.
    - Policy debates, government legislation, or funding news.
    - Corporate business news (mergers, stock prices, quarterly earnings).
    - General research papers or clinical trial results without an active supply failure.
    - Historical data or sentencing for crimes that happened years ago.

    VALID DISRUPTION EXAMPLES (Mark as YES):
    - A specific hospital diverting ambulances or canceling surgeries.
    - A ransomware attack currently locking a facility's EHR (Electronic Health Records).
    - A drug being physically unavailable at pharmacies due to a factory shutdown.
    - Physical damage to a clinic from weather or fire.

    You MUST respond in this JSON format:
    {{
    "analysis": "A one-sentence explanation of the active impact found in the text.",
    "is_operational_disruption": boolean,
    "subsector": "drug_shortage" | "medical_device_shortage" | "cyber_attack" | "natural_disaster" | "other" | "none"
    }}
    <</SYS>>

    TITLE: {title}
    EXCERPT: {body}

    [/INST]
    """
    
    try:
        resp = requests.post(
            AI_URL,
            json={
                "model": AI_MODEL, 
                "prompt": prompt, 
                "stream": False,
                "format": "json",
                "options": { "temperature": 0.1 }
            },
            timeout=60,
        )
        
        raw_response = resp.json().get("response", "{}")
        data = json.loads(raw_response)
        
        is_threat = data.get("is_operational_disruption", False)

        # Use subsector if it's a threat, otherwise use the analysis as the "reason"
        detail = data.get("subsector", "none") if is_threat else data.get("analysis", "No impact detected")
        
        return is_threat, detail

    except Exception as e:
        print(f"Error parsing AI response: {e}")
        return False, "Parsing Error"




'''
This function checks to see that we have 2 valid files in the data directory.
One being [title].json under the valid data/valid and the other being [title].txt under the invalid data/invalid.
This will automatically add the files if they are not found
'''
def check_valid_file(title):
    VALID_DIR.mkdir(parents=True, exist_ok=True)
    NOISE_DIR.mkdir(parents=True, exist_ok=True)
    VULNERABILITIES_DIR.mkdir(parents=True, exist_ok=True)

    json_path = VALID_DIR / f"{title.strip()}.json"
    if not json_path.exists():
        json_path.write_text(json.dumps({"sources": []}, indent=4), encoding="utf-8")
        print(f"Created {json_path}")

    noise_path = NOISE_DIR / f"{title.strip()}.csv"
    if not noise_path.exists():
        noise_path.write_text(csv.writer(noise_path))
        print(f"Created {noise_path}")

    vulnerabilities_path = VULNERABILITIES_DIR / f"{title.strip()}.csv"
    if not vulnerabilities_path.exists():
        vulnerabilities_path.write_text(csv.writer(vulnerabilities_path))
        print(f"Created {vulnerabilities_path}")
'''

'''
def json_output(site_name, title, url, body, subsector):
    print(f"[VALID] {title} | {url}") # makes easy to see, delete later
    json_path = VALID_DIR / f"{site_name.lower()}.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["sources"].append({
        "id": str(uuid.uuid4()), 
        "title": title,
        "souce_name": site_name,
        "direct_link": url,
        "subsector": subsector,
        "date_accessed": datetime.now().isoformat(),
        "date_published": datetime.now().isoformat(), # TODO: placeholder for now, fix later
        "content": body,
        "exec_summary": "",
        "subsector_data": {
            "device_name": "Infusion Pump Model X",
            "device_category": "Infusion Systems",
            "manufacturer": "Baxter International",
            "manufacturer_country": "US",
            "shortage_reason": "Manufacturing disruption",
            "fda_recall_number": "",
            "recall_class": "",
            "affected_specialties": "",
            "alternatives_available": "",
            "estimated_resolution_date": ""
        }
    })
    json_path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    print(f"[VALID] ([{subsector}]: {title}")

'''
This function is called after the AI determines that an articles is not a threat to the healthcare industry.
This will write to data/invalid/[site_name].txt and present the title with the URL. 
'''
def invalid_article_output(site_name, title, url, body, reason):
    txt_path = INVALID_DIR / f"{site_name.lower()}.txt"
    with open(txt_path, "a", encoding="utf-8") as f:
        f.write(f"* {title} | {url}\n  Reason: {reason}\n\n")

# Run everything in one go
for site in SITES_TO_SCRAPE:
    run_scraper(site)