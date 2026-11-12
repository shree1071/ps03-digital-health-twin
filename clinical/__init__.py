"""Clinical monitoring core for MineArena PS-03.

Post-discharge deterioration monitoring: FHIR R4 ingestion, NEWS2-style
scoring personalized by discharge profile, sensor artifact suppression and
explained Watch / Review / Urgent alerts.

Clinical decision support only. Not a medical device, not a diagnosis.
"""

from clinical.news2 import News2Input, News2Result, Tier, score_news2
from clinical.profiles import Condition, DischargeProfile, Medication

__all__ = [
    "Condition",
    "DischargeProfile",
    "Medication",
    "News2Input",
    "News2Result",
    "Tier",
    "score_news2",
]