import pandas as pd
import time
import asyncio
import sys
import os
from pathlib import Path

current_dir = Path(__file__).resolve().parent
scraper_dir = current_dir.parent / "scrapers"
if str(scraper_dir) not in sys.path:
    sys.path.append(str(scraper_dir))

from BERT_filter import run_bert_inference
from ollama_filter import ask_ai

""" This try block is not a good practice but it was just the way I could confirm I was getting the BERT scraper specifically"""
try:
    from bert_scraper import bert_scraper
except ImportError:
    print(f"Error: Could not find bert_scraper.py in {scraper_dir}")
    sys.exit(1)

async def run_race_from_existing_data(csv_input="frozen_race_data.csv"):
    print(f"Loading URLs from {csv_input}...")
    try:
        input_df = pd.read_csv(csv_input).fillna("")
        urls = input_df['url'].tolist()
    except Exception as e:
        print(f"Failed to load CSV: {e}")
        return

    race_results = []
    print(f"Re-running race on {len(urls)} historical URLs...\n")

    for i, url in enumerate(urls, start=1):
        print(f"[{i}/{len(urls)}] Processing: {url[:60]}...")
        
        start_scrape = time.perf_counter()
        try:
            content = bert_scraper(url)
            scrape_time = (time.perf_counter() - start_scrape) * 1000
            
            if not content or not content.get('body'):
                print(f"   [!] Skip: No content found for {url[:30]}")
                continue
        except Exception as e:
            print(f"   [!] Scrape Error: {e}")
            continue

        start_bert = time.perf_counter()
        bert_label = run_bert_inference(content)
        bert_time = (time.perf_counter() - start_bert) * 1000
        
        start_llama = time.perf_counter()
        try:
            llama_hit = ask_ai(url, content['body']) 
            llama_time = (time.perf_counter() - start_llama) * 1000
        except Exception as e:
            print(f"   [!] Llama Error: {e}")
            llama_hit = False
            llama_time = 0

        bert_hit_val = 1 if bert_label == "potential_hit" else 0
        llama_hit_val = 1 if llama_hit else 0
        
        race_results.append({
            "url": url,
            "scrape_time_ms": scrape_time,
            "bert_hit": bert_hit_val,
            "bert_time_ms": bert_time,
            "llama_hit": llama_hit_val,
            "llama_time_ms": llama_time,
            "is_correct": 1 if bert_hit_val == llama_hit_val else 0
        })

    output_df = pd.DataFrame(race_results)
    
    base_filename = "race_results"
    counter = 1
    while os.path.exists(f"{base_filename}_{counter}.csv"):
        counter += 1
    
    final_filename = f"{base_filename}_{counter}.csv"
    output_df.to_csv(final_filename, index=False)
    
    if not output_df.empty:
        acc = output_df['is_correct'].mean() * 100
        print(f"\n{'='*20} TEST COMPLETE {'='*20}")
        print(f"Accuracy vs Llama: {acc:.2f}%")
        print(f"Total processed:   {len(output_df)}")
        print(f"Results saved to:  {final_filename}")
        print(f"{'='*55}\n")
    else:
        print("\n[!] No results were generated. Check your scraper/CSV.")

if __name__ == "__main__":
    asyncio.run(run_race_from_existing_data())