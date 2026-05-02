import pandas as pd
import time
from BERT_filter import run_bert_inference

"""
This module is purely for saving data for later analysis it has no impact
on the overall pipeline.
"""


def run_offline_benchmark(csv_path="race_results.csv"):
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
