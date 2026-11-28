"""Build the known-answer toy filings (``python -m experiments.toy_filings.build_toys``).

Each toy is written in the same format as data/cleaned_filings/*.json, so it
goes through exactly the same prompt rendering as a real filing. The expected
answers below are computed BY HAND (written as literals, never with
agent/calculator.py), so they also check the calculator itself.

Toys and what they test:
  TOY-SIMPLE    round numbers, millions                       -> all four ratios trivial
  TOY-THOUSANDS statement "in thousands"                      -> scale handling
  TOY-ZEROCL    zero current liabilities                      -> current_ratio undefined (no number)
  TOY-NEGEQ     stockholders' deficit, values in parentheses  -> negative numbers, low-confidence ROE
  TOY-NOCOGS    services company without a cost-of-revenue line -> gross margin must abstain
  TOY-NCI       noncontrolling interests                      -> parent equity and parent net income
"""

import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
FY, PY, PPY = "December 31, 2025", "December 31, 2024", "December 31, 2023"


def statement(heading, unit_note, rows, periods):
    return {"heading_matched": heading, "unit_note": unit_note, "periods": periods,
            "tables_joined": 1, "rowspan_cells": 0, "rows": rows}


def filing(filing_id, company, unit_note, bs_rows, is_rows, cf_rows):
    return {
        "filing_id": filing_id,
        "company": company,
        "cik": None,
        "fiscal_year": 2025,
        "accession_number": None,
        "period_end": "2025-12-31",
        "source_url": None,
        "synthetic": True,
        "statements": {
            "balance_sheet": statement("CONSOLIDATED BALANCE SHEETS", unit_note,
                                       [["", FY, PY]] + bs_rows, ["2025", "2024"]),
            "income_statement": statement("CONSOLIDATED STATEMENTS OF OPERATIONS", unit_note,
                                          [["", "Years ended", "", ""], ["", FY, PY, PPY]] + is_rows,
                                          ["2025", "2024", "2023"]),
            "cash_flow_statement": statement("CONSOLIDATED STATEMENTS OF CASH FLOWS", unit_note,
                                             [["", "Years ended", "", ""], ["", FY, PY, PPY]] + cf_rows,
                                             ["2025", "2024", "2023"]),
        },
    }


def item(value, scale, period_end, statement_key, label):
    return {"value_reported": value, "scale": scale, "period_end": period_end,
            "source_statement": statement_key, "source_label": label,
            "status": "extracted" if value is not None else "not_found"}


E, B = "2025-12-31", "2024-12-31"

TOYS = []

