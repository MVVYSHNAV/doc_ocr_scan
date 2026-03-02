"""
OCR Processing Service
Runs Tesseract + Docling in a background job, then persists results to Invoice OCR via db_set
(never doc.save) to avoid timestamp conflicts with concurrent user saves.
"""
import frappe
import pytesseract
from pdf2image import convert_from_path
import os
import json
import re

from doc_ocr.services.normalizer import normalize_and_validate, normalize_float
from doc_ocr.services import docling_service


# ─────────────────────────────────────────────────────────────────────────────
# Background Job Entry Point
# ─────────────────────────────────────────────────────────────────────────────
def process_invoice_ocr(doc_name):
    """Background job: Tesseract + Docling → normalise → persist (no doc.save)."""
    doc = frappe.get_doc("Invoice OCR", doc_name)
    doc.db_set("ocr_status", "Processing")

    try:
        file_path = _resolve_file_path(doc)

        # 1. Tesseract text extraction + regex parse
        raw = parse_invoice_text(_run_tesseract(file_path))

        # 2. Docling structure layer (fail-safe)
        engine, raw = _run_docling(file_path, raw)

        # 2b. Fallback: If Docling found NO items, try extracting from Tesseract text
        if not raw.get("items") and raw.get("raw_text"):
            fallback_items = []
            # Look for typical item lines ending with dollar/currency amounts
            # E.g., DO: "zabbix-updated (s-lvcpu-2gb) 672 02-01 00:00 03-01 00:00 $12.00"
            for line in raw["raw_text"].split("\n"):
                # matches things like: <Item Description> ... $12.34
                m = re.search(r'^([A-Za-z0-9_( \)-]+(?:(?:20\d{2}|[0-3]\d-[0-1]\d)[ \d:]+)*)\s+(?:[₹$£€])?([\d.,]+)$', line.strip())
                if m:
                    desc_raw = m.group(1).strip()
                    amt_str = m.group(2)
                    amt, _ = normalize_float(amt_str)
                    
                    # Ignore random totals lines
                    skip_words = ["total", "due", "payable", "summary", "tax", "gst", "page "]
                    is_total_line = any(w in desc_raw.lower() for w in skip_words)
                    
                    if amt > 0 and len(desc_raw) > 3 and not is_total_line:
                        fallback_items.append({
                            "description": desc_raw[:140],
                            "quantity": 1,
                            "rate": amt,
                            "amount": amt,
                            "tax": 0
                        })
            raw["items"] = fallback_items

        # 3. Normalise + validate
        data = normalize_and_validate(raw)
        data["engine"] = engine

        # 4. Persist
        _persist(doc_name, data, engine)

    except Exception as e:
        frappe.db.rollback()
        frappe.db.set_value("Invoice OCR", doc_name, {
            "ocr_status": "Failed",
            "extracted_data": json.dumps({"error": str(e)}, indent=2),
        })
        frappe.db.commit()
        frappe.log_error(
            f"OCR Processing Failed for {doc_name}:\n{frappe.get_traceback()}",
            "Invoice OCR"
        )




# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_file_path(doc):
    if not doc.file:
        frappe.throw("No file attached to Invoice OCR")
    if doc.file.startswith(("/files/", "/private/files/")):
        file_doc = frappe.get_last_doc("File", {"file_url": doc.file})
        return file_doc.get_full_path()
    path = frappe.get_site_path("public", doc.file.lstrip("/"))
    if not os.path.exists(path):
        frappe.throw(f"File not found: {path}")
    return path


def _run_tesseract(file_path):
    text = ""
    if file_path.lower().endswith(".pdf"):
        images = convert_from_path(file_path, dpi=200)
        for img in images:
            text += pytesseract.image_to_string(img, config="--psm 6") + "\n"
    else:
        text = pytesseract.image_to_string(file_path, config="--psm 6")
    return text


def _run_docling(file_path, raw):
    engine = "Tesseract"
    try:
        result = docling_service.extract_with_docling(file_path)
        if result:
            if result.get("items"):
                raw["items"] = result["items"]
            for key in ("subtotal", "tax_amount", "grand_total"):
                val = result.get("summary", {}).get(key, 0)
                if val and val > 0:
                    raw[key] = val
            if result.get("raw"):
                raw["docling_raw"] = result["raw"][:3000]
            engine = "Tesseract + Docling"
    except Exception as e:
        frappe.log_error(f"Docling failed (non-fatal): {e}", "Invoice OCR - Docling")
    return engine, raw


