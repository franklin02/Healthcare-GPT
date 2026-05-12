"""Benchmark BERT inference on frozen sample data and save results.

This module is for offline analysis and has no impact on the main pipeline.
It loads CSV data with article titles and bodies, runs BERT inference on
each sample, records inference time and classification results, and exports
to a CSV file for visualization or further analysis.
"""

import pandas as pd
import time
from BERT_filter import run_bert_inference


def run_offline_benchmark(csv_path="race_results.csv"):
    """Benchmark BERT inference on frozen samples and save timing results.

    Loads a CSV containing article data (title, body, url, llama_hit),
    runs `run_bert_inference` on each sample, records the inference time,
    compares BERT results against the llama_hit label, and exports to
    `bert_benchmark_results.csv`.

    Args:
        csv_path (str): Path to the input CSV. Expected columns: title, body,
            url, llama_hit. Defaults to "race_results.csv".

    Returns:
        pd.DataFrame: A dataframe with columns: url, llama_hit, bert_hit,
            inference_time_ms.
    """
    df = pd.read_csv(csv_path)
    results = []

    print(f"Benchmarking BERT against {len(df)} frozen samples...")

    for _, row in df.iterrows():
        data = {"title": row["title"], "body": row["body"]}

        start_time = time.perf_counter()
        bert_result = run_bert_inference(data)
        end_time = time.perf_counter()

        results.append(
            {
                "url": row["url"],
                "llama_hit": row["llama_hit"],
                "bert_hit": 1 if bert_result == "potential_hit" else 0,
                "inference_time_ms": (end_time - start_time) * 1000,
            }
        )

    benchmark_df = pd.DataFrame(results)
    benchmark_df.to_csv("bert_benchmark_results.csv", index=False)
    return benchmark_df


if __name__ == "__main__":
    run_offline_benchmark()
