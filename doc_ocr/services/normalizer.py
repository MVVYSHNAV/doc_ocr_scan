"""
OCR Data Normalization and Validation Module
Cleans, validates and reconciles raw OCR-extracted invoice data
before it is mapped to ERPNext fields.
"""
import re
import math
from datetime import datetime


# ─────────────────────────────────────────────
# OCR Char Correction
# ─────────────────────────────────────────────
_NUMERIC_FIXES = str.maketrans({
    'O': '0', 'o': '0',
    'l': '1', 'I': '1',
    'S': '5', 's': '5',
    'Z': '2', 'z': '2',
    'B': '8',
    'G': '6',
    ' ': '',
})

def fix_ocr_chars(text):
    """Replace common OCR character misreads in a raw text string."""
    return text if isinstance(text, str) else ""


def fix_numeric_ocr(raw):
    """Fix OCR chars specifically in a numeric string."""
    if not raw:
        return ""
    return str(raw).strip().translate(_NUMERIC_FIXES)


# ─────────────────────────────────────────────
# Float Normalization
# ─────────────────────────────────────────────
def normalize_float(raw):
    """
    Safely convert an OCR-extracted numeric string to float.
    Returns (float_value, ok: bool)
    """
    if raw is None:
        return 0.0, False
    if isinstance(raw, (int, float)):
        return float(raw), True
    cleaned = str(raw)
    cleaned = re.sub(r'[₹$£€,\s]', '', cleaned)   # strip currency symbols / spaces
    cleaned = fix_numeric_ocr(cleaned)
    # Handle e.g. "1.234.56" (European) → "1234.56"
    if cleaned.count('.') > 1:
        parts = cleaned.rsplit('.', 1)
        cleaned = parts[0].replace('.', '') + '.' + parts[1]
    try:
        return float(cleaned), True
    except (ValueError, TypeError):
        return 0.0, False


# ─────────────────────────────────────────────
# Date Normalization
# ─────────────────────────────────────────────
_DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y",
    "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d",
    "%d.%m.%Y", "%m.%d.%Y",
    "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y",
    "%d-%b-%Y", "%d-%B-%Y",
]

def normalize_date(raw):
    """
    Parse any common date format and return ISO 'YYYY-MM-DD'.
    Returns (iso_string, ok: bool)
    """
    if not raw:
        return None, False
    cleaned = str(raw).strip()
    # Try known formats
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime('%Y-%m-%d'), True
        except ValueError:
            continue
    # Try dateutil as fallback
    try:
        from dateutil import parser as dp
        dt = dp.parse(cleaned, dayfirst=True)
        return dt.strftime('%Y-%m-%d'), True
    except Exception:
        pass
    return None, False


# ─────────────────────────────────────────────
# Line Item Validation
# ─────────────────────────────────────────────
def validate_line_items(raw_items):
    """
    Clean each line item — normalize types only.
    No auto-calculations, no merging, no inferences.
    """
    clean_items = []
    for idx, item in enumerate(raw_items):
        desc      = (item.get("description") or "").strip() or f"Item {idx + 1}"
        item_name = (item.get("item_name") or "").strip() or desc  # preserve Docling name
        qty,    _ = normalize_float(item.get("quantity") or item.get("qty") or 1)
        rate,   _ = normalize_float(item.get("rate") or 0)
        amount, _ = normalize_float(item.get("amount") or 0)
        tax,    _ = normalize_float(item.get("tax") or 0)

        # Only skip completely blank rows (no description AND all zeros)
        if not desc and qty == 0 and rate == 0 and amount == 0:
            continue

        clean_items.append({
            "item_name":   item_name,
            "description": desc,
            "quantity":    qty,
            "rate":        rate,
            "amount":      amount,
            "tax":         tax,
        })

    return clean_items, []  # no warnings generated here


# ─────────────────────────────────────────────
# Totals Reconciliation
# ─────────────────────────────────────────────
def reconcile_totals(data):
    """
    Flag totals inconsistencies as warnings — no calculations, no corrections.
    """
    warnings = []
    subtotal   = data.get("subtotal",   0) or 0
    tax_amount = data.get("tax_amount", 0) or 0
    grand_total = data.get("grand_total", 0) or 0

    if subtotal > 0 and grand_total > 0 and tax_amount > 0:
        inferred = round(subtotal + tax_amount, 2)
        if abs(inferred - grand_total) / max(grand_total, 1) > 0.01:
            warnings.append(
                f"Totals check: subtotal({subtotal}) + tax({tax_amount}) = {inferred}, "
                f"but grand_total = {grand_total}"
            )
    return warnings


# ─────────────────────────────────────────────
# Confidence Scoring
# ─────────────────────────────────────────────
def score_confidence(data):
    """
    Assign High / Medium / Low confidence based on:
    - Presence of required fields
    - Number of warnings
    - Item validation pass
    """
    score = 100
    warnings = data.get("warnings", [])
    score -= len(warnings) * 15

    required = ["invoice_number", "invoice_date", "grand_total"]
    for field in required:
        if not data.get(field):
            score -= 20

    if not data.get("items"):
        score -= 20

    if score >= 75:
        return "High"
    elif score >= 40:
        return "Medium"
    else:
        return "Low"


# ─────────────────────────────────────────────
# Full Normalization Pipeline
# ─────────────────────────────────────────────
def normalize_and_validate(parsed_data):
    """
    Run the full normalization pipeline on raw parsed OCR output.
    Returns a clean, validated dict safe for ERPNext field mapping.
    """
    result = {}
    all_warnings = []

    # Invoice Number
    inv_no = str(parsed_data.get("invoice_number") or "").strip()
    result["invoice_number"] = fix_numeric_ocr(inv_no) if inv_no else ""

    # Invoice Date
    raw_date = parsed_data.get("invoice_date") or ""
    iso_date, date_ok = normalize_date(raw_date)
    result["invoice_date"] = iso_date
    if not date_ok and raw_date:
        all_warnings.append(f"Could not parse invoice date: '{raw_date}'")

    # Numeric Fields
    for field in ("subtotal", "tax_amount", "grand_total"):
        val, ok = normalize_float(parsed_data.get(field))
        result[field] = round(val, 2)
        if not ok and parsed_data.get(field):
            all_warnings.append(f"Could not parse {field}: '{parsed_data.get(field)}'")

    # Party Name
    result["party_name"] = (parsed_data.get("party_name") or "").strip()

    # Line Items
    raw_items = parsed_data.get("items") or []
    valid_items, item_warnings = validate_line_items(raw_items)
    result["items"] = valid_items
    all_warnings.extend(item_warnings)

    # Totals Reconciliation
    recon_warnings = reconcile_totals(result)
    all_warnings.extend(recon_warnings)

    # Preserve raw text
    result["raw_text"] = parsed_data.get("raw_text", "")

    # Warnings and confidence
    result["warnings"] = all_warnings
    result["confidence"] = score_confidence(result)

    return result