def _persist(doc_name, data, engine):
    """Write extracted fields directly to DB — no doc.save() to avoid timestamp errors."""
    items_list      = data.get("items", [])
    parent_tax      = data.get("tax_amount", 0) or 0
    parent_subtotal = data.get("subtotal",   0) or 0
    parent_grand    = data.get("grand_total", 0) or 0
    total_item_amt  = sum(i.get("amount", 0) for i in items_list) or parent_subtotal or 1

    # Enrich items with computed tax / grand_total / item_name before saving JSON
    enriched_items = []
    items_list = [item for item in items_list if bool(item.get("description") or item.get("item_name"))] # Filter out empty items
    for item in items_list:
        item_amt  = item.get("amount", 0)
        item_tax  = item.get("tax", 0)
        item_name = item.get("item_name") or item.get("description", "OCR Item")

        if item_tax == 0 and parent_tax > 0:
            item_tax = round(parent_tax * item_amt / total_item_amt, 2)

        row_grand = round(item_amt + item_tax, 2)
        if len(items_list) == 1 and parent_grand > 0:
            row_grand = parent_grand

        enriched_items.append({
            "item_name":   item_name,
            "description": item.get("description", item_name),
            "quantity":    item.get("quantity", 1),
            "rate":        item.get("rate", 0),
            "amount":      item_amt,
            "tax":         item_tax,
            "grand_total": row_grand,
        })

    data["items"] = enriched_items   # stored back so extracted_data JSON is correct

    # Header fields
    update = {
        "ocr_status":     "Extracted",
        "ocr_engine":     engine,
        "extracted_data": json.dumps(data, indent=2),
    }
    if data.get("invoice_number"): update["invoice_number"] = data["invoice_number"]
    if data.get("invoice_date"):   update["invoice_date"]   = data["invoice_date"]
    if data.get("party_name"):     update["name1"]          = data["party_name"]
    if data.get("tax_amount"):     update["tax_amount"]     = data["tax_amount"]
    if data.get("grand_total"):    update["grand_total"]    = data["grand_total"]

    frappe.db.set_value("Invoice OCR", doc_name, update)

    # Child table — delete existing rows then insert fresh
    frappe.db.delete("Invoice OCR Item", {"parent": doc_name})
    for idx, item in enumerate(enriched_items, start=1):
        row = frappe.new_doc("Invoice OCR Item")
        row.parent           = doc_name
        row.parenttype       = "Invoice OCR"
        row.parentfield      = "table_seys"
        row.idx              = idx
        row.item_name        = item["item_name"]
        row.item_description = item["description"]
        row.quantity         = item["quantity"]
        row.rate             = item["rate"]
        row.sub_total        = item["amount"]
        row.tax_amount       = item["tax"]
        row.grand_total      = item["grand_total"]
        row.db_insert()

    frappe.db.commit()




