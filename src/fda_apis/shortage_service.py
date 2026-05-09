import requests
import time
import os
from typing import List
from datetime import datetime
from .models.drug_snapshot import DrugSnapshot
from .models.drug_snapshot import NDCSnapshot


def get_earliest_date(ndc_list: List[NDCSnapshot]):
    if not ndc_list:
        return "N/A"

    date_objects = []

    for ndc in ndc_list:
        date_str = ndc.shortage_start_date

        if date_str and date_str != "N/A":
            try:
                parsed_date = datetime.strptime(date_str, "%m/%d/%Y")
                date_objects.append(parsed_date)
            except ValueError:
                pass

    if not date_objects:
        return "N/A"

    earliest_date = min(date_objects)
    return earliest_date.strftime("%m/%d/%Y")


def get_therapeutic_class(current: DrugSnapshot, results):
    if current.therapeutic_class == "N/A":
        if results and len(results) > 0:
            current.therapeutic_class = results[0].get("therapeutic_category", "N/A")


def printIndivNDC(ndc_list: List[NDCSnapshot]):
    print("--- NDC VARIATIONS ---")
    if not ndc_list:
        print("  No NDC data found for this Set ID.")
        return

    for i, ndc in enumerate(ndc_list, 1):
        print(f"  {i}. NDC: {ndc.package_ndc} | {ndc.availability}")
        print(f"     Presentation:  {ndc.presentation}")
        print(f"     Reason:       {ndc.shortage_reason}")
        print(f"     Recovery:     {ndc.recovery_info}")
        print(f"     Start Date:   {ndc.shortage_start_date}")
        print(f"     Updated:      {ndc.last_updated}")
        print("     " + "-" * 50)


def printDrugSnapshot(snapshot: DrugSnapshot):
    print(
        "================================================================================================"
    )
    print(
        "================================================================================================"
    )
    print(
        "================================================================================================\n"
    )
    print(f"\n------ DATA REPORT FOR: {snapshot.drug_name} ------\n")
    print(f"Therapeutic Class: {snapshot.therapeutic_class}\n")
    print(f"Shortage Start Date: {snapshot.shortage_start_date}\n")
    print(f"Last Updated Date: {snapshot.shortage_last_updated_date}\n")
    print(f"FDA JSON Link: {snapshot.shortage_fda_link}\n")
    printIndivNDC(snapshot.ndc_map)
    print("\n")


def make_ndc_map(matched_results) -> List[NDCSnapshot]:
    ndc_list = []
    seen_ndcs = set()

    for record in matched_results:
        ndc = record.get("package_ndc", "N/A")

        if ndc in seen_ndcs:
            continue

        seen_ndcs.add(ndc)

        ndc_obj = NDCSnapshot(
            package_ndc=ndc,
            availability=record.get("availability", "N/A"),
            presentation=record.get("presentation", "N/A"),
            shortage_reason=record.get("shortage_reason", "N/A"),
            recovery_info=record.get("related_info", "N/A"),
            shortage_start_date=record.get("initial_posting_date", "N/A"),
            last_updated=record.get("update_date", "N/A"),
        )
        ndc_list.append(ndc_obj)

    return ndc_list


def collect_data(current_snapshot: DrugSnapshot, api_key: str) -> DrugSnapshot:
    BASE_URL = "https://api.fda.gov/drug/shortages.json"

    search_term = current_snapshot.drug_name
    if search_term == "N/A":
        return

    params = {
        "api_key": api_key,
        "search": f'generic_name:"{search_term}"',
        "limit": 100,
    }

    response = requests.get(BASE_URL, params=params)

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        return current_snapshot  # return as is

    data = response.json()
    results = data.get("results", [])
    meta = data.get("meta", [])

    current_snapshot.shortage_last_updated_date = meta.get("last_updated", "N/A")
    current_snapshot.shortage_fda_link = (
        f'{BASE_URL}?search=generic_name:"{search_term}"&limit=100'
    )

    matched_results = []
    wanted_spl = current_snapshot.spl_set_id

    for record in results:
        openfda = record.get("openfda", {})
        record_set_ids = openfda.get("spl_set_id", [])

        if wanted_spl != "N/A" and wanted_spl in record_set_ids:
            matched_results.append(record)

    get_therapeutic_class(current_snapshot, matched_results)
    current_snapshot.ndc_map = make_ndc_map(matched_results)
    current_snapshot.shortage_start_date = get_earliest_date(current_snapshot.ndc_map)

    return current_snapshot


def main():
    test_snapshot = DrugSnapshot()
    test_snapshot.drug_name = "Methylprednisolone Acetate"  # # 823b0010-2b57-4e76-b5ac-4a8c2963438f Depo-Medrol (Methylprednisolone Acetate Injection)
    # test_snapshot.spl_set_id = "823b0010-2b57-4e76-b5ac-4a8c2963438f"
    test_snapshot.spl_set_id = "a0cbcdb5-f657-49f7-8f86-cc5959d69db0"
    # test_drug = triamcinolone acetonide # "ec04ecbb-2896-3feb-85fd-a64aba93b289"  Kenalog-10 (triamcinolone acetonide injectable suspension, USP)

    API_KEY = os.getenv("FDA_SHORTAGE_API_KEY")
    snapshot = collect_data(test_snapshot, API_KEY)
    printDrugSnapshot(snapshot)

    time.sleep(0.2)


if __name__ == "__main__":
    main()