# --- TOY-SIMPLE (millions) ---------------------------------------------------
TOYS.append((
    filing("TOY-SIMPLE_2025-12-31_10K", "TOYSIMPLE", "(In millions)",
           [["Current assets:", "", ""], ["Cash and cash equivalents", "$80", "$60"],
            ["Accounts receivable, net", "70", "50"], ["Inventories", "50", "40"],
            ["Total current assets", "200", "150"], ["Property and equipment, net", "400", "250"],
            ["Total assets", "$600", "$400"], ["Current liabilities:", "", ""],
            ["Accounts payable", "$60", "$50"], ["Accrued expenses", "40", "30"],
            ["Total current liabilities", "100", "80"], ["Long-term debt", "200", "120"],
            ["Total liabilities", "300", "200"], ["Stockholders' equity:", "", ""],
            ["Common stock and additional paid-in capital", "200", "150"], ["Retained earnings", "100", "50"],
            ["Total stockholders' equity", "300", "200"],
            ["Total liabilities and stockholders' equity", "$600", "$400"]],
           [["Net sales", "$1,000", "$900", "$800"], ["Cost of sales", "600", "560", "500"],
            ["Gross margin", "400", "340", "300"], ["Operating expenses", "330", "290", "260"],
            ["Operating income", "70", "50", "40"], ["Provision for income taxes", "20", "15", "10"],
            ["Net income", "$50", "$35", "$30"]],
           [["Net income", "$50", "$35", "$30"], ["Depreciation", "20", "15", "10"],
            ["Net cash provided by operating activities", "70", "50", "40"],
            ["Capital expenditures", "(180)", "(60)", "(40)"],
            ["Net cash used in investing activities", "(180)", "(60)", "(40)"],
            ["Proceeds from borrowings", "80", "20", "10"], ["Proceeds from issuance of stock", "50", "0", "0"],
            ["Net cash provided by financing activities", "130", "20", "10"],
            ["Net change in cash", "20", "10", "10"]]),
    {
        "current_ratio": ({"current_assets": item(200, "millions", E, "balance_sheet", "Total current assets"),
                           "current_liabilities": item(100, "millions", E, "balance_sheet", "Total current liabilities")},
                          2.0, "ok"),
        "gross_profit_margin": ({"revenue": item(1000, "millions", E, "income_statement", "Net sales"),
                                 "cost_of_revenue": item(600, "millions", E, "income_statement", "Cost of sales")},
                                0.4, "ok"),
        "roa": ({"net_income": item(50, "millions", E, "income_statement", "Net income"),
                 "total_assets_begin": item(400, "millions", B, "balance_sheet", "Total assets"),
                 "total_assets_end": item(600, "millions", E, "balance_sheet", "Total assets")},
                0.1, "ok"),
        "roe": ({"net_income": item(50, "millions", E, "income_statement", "Net income"),
                 "equity_begin": item(200, "millions", B, "balance_sheet", "Total stockholders' equity"),
                 "equity_end": item(300, "millions", E, "balance_sheet", "Total stockholders' equity")},
                0.2, "ok"),
    },
))

# --- TOY-THOUSANDS (thousands) -------------------------------------------------
TOYS.append((
    filing("TOY-THOUSANDS_2025-12-31_10K", "TOYTHOUSANDS", "(In thousands)",
           [["Current assets:", "", ""], ["Cash and cash equivalents", "$1,500", "$1,300"],
            ["Accounts receivable, net", "2,000", "1,600"], ["Inventories", "1,000", "1,000"],
            ["Total current assets", "4,500", "3,900"], ["Property and equipment, net", "1,500", "100"],
            ["Total assets", "$6,000", "$4,000"], ["Current liabilities:", "", ""],
            ["Accounts payable", "$2,000", "$1,400"], ["Accrued expenses", "1,000", "600"],
            ["Total current liabilities", "3,000", "2,000"], ["Long-term debt", "500", "500"],
            ["Total liabilities", "3,500", "2,500"], ["Stockholders' equity:", "", ""],
            ["Common stock", "1,000", "600"], ["Retained earnings", "1,500", "900"],
            ["Total stockholders' equity", "2,500", "1,500"],
            ["Total liabilities and stockholders' equity", "$6,000", "$4,000"]],
           [["Revenue", "$8,000", "$7,000", "$6,500"], ["Cost of revenue", "6,000", "5,400", "5,100"],
            ["Gross profit", "2,000", "1,600", "1,400"], ["Selling, general and administrative", "1,450", "1,200", "1,050"],
            ["Income before income taxes", "550", "400", "350"], ["Income tax expense", "150", "100", "90"],
            ["Net income", "$400", "$300", "$260"]],
           [["Net income", "$400", "$300", "$260"], ["Depreciation and amortization", "100", "20", "20"],
            ["Changes in working capital", "(200)", "(50)", "(40)"],
            ["Net cash provided by operating activities", "300", "270", "240"],
            ["Purchases of property and equipment", "(1,500)", "(50)", "(40)"],
            ["Net cash used in investing activities", "(1,500)", "(50)", "(40)"],
            ["Proceeds from issuance of common stock", "400", "0", "0"],
            ["Proceeds from (repayments of) borrowings, net", "1,000", "(100)", "(100)"],
            ["Net cash provided by financing activities", "1,400", "(100)", "(100)"],
            ["Net change in cash", "200", "120", "100"]]),
    {
        "current_ratio": ({"current_assets": item(4500, "thousands", E, "balance_sheet", "Total current assets"),
                           "current_liabilities": item(3000, "thousands", E, "balance_sheet", "Total current liabilities")},
                          1.5, "ok"),
        "gross_profit_margin": ({"revenue": item(8000, "thousands", E, "income_statement", "Revenue"),
                                 "cost_of_revenue": item(6000, "thousands", E, "income_statement", "Cost of revenue")},
                                0.25, "ok"),
        "roa": ({"net_income": item(400, "thousands", E, "income_statement", "Net income"),
                 "total_assets_begin": item(4000, "thousands", B, "balance_sheet", "Total assets"),
                 "total_assets_end": item(6000, "thousands", E, "balance_sheet", "Total assets")},
                0.08, "ok"),
        "roe": ({"net_income": item(400, "thousands", E, "income_statement", "Net income"),
                 "equity_begin": item(1500, "thousands", B, "balance_sheet", "Total stockholders' equity"),
                 "equity_end": item(2500, "thousands", E, "balance_sheet", "Total stockholders' equity")},
                0.2, "ok"),
    },
))

