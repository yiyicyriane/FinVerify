"""Deterministic financial ratio calculator.

This module is the single source of truth for ratio formulas. It is shared by:
  * the Ground Truth Builder (data/match_ground_truth.py)
  * Agent version C (LLM extraction + Python calculation)
  * Agent version D (XBRL oracle + Python calculation)

Do not re-implement these formulas anywhere else.

Every function returns a ``(value, status)`` tuple instead of a bare number so
that missing inputs and zero denominators propagate downstream explicitly
instead of silently becoming 0 or raising.

Status values:
  "ok"                             value computed normally
  "missing_input"                  at least one input is None
  "undefined_division"             denominator is 0
  "low_confidence_negative_equity" ROE computed, but average equity < 0

Formulas (fixed for all experiment versions):
  current_ratio       = current_assets / current_liabilities
  gross_profit_margin = (revenue - cost_of_revenue) / revenue
  roa                 = net_income / ((total_assets_begin + total_assets_end) / 2)
  roe                 = net_income / ((equity_begin + equity_end) / 2)
"""

from typing import Optional, Tuple

Number = Optional[float]
Result = Tuple[Optional[float], str]


def current_ratio(current_assets: Number, current_liabilities: Number) -> Result:
    """Short-term solvency: current assets divided by current liabilities."""
    if current_assets is None or current_liabilities is None:
        return None, "missing_input"
    if current_liabilities == 0:
        return None, "undefined_division"
    return current_assets / current_liabilities, "ok"


def gross_profit_margin(revenue: Number, cost_of_revenue: Number) -> Result:
    """Share of revenue left after cost of revenue: (revenue - COGS) / revenue."""
    if revenue is None or cost_of_revenue is None:
        return None, "missing_input"
    if revenue == 0:
        return None, "undefined_division"
    return (revenue - cost_of_revenue) / revenue, "ok"


def roa(net_income: Number, total_assets_begin: Number, total_assets_end: Number) -> Result:
    """Return on assets, using the average of beginning and ending total assets."""
    if None in (net_income, total_assets_begin, total_assets_end):
        return None, "missing_input"
    avg_assets = (total_assets_begin + total_assets_end) / 2
    if avg_assets == 0:
        return None, "undefined_division"
    return net_income / avg_assets, "ok"


def roe(net_income: Number, equity_begin: Number, equity_end: Number) -> Result:
    """Return on equity, using the average of beginning and ending stockholders' equity.

    A negative average equity still yields a number, but it is flagged as
    ``low_confidence_negative_equity`` because the ratio is not meaningful in
    conventional financial analysis.
    """
    if None in (net_income, equity_begin, equity_end):
        return None, "missing_input"
    avg_equity = (equity_begin + equity_end) / 2
    if avg_equity == 0:
        return None, "undefined_division"
    status = "low_confidence_negative_equity" if avg_equity < 0 else "ok"
    return net_income / avg_equity, status


# Registry used by the Ground Truth Builder: ratio name -> (function, ordered input names).
RATIOS = {
    "current_ratio": (current_ratio, ["current_assets", "current_liabilities"]),
    "gross_profit_margin": (gross_profit_margin, ["revenue", "cost_of_revenue"]),
    "roa": (roa, ["net_income", "total_assets_begin", "total_assets_end"]),
    "roe": (roe, ["net_income", "equity_begin", "equity_end"]),
}
