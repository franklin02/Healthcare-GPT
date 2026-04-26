from dataclasses import dataclass, field, asdict
from typing import List
from .ndc_snapshot import NDCSnapshot


@dataclass
class DrugSnapshot:
    # ---------- FDA SPL files: https://open.fda.gov/apis/drug/label/ ----------
    drug_name: str = "N/A"
    drug_name_normalized: str = "N/A"
    spl_set_id: str = "N/A"
    product_ndc_list: List[str] = field(default_factory=list)
    dosage_form: str = "N/A"
    dosage_form_group: str = "N/A"
    route: str = "N/A"
    strength_concentration: str = "N/A"
    package_size: str = "N/A"
    packaging_type: str = "N/A"
    spl_last_updated: str = "N/A"
    spl_json_link: str = "N/A"

    # --- SHORTAGE STATUS (Shortage API) ---
    therapeutic_class: str = "N/A"  # "theraputic_category"
    shortage_status: str = "N/A"  # "availability"
    shortage_reason: str = "N/A"  # "shortage_reason"
    shortage_start_date: str = "N/A"  # "initial_posting_date"
    shortage_fda_link: str = "N/A"
    ndc_map: List[NDCSnapshot] = field(default_factory=list)
    shortage_last_updated_date: str = "N/A"  # update_date

    # --- MANUFACTURING & QUALITY (Label + Other APIs) ---
    fda_labeller_name: str = "N/A"
    actual_manufacturer_name: str = "N/A"
    manufacturer_country: str = "N/A"
    fill_finish_site_name: str = "N/A"
    fill_finish_site_location: str = "N/A"
    fill_finish_site_country: str = "N/A"
    api_manufacturer_name: str = "N/A"
    api_manufacturer_country: str = "N/A"
    api_source_country: str = "N/A"
    fda_inspection_quality_signal: str = "N/A"
    oai_vai_history_summary: str = "N/A"
    sterility_related_cfr_flag_211_113b: bool = False
    sterility_issue_notes: str = "N/A"

    # --- SCIENTIFIC & CLINICAL ---
    key_active_ingredient: str = "N/A"
    key_active_ingredient_api: str = "N/A"
    notable_inactive_ingredients: str = "N/A"
    approved_indications: str = "N/A"
    common_clinical_uses_primary: str = "N/A"
    common_clinical_uses_secondary: str = "N/A"
    storage_requirements: str = "N/A"
    shelf_life_labeled: str = "N/A"
    stability_in_use_or_post_reconstitution: str = "N/A"
    requires_cold_chain: bool = False
    injectable_presentation_type: str = "N/A"

    # --- MARKET & ALTERNATIVES ---
    price_signal_nadac: float = 0.0
    nadac_last_updated_date: str = "N/A"
    therapeutic_alternatives: str = "N/A"
    alternatives_cover_all_indications: bool = False
    alternatives_availability_status: str = "N/A"
    non_drug_alternatives: str = "N/A"

    # --- SUPPLY CHAIN INTELLIGENCE (ImportYeti / Sea Route) ---
    importyeti_company_page: str = "N/A"
    importyeti_ports_observed: str = "N/A"
    importyeti_origin_countries: str = "N/A"
    importyeti_shipment_volume_signal: str = "N/A"
    sea_route_ports_path_summary: str = "N/A"

    # --- METADATA & AUDIT ---
    distribution_notes: str = "N/A"
    related_supabase_tables: str = "N/A"
    data_sources_used: List[str] = field(default_factory=list)
    data_completeness_score: float = 0.0
    unknown_fields_list: List[str] = field(default_factory=list)
    analyst_notes: str = "N/A"
    last_reviewed_date: str = "N/A"
    reviewed_by: str = "N/A"

    def to_dict(self):
        return asdict(self)