# --- TOY-ZEROCL (no current liabilities) -------------------------------------------
TOYS.append((
    filing("TOY-ZEROCL_2025-12-31_10K", "TOYZEROCL", "(In millions)",
           [["Current assets:", "", ""], ["Cash and cash equivalents", "$60", "$50"],
            ["Short-term investments", "40", "30"], ["Total current assets", "100", "80"],
            ["Long-term investments", "400", "320"], ["Total assets", "$500", "$400"],
            ["Current liabilities:", "", ""], ["Total current liabilities", "0", "0"],
            ["Total liabilities", "0", "0"], ["Stockholders' equity:", "", ""],
            ["Common stock and additional paid-in capital", "350", "290"], ["Retained earnings", "150", "110"],
            ["Total stockholders' equity", "500", "400"],
            ["Total liabilities and stockholders' equity", "$500", "$400"]],
           [["Revenue", "$300", "$250", "$200"], ["Cost of revenue", "120", "100", "80"],
            ["Gross profit", "180", "150", "120"], ["Operating expenses", "130", "110", "95"],
            ["Income before income taxes", "50", "40", "25"], ["Income tax expense", "10", "8", "5"],
            ["Net income", "$40", "$32", "$20"]],
           [["Net income", "$40", "$32", "$20"], ["Net cash provided by operating activities", "40", "32", "20"],
            ["Purchases of investments", "(90)", "(60)", "(40)"],
            ["Net cash used in investing activities", "(90)", "(60)", "(40)"],
            ["Proceeds from issuance of common stock", "60", "40", "30"],
            ["Net cash provided by financing activities", "60", "40", "30"],
            ["Net change in cash", "10", "12", "10"]]),
    {
        # current_ratio: denominator is zero -> no number. C/D: undefined_division; LLM versions should return null.
        "current_ratio": ({"current_assets": item(100, "millions", E, "balance_sheet", "Total current assets"),
                           "current_liabilities": item(0, "millions", E, "balance_sheet", "Total current liabilities")},
                          None, "undefined_division"),
        "gross_profit_margin": ({"revenue": item(300, "millions", E, "income_statement", "Revenue"),
                                 "cost_of_revenue": item(120, "millions", E, "income_statement", "Cost of revenue")},
                                0.6, "ok"),
        # 40 / ((400 + 500) / 2) = 40 / 450 = 0.08888...
        "roa": ({"net_income": item(40, "millions", E, "income_statement", "Net income"),
                 "total_assets_begin": item(400, "millions", B, "balance_sheet", "Total assets"),
                 "total_assets_end": item(500, "millions", E, "balance_sheet", "Total assets")},
                0.0888888888888889, "ok"),
        "roe": ({"net_income": item(40, "millions", E, "income_statement", "Net income"),
                 "equity_begin": item(400, "millions", B, "balance_sheet", "Total stockholders' equity"),
                 "equity_end": item(500, "millions", E, "balance_sheet", "Total stockholders' equity")},
                0.0888888888888889, "ok"),
    },
))