# ─────────────────────────────────────────────────────────────────────────────
# Multi-Pattern Text Parser
# ─────────────────────────────────────────────────────────────────────────────
def parse_invoice_text(text):
    """Extract raw (unvalidated) fields from OCR text. Normalisation happens in normalizer.py."""
    data = {
        "raw_text": text,
        "party_name": "",
        "invoice_number": "",
        "invoice_date": "",
        "subtotal": 0.0,
        "tax_amount": 0.0,
        "grand_total": 0.0,
        "items": [],
    }

    # ── Invoice Number ────────────────────────────────────────────────
    for pat in [
        r'(?i)invoice\s*(?:no|num|number|#|id)[\s:\.]+([A-Z0-9][A-Z0-9/_-]{2,})',
        r'(?i)bill\s*(?:no|number)[\s:\.]+([A-Z0-9][A-Z0-9/_-]{2,})',
        r'(?i)receipt\s*(?:no|number)[\s:\.]+([A-Z0-9][A-Z0-9/_-]{2,})',
        r'(?i)invoice[\s:]+([A-Z0-9][A-Z0-9/_-]{4,})', # Fallback for "Invoice: 12345"
    ]:
        m = re.search(pat, text)
        if m:
            data["invoice_number"] = m.group(1).strip()
            break

    # ── Invoice Date ──────────────────────────────────────────────────
    # Most specific first: "Invoice date ........ 31 Jan 2026" (dotted separator, word month)
    for pat in [
        r'(?i)(?:date\s+of\s+issue|issue\s+date)[\s:\.]+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})',
        r'(?i)invoice\s*date[\s:\.]+([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4})',
        r'(?i)invoice\s*date[\s:\.]+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})',
        r'(?i)invoice\s*date[\s:\.]+(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4})',
        r'(?i)(?:bill|issued?)\s*date[\s:\.]+(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4})',
        r'(?i)(?:bill|issued?)\s*date[\s:]+([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4})',
        r'\b(\d{2}[/-]\d{2}[/-]\d{4})\b',
        r'\b(\d{4}-\d{2}-\d{2})\b',
    ]:
        m = re.search(pat, text)
        if m:
            data["invoice_date"] = m.group(1).strip()
            break

    # ── Totals — allow OCR noise before number (%, 2, J, etc. in place of ₹) ────
    # Pattern: label + spaces/colon + optional 1-2 char OCR noise + number
    _CURR = r'(?:[₹$£€]|[%@#&~J2])?\s*'
    _NUM  = r'([0-9][0-9,]*(?:\.[0-9]{1,2})?)'

    for pat in [
        rf'(?i)(?:grand\s*total|total\s*amount|amount\s*due|total\s*payable|amount\s*payable|total\s*due)[\s:]+{_CURR}{_NUM}',
        rf'(?i)(?:total\s+in\s+(?:inr|usd|gbp|eur))[\s:]+{_CURR}{_NUM}',
        rf'(?i)(?:net\s*total)[\s:]+{_CURR}{_NUM}',
    ]:
        # Use findall and take the LAST match (most likely the final total)
        matches = re.findall(pat, text)
        if matches:
            last = matches[-1]
            val, ok = normalize_float(last)
            if ok and val > 0:
                data["grand_total"] = val
                break

    for pat in [
        rf'(?i)(?:integrated\s*gst|cgst|sgst|igst|gst|vat|tax\s*amount)[\s()0-9%]+[\s:]+{_CURR}{_NUM}',
        rf'(?i)(?:tax)[\s:]+{_CURR}{_NUM}',
    ]:
        m = re.search(pat, text)
        if m:
            val, ok = normalize_float(m.group(1))
            if ok and val > 0:
                data["tax_amount"] = val
                break

    for pat in [
        rf'(?i)(?:sub\s*total|amount\s*before\s*tax|taxable\s*amount)\s+(?:in\s+\w+)?[\s:]+{_CURR}{_NUM}',
    ]:
        m = re.search(pat, text)
        if m:
            val, ok = normalize_float(m.group(1))
            if ok and val > 0:
                data["subtotal"] = val
                break

    # ── Party Name — look for "Bill to" / "Billed to" section ────────
    data["party_name"] = _extract_party_name(text)

    # ── Line items via regex table heuristic ──────────────────────────
    data["items"] = _extract_line_items(text)

    return data


# Skip lines that look like hashes, codes, or headers — not company names
_SKIP_LINE_RE = re.compile(
    r'^(?:IRN|GSTIN|PAN|CIN|TAN|UIN|HSN|SAC|GST|Page)\s*[:0-9a-f]',
    re.IGNORECASE
)

def _extract_party_name(text):
    """
    Extract the billed-to party name.
    Strategy:
      1. Look for 'Bill to' / 'Billed to' section → first non-empty line after it
      2. Fallback: first non-skippable, non-short line
    """
    # Strategy 1: Bill to / Billed to section
    m = re.search(r'(?i)bill(?:ed)?\s*to\s*[\n:]+\s*(.+)', text)
    if m:
        candidate = m.group(1).strip()
        if candidate and not _SKIP_LINE_RE.match(candidate):
            return candidate

    # Fallback: first line that is not a hash/code and is long enough
    for line in text.split("\n"):
        line = line.strip()
        if len(line) >= 5 and not _SKIP_LINE_RE.match(line):
            return line
    return ""



_ITEM_ROW = re.compile(
    r'^(.{3,60}?)\s+(\d+(?:\.\d+)?)\s+([0-9,]+(?:\.[0-9]{1,2})?)\s+([0-9,]+(?:\.[0-9]{1,2})?)$',
    re.MULTILINE
)

def _extract_line_items(text):
    items = []
    for m in _ITEM_ROW.finditer(text):
        qty,    _ = normalize_float(m.group(2))
        rate,   _ = normalize_float(m.group(3))
        amount, _ = normalize_float(m.group(4))
        if qty > 0 or rate > 0 or amount > 0:
            items.append({
                "description": m.group(1).strip(),
                "quantity":    qty,
                "rate":        rate,
                "amount":      amount,
                "tax":         0,
            })
    return items
