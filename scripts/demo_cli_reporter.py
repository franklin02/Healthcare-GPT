"""Visual demo of the tqdm CLI reporter (single + multi-instance modes).

Simulates the pipeline with fake work so the sticky-bar layout can be seen
without scraping anything or calling an LLM. Not part of pytest.

Usage:
    python -m scripts.demo_cli_reporter                # single instance (default)
    python -m scripts.demo_cli_reporter --instances 3  # multi-instance preview
    python -m scripts.demo_cli_reporter --verbose      # annotate model endpoints
"""

import argparse
import random
import threading
import time

from src.cli_reporter import CliReporter, InstanceSpec, PipelineStats, whim

# (unit name, number of fake items) — mirrors GDELT + a few scraper sites.
UNITS = [
    ("GDELT", 12),
    ("CyberScoop", 8),
    ("StateScoop", 6),
    ("FedScoop", 6),
]
DEMO_MODEL = "demo-model:latest"


def fake_unit(
    reporter: CliReporter,
    unit: str,
    items: int,
    delay: float,
    stats: PipelineStats,
) -> None:
    """Run one fake unit of work against whatever bar resolves for it."""
    started = time.monotonic()
    # Model/endpoint metadata is inherited from the InstanceSpec declared at
    # build time, so each instance keeps its own endpoint annotation.
    bar = reporter.register_instance(unit, total=items)
    bar.set_step("starting")
    for i in range(1, items + 1):
        bar.set_step(whim(f"validating {i}/{items}"))
        time.sleep(delay)  # the "LLM call"
        stats.processed += 1
        roll = random.random()
        if roll < 0.25:
            stats.validated += 1
            reporter.info(f"[{unit}] validated a disruption on item {i}")
        elif roll < 0.85:
            stats.rejected += 1
        else:
            stats.skipped += 1
        if random.random() < 0.1:
            reporter.warn(f"[{unit}] flaky fetch on item {i}", stats)
        bar.set_step(f"{i}/{items}")
        bar.advance(1)
    stats.discovered = items
    stats.output_records = stats.validated
    stats.elapsed_seconds = time.monotonic() - started


def run_single(reporter: CliReporter, delay: float) -> list[PipelineStats]:
    """Sequential units on the shared task bar, exactly like the orchestrator."""
    stats_list = []
    for unit, items in UNITS:
        stats = PipelineStats(unit)
        reporter.set_overall_step(unit)
        fake_unit(reporter, unit, items, delay, stats)
        reporter.advance_overall(1)
        stats_list.append(stats)
    return stats_list


def run_multi(
    reporter: CliReporter, instances: int, delay: float
) -> list[PipelineStats]:
    """Round-robin the units across one bound worker thread per instance."""
    assignments: list[list[tuple[str, int]]] = [[] for _ in range(instances)]
    for index, unit in enumerate(UNITS):
        assignments[index % instances].append(unit)
    stats_list = [PipelineStats(unit) for unit, _ in UNITS]
    stats_by_unit = {stats.name: stats for stats in stats_list}
    reporter.set_overall_step(f"running {instances} instances")

    def worker(instance_name: str, units: list[tuple[str, int]]) -> None:
        with reporter.bind_instance(instance_name):
            for unit, items in units:
                fake_unit(reporter, unit, items, delay, stats_by_unit[unit])
                reporter.advance_overall(1)

    threads = [
        threading.Thread(target=worker, args=(f"Instance {i + 1}", assignments[i]))
        for i in range(instances)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return stats_list


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI reporter visual demo")
    parser.add_argument(
        "--instances", type=int, default=1, help="number of fake instances"
    )
    parser.add_argument(
        "--delay", type=float, default=0.05, help="seconds per fake item"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="annotate instance model endpoints"
    )
    args = parser.parse_args()

    # disable=False forces bars on even when output is piped/captured.
    with CliReporter(verbose=args.verbose, disable=False) as reporter:
        specs = [
            InstanceSpec(
                f"Instance {i + 1}",
                model=DEMO_MODEL,
                endpoint=f"http://localhost:1143{i + 1}",
            )
            for i in range(max(args.instances, 1))
        ]
        reporter.build_instances(specs, model_label=DEMO_MODEL)
        reporter.set_overall_total(len(UNITS))
        reporter.set_overall_step("Initializing")

        if len(specs) == 1:
            stats_list = run_single(reporter, args.delay)
        else:
            stats_list = run_multi(reporter, len(specs), args.delay)

        reporter.summary(stats_list)


if __name__ == "__main__":
    main()