# --- TOY-NEGEQ (stockholders' deficit) ------------------------------------------
TOYS.append((
    filing("TOY-NEGEQ_2025-12-31_10K", "TOYNEGEQ", "(In millions)",
           [["Current assets:", "", ""], ["Cash and cash equivalents", "$90", "$85"],
            ["Accounts receivable, net", "60", "55"], ["Total current assets", "150", "140"],
            ["Property and equipment, net", "150", "140"], ["Total assets", "$300", "$280"],
            ["Current liabilities:", "", ""], ["Accounts payable", "$70", "$60"],
            ["Accrued expenses", "30", "30"], ["Total current liabilities", "100", "90"],
            ["Long-term debt", "320", "270"], ["Total liabilities", "420", "360"],
            ["Stockholders' deficit:", "", ""], ["Common stock and additional paid-in capital", "50", "50"],
            ["Accumulated deficit", "(170)", "(130)"], ["Total stockholders' deficit", "(120)", "(80)"],
            ["Total liabilities and stockholders' deficit", "$300", "$280"]],
           [["Net revenues", "$500", "$470", "$450"], ["Cost of revenues", "350", "335", "320"],
            ["Gross profit", "150", "135", "130"], ["Operating expenses", "105", "100", "98"],
            ["Interest expense", "(15)", "(12)", "(10)"], ["Income before income taxes", "30", "23", "22"],
            ["Income tax expense", "5", "3", "4"], ["Net income", "$25", "$20", "$18"]],
           [["Net income", "$25", "$20", "$18"], ["Net cash provided by operating activities", "45", "40", "35"],
            ["Capital expenditures", "(20)", "(15)", "(15)"], ["Net cash used in investing activities", "(20)", "(15)", "(15)"],
            ["Share repurchases", "(65)", "(30)", "(20)"], ["Proceeds from long-term debt", "45", "10", "5"],
            ["Net cash used in financing activities", "(20)", "(20)", "(15)"],
            ["Net change in cash", "5", "5", "5"]]),
    {
        "current_ratio": ({"current_assets": item(150, "millions", E, "balance_sheet", "Total current assets"),
                           "current_liabilities": item(100, "millions", E, "balance_sheet", "Total current liabilities")},
                          1.5, "ok"),
        "gross_profit_margin": ({"revenue": item(500, "millions", E, "income_statement", "Net revenues"),
                                 "cost_of_revenue": item(350, "millions", E, "income_statement", "Cost of revenues")},
                                0.3, "ok"),
        # 25 / ((280 + 300) / 2) = 25 / 290 = 0.086206896...
        "roa": ({"net_income": item(25, "millions", E, "income_statement", "Net income"),
                 "total_assets_begin": item(280, "millions", B, "balance_sheet", "Total assets"),
                 "total_assets_end": item(300, "millions", E, "balance_sheet", "Total assets")},
                0.08620689655172414, "ok"),
        # 25 / ((-80 + -120) / 2) = 25 / -100 = -0.25, flagged low confidence
        "roe": ({"net_income": item(25, "millions", E, "income_statement", "Net income"),
                 "equity_begin": item(-80, "millions", B, "balance_sheet", "Total stockholders' deficit"),
                 "equity_end": item(-120, "millions", E, "balance_sheet", "Total stockholders' deficit")},
                -0.25, "low_confidence_negative_equity"),
    },
))

