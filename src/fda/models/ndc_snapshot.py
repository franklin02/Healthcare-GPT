from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any

@dataclass
class NDCSnapshot:
    package_ndc: str = "N/A"
    availability: str = "N/A"
    presentation: str = "N/A"
    shortage_reason: str = "N/A"
    recovery_info: str = "N/A" # goes to 'related_info'
    shortage_start_date: str = "N/A" # "initial_posting_date"
    last_updated: str = "N/A"

    def to_dict(self):
        return asdict(self)