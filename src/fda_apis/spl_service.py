import re
import requests
import time
import os
from models.drug_snapshot import DrugSnapshot


def get_drug_name(openfda):
    brand = openfda.get("brand_name", ["N/A"])[0]
    generic = openfda.get("generic_name", ["N/A"])[0]
    return f"{brand} ({generic})" if brand and generic else (brand or generic or "N/A")


def get_spl_set_id(results, openfda):
    set_id = results.get("set_id")
    spl_set_id = openfda.get("spl_set_id")
    if set_id:
        return set_id
    if spl_set_id:
        return spl_set_id
    return "N/A"


def get_dosage_form(results, openfda):
    if openfda.get("dosage_form"):
        return openfda.get("dosage_form")[0].title()

    val = results.get("dosage_forms_and_strengths")
    if val:
        return val[0][:200].replace("\n", " ").strip()

    val = results.get("how_supplied")
    if val and isinstance(val, list) and len(val) > 0:
        text = val[0].replace("\n", " ").strip()
        # Cut at first period to get just the first sentence
        first_sentence = text.split(".")[0]
        return first_sentence.strip() + "."

    return "N/A"


def get_route(openfda):
    return openfda.get("route", ["N/A"])


def get_prod_ndc_list(openfda):
    return openfda.get("package_ndc", ["N/A"])


def cutWord(text, word):
    pattern = re.compile(re.escape(word), re.IGNORECASE)
    clean_text = pattern.sub("", text)
    return " ".join(clean_text.split())


def regex_unloop(text):
    if not text or len(text.split()) < 4:
        return text

    words = text.split()
    n = len(words)

    for size in range(n // 2, 1, -1):
        if n % size == 0:
            block = words[:size]
            if words == block * (n // size):
                return " ".join(block)

    first_word = words[0]
    for i in range(1, n):
        if words[i] == first_word:
            candidate_block = words[:i]
            if words[i : i + len(candidate_block)] == candidate_block:
                return " ".join(candidate_block)

    return text


def get_api(openfda):
    return openfda.get("substance_name", ["N/A"])[0]


def get_notable_inactive_ingredients(results, openfda):
    inactive_ingredients = results.get("inactive_ingredient", ["N/A"])[0]
    if inactive_ingredients == "N/A":
        inactive_ingredients = results.get("spl_product_data_elements", ["N/A"])[0]
    if inactive_ingredients == "N/A":
        return "N/A"

    final_clean = regex_unloop(inactive_ingredients)

    brand = openfda.get("brand_name", [None])[0]
    generic = openfda.get("generic_name", [None])[0]
    substance = get_api(openfda)

    to_remove = [brand, generic, substance]

    for word in to_remove:
        if word and word != "N/A":
            final_clean = cutWord(final_clean, word)

    return final_clean


def get_storage_requirements(results):
    return results.get("storage_and_handling", "N/A")[0]


def get_fda_labeller_name(openfda):
    return openfda.get("manufacturer_name", ["N/A"])[0]


def get_indications(results):
    return results.get("indications_and_usage", ["N/A"])[0]


def get_therapeutic_class(openfda):
    epc = openfda.get("pharm_class_epc")
    moa = openfda.get("pharm_class_moa")

    if epc:
        return epc[0]
    if moa:
        return moa[0]
    return "N/A"


def get_last_updated(meta):
    return meta.get("last_updated", ["N/A"])


def collect_data(set_id: str, api_key: str) -> DrugSnapshot:
    BASE_URL = "https://api.fda.gov/drug/label.json"

    params = {"api_key": api_key, "search": f'set_id:"{set_id}"', "limit": 1}

    response = requests.get(BASE_URL, params=params)

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        return DrugSnapshot()  # Return empty snapshot with all defaults

    data = response.json()
    results = data.get("results", [])[0]
    meta = data.get("meta", {})
    openfda = results.get("openfda", {})

    snapshot = DrugSnapshot()

    snapshot.drug_name = get_drug_name(openfda)
    snapshot.spl_set_id = get_spl_set_id(results, openfda)
    snapshot.dosage_form = get_dosage_form(results, openfda)
    snapshot.route = get_route(openfda)
    snapshot.product_ndc_list = get_prod_ndc_list(openfda)
    snapshot.key_active_ingredient = get_api(openfda)
    snapshot.notable_inactive_ingredients = get_notable_inactive_ingredients(
        results, openfda
    )
    snapshot.storage_requirements = get_storage_requirements(results)
    snapshot.fda_labeller_name = get_fda_labeller_name(openfda)
    snapshot.approved_indications = get_indications(results)
    snapshot.therapeutic_class = get_therapeutic_class(openfda)
    snapshot.data_sources_used = ["openFDA Drug Label API"]
    snapshot.spl_last_updated = f"FDA label last updated: {get_last_updated(meta)}"
    snapshot.spl_json_link = f'{BASE_URL}?search=set_id:"{set_id}"&limit=1'

    return snapshot


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
    print(f"JSON Link: {snapshot.spl_json_link}\n")
    print(f"Spl Set Id: {snapshot.spl_set_id}\n")
    print(f"Dosage Form: {snapshot.dosage_form}\n")
    print(f"Route: {snapshot.route}\n")
    print(f"Product NDC: {snapshot.product_ndc_list}\n")
    print(f"Key API: {snapshot.key_active_ingredient}\n")
    print(f"Inactive ingredients: {snapshot.notable_inactive_ingredients}\n")
    print(f"Storage Requirements: {snapshot.storage_requirements}\n")
    print(f"FDA Labeller: {snapshot.fda_labeller_name}\n")
    print(f"Primary Indications: {snapshot.approved_indications}\n")
    print(f"Therapeutic Class: {snapshot.therapeutic_class}\n")
    print(f"{snapshot.spl_last_updated}\n")
    print("\n")


def main():
    test_drug = "a0cbcdb5-f657-49f7-8f86-cc5959d69db0"
    # test_drug = "012d46f1-d0a0-4676-a879-cd320297ab16" # Bicillin L-A, Injection, 600000 [iU]/1 mL (Penicillin G Benzathine Injection)
    # ftest_drug = "823b0010-2b57-4e76-b5ac-4a8c2963438f" # Depo-Medrol (Methylprednisolone Acetate Injection)
    # test_drug = "ec04ecbb-2896-3feb-85fd-a64aba93b289" # Kenalog-10 (triamcinolone acetonide injectable suspension, USP)

    API_KEY = os.getenv("FDA_SPL_API_KEY")
    snapshot = collect_data(test_drug, API_KEY)
    printDrugSnapshot(snapshot)
    time.sleep(0.2)


if __name__ == "__main__":
    main()