# --- TOY-NOCOGS (services company, no cost-of-revenue line) -------------------------
TOYS.append((
    filing("TOY-NOCOGS_2025-12-31_10K", "TOYNOCOGS", "(In millions)",
           [["Current assets:", "", ""], ["Cash and cash equivalents", "$50", "$40"],
            ["Accounts receivable, net", "40", "30"], ["Total current assets", "90", "70"],
            ["Goodwill and intangible assets", "210", "130"], ["Total assets", "$300", "$200"],
            ["Current liabilities:", "", ""], ["Accrued compensation", "$40", "$35"],
            ["Deferred revenue", "20", "15"], ["Total current liabilities", "60", "50"],
            ["Long-term debt", "90", "50"], ["Total liabilities", "150", "100"],
            ["Stockholders' equity:", "", ""], ["Common stock and additional paid-in capital", "100", "80"],
            ["Retained earnings", "50", "20"], ["Total stockholders' equity", "150", "100"],
            ["Total liabilities and stockholders' equity", "$300", "$200"]],
           [["Revenues", "$400", "$350", "$300"], ["Operating expenses:", "", "", ""],
            ["Compensation and benefits", "250", "225", "200"], ["General and administrative", "100", "90", "80"],
            ["Total operating expenses", "350", "315", "280"], ["Operating income", "50", "35", "20"],
            ["Income tax expense", "20", "14", "8"], ["Net income", "$30", "$21", "$12"]],
           [["Net income", "$30", "$21", "$12"], ["Net cash provided by operating activities", "40", "30", "20"],
            ["Acquisitions, net of cash acquired", "(80)", "(10)", "(5)"],
            ["Net cash used in investing activities", "(80)", "(10)", "(5)"],
            ["Proceeds from (repayments of) borrowings, net", "40", "(10)", "(5)"],
            ["Proceeds from issuance of stock", "10", "0", "0"],
            ["Net cash provided by financing activities", "50", "(10)", "(5)"],
            ["Net change in cash", "10", "10", "10"]]),
    {
        "current_ratio": ({"current_assets": item(90, "millions", E, "balance_sheet", "Total current assets"),
                           "current_liabilities": item(60, "millions", E, "balance_sheet", "Total current liabilities")},
                          1.5, "ok"),
        # No cost-of-revenue line: every version should abstain (C/D: missing_input).
        "gross_profit_margin": ({"revenue": item(400, "millions", E, "income_statement", "Revenues"),
                                 "cost_of_revenue": item(None, None, None, None, None)},
                                None, "missing_input"),
        # 30 / ((200 + 300) / 2) = 30 / 250 = 0.12
        "roa": ({"net_income": item(30, "millions", E, "income_statement", "Net income"),
                 "total_assets_begin": item(200, "millions", B, "balance_sheet", "Total assets"),
                 "total_assets_end": item(300, "millions", E, "balance_sheet", "Total assets")},
                0.12, "ok"),
        # 30 / ((100 + 150) / 2) = 30 / 125 = 0.24
        "roe": ({"net_income": item(30, "millions", E, "income_statement", "Net income"),
                 "equity_begin": item(100, "millions", B, "balance_sheet", "Total stockholders' equity"),
                 "equity_end": item(150, "millions", E, "balance_sheet", "Total stockholders' equity")},
                0.24, "ok"),
    },
))

