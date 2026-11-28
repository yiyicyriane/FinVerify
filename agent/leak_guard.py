"""Last check on the exact prompt text before it is sent to a model.

``data/clean_filing.py`` already checks the cleaned JSON for XBRL traces. This
module checks the final prompt string (system + user), which is what the model
actually sees.

Pattern design (recall first, boundaries only where false positives are real):
  * short prefixes ``ix:`` / ``dei:`` get boundaries, so "Appendix:" or
    "Matrix:" do not match;
  * ``us-gaap``, ``xbrli``, ``contextref``, ``unitref``, ``xmlns`` never occur in
    normal English, so plain substring matching (case-insensitive) is used;
    this also catches ``us-gaap_Revenues`` style ids;
  * the camel-case XBRL concept names the ground truth uses are matched as
    whole words, case-sensitive. They never appear in the visible text of a
    10-K, so a hit would be the most harmful kind of leak. One-word tags
    (``Revenues``, ``Assets``) are excluded on purpose: they appear in normal
    visible text such as "Net Operating Revenues".
"""

import re

PATTERNS = [
    re.compile(r"<\s*/?\s*ix:"),
    re.compile(r"\bix:[A-Za-z]"),
    re.compile(r"\bdei:[A-Za-z]"),  # dei element names start upper-case (dei:DocumentType)
    re.compile(r"us-gaap|xbrli|contextref|unitref|xmlns", re.IGNORECASE),
    re.compile(
        r"\b(AssetsCurrent|LiabilitiesCurrent|StockholdersEquity|NetIncomeLoss"
        r"|RevenueFromContractWithCustomerExcludingAssessedTax|SalesRevenueNet"
        r"|CostOfRevenue|CostOfGoodsAndServicesSold|CostOfGoodsSold)\b"
    ),
]


class LeakDetected(Exception):
    """Raised when a prompt contains XBRL traces; the caller records ``leak_check_failed``."""

    def __init__(self, hits):
        super().__init__(f"XBRL traces in prompt: {hits}")
        self.hits = hits


def find_leaks(text: str) -> list:
    """Return the distinct matched strings (empty list when the text is clean)."""
    hits = []
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            if match.group(0) not in hits:
                hits.append(match.group(0))
    return hits


def check_prompt(*parts: str) -> None:
    """Raise ``LeakDetected`` if any prompt part contains XBRL traces."""
    hits = find_leaks("\n".join(parts))
    if hits:
        raise LeakDetected(hits)