# --- TOY-NCI (noncontrolling interests) ----------------------------------------------
TOYS.append((
    filing("TOY-NCI_2025-12-31_10K", "TOYNCI", "(In millions)",
           [["Current assets:", "", ""], ["Cash and cash equivalents", "$300", "$250"],
            ["Accounts receivable, net", "250", "220"], ["Inventories", "150", "130"],
            ["Total current assets", "700", "600"], ["Property, plant and equipment, net", "1,700", "1,400"],
            ["Total assets", "$2,400", "$2,000"], ["Current liabilities:", "", ""],
            ["Accounts payable", "$300", "$270"], ["Accrued liabilities", "200", "180"],
            ["Total current liabilities", "500", "450"], ["Long-term debt", "800", "650"],
            ["Total liabilities", "1,300", "1,100"], ["Equity:", "", ""],
            ["Common stock and additional paid-in capital", "600", "500"], ["Retained earnings", "400", "300"],
            ["Total stockholders' equity attributable to the parent", "1,000", "800"],
            ["Noncontrolling interests", "100", "100"], ["Total equity", "1,100", "900"],
            ["Total liabilities and equity", "$2,400", "$2,000"]],
           [["Net sales", "$2,000", "$1,800", "$1,650"], ["Cost of goods sold", "1,300", "1,190", "1,100"],
            ["Gross profit", "700", "610", "550"], ["Operating expenses", "540", "480", "440"],
            ["Income before income taxes", "160", "130", "110"], ["Income tax expense", "40", "32", "27"],
            ["Consolidated net income", "120", "98", "83"],
            ["Less: Net income attributable to noncontrolling interests", "20", "18", "15"],
            ["Net income attributable to the parent", "$100", "$80", "$68"]],
           [["Consolidated net income", "$120", "$98", "$83"], ["Depreciation", "90", "80", "75"],
            ["Net cash provided by operating activities", "210", "178", "158"],
            ["Capital expenditures", "(390)", "(200)", "(150)"],
            ["Net cash used in investing activities", "(390)", "(200)", "(150)"],
            ["Proceeds from borrowings", "150", "50", "20"], ["Proceeds from issuance of stock", "100", "0", "0"],
            ["Dividends paid to noncontrolling interests", "(20)", "(18)", "(15)"],
            ["Net cash provided by financing activities", "230", "32", "5"],
            ["Net change in cash", "50", "10", "13"]]),
    {
        # 700 / 500 = 1.4
        "current_ratio": ({"current_assets": item(700, "millions", E, "balance_sheet", "Total current assets"),
                           "current_liabilities": item(500, "millions", E, "balance_sheet", "Total current liabilities")},
                          1.4, "ok"),
        # (2000 - 1300) / 2000 = 0.35
        "gross_profit_margin": ({"revenue": item(2000, "millions", E, "income_statement", "Net sales"),
                                 "cost_of_revenue": item(1300, "millions", E, "income_statement", "Cost of goods sold")},
                                0.35, "ok"),
        # parent net income 100 / ((2000 + 2400) / 2) = 100 / 2200 = 0.04545...
        "roa": ({"net_income": item(100, "millions", E, "income_statement", "Net income attributable to the parent"),
                 "total_assets_begin": item(2000, "millions", B, "balance_sheet", "Total assets"),
                 "total_assets_end": item(2400, "millions", E, "balance_sheet", "Total assets")},
                0.045454545454545456, "ok"),
        # 100 / ((800 + 1000) / 2) = 100 / 900 = 0.1111...  (not 120 / 1000 with NCI)
        "roe": ({"net_income": item(100, "millions", E, "income_statement", "Net income attributable to the parent"),
                 "equity_begin": item(800, "millions", B, "balance_sheet", "Total stockholders' equity attributable to the parent"),
                 "equity_end": item(1000, "millions", E, "balance_sheet", "Total stockholders' equity attributable to the parent")},
                0.1111111111111111, "ok"),
    },
))


def main():
    expected = {}
    for doc, answers in TOYS:
        path = OUT_DIR / f"{doc['filing_id']}.json"
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        expected[doc["filing_id"]] = {
            ratio: {"items": items, "expected_ratio": value, "expected_status": status}
            for ratio, (items, value, status) in answers.items()
        }
        print(f"wrote {path.name}")
    (OUT_DIR / "expected.json").write_text(json.dumps(expected, indent=1) + "\n", encoding="utf-8")
    print("wrote expected.json")


if __name__ == "__main__":
    main()
