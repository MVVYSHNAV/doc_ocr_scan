"""
Docling Service — document structure extraction layer.

Runs AFTER Tesseract to improve table detection and line-item extraction.
Falls back silently to None on any failure so the Tesseract pipeline is never broken.
"""
import frappe


def extract_with_docling(file_path):
    """
    Use Docling's DocumentConverter to extract structured data from a PDF or image.

    Returns a dict with keys:
        items   - list of {"description", "quantity", "rate", "amount", "tax"}
        summary - {"subtotal", "tax_amount", "grand_total"}
        raw     - raw Docling markdown export (for debugging)

    Returns None on any error.
    """
    try:
        from docling.document_converter import DocumentConverter
        from docling.datamodel.base_models import InputFormat
        from pathlib import Path
        import re

        path = Path(file_path)
        if not path.exists():
            return None

        # Docling supports PDF natively; for images wrap in a lightweight path
        converter = DocumentConverter()
        result = converter.convert(str(path))
        doc = result.document

        # Export to markdown for text-based parsing
        md_text = doc.export_to_markdown()

        # Extract tables as structured dicts
        items = _extract_items_from_tables(doc)

        # Extract summary totals from markdown text
        summary = _extract_summary_from_text(md_text)

        return {
            "items": items,
            "summary": summary,
            "raw": md_text,
        }

    except Exception as e:
        frappe.log_error(f"Docling extraction failed: {str(e)}", "Invoice OCR - Docling")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Table Extraction
# ─────────────────────────────────────────────────────────────────────────────
def _extract_items_from_tables(doc):
    """
    Iterate over all Docling tables and try to map columns to invoice fields.
    Returns list of item dicts.
    """
    items = []
    try:
        for table in doc.tables:
            rows = _table_to_rows(table)
            if not rows or len(rows) < 2:
                continue

            header = [str(h).strip().lower() for h in rows[0]]
            col_map = _map_columns(header)

            if not col_map:
                continue  # not an invoice table

            for row in rows[1:]:
                item = _row_to_item(row, col_map)
                if item:
                    items.append(item)
    except Exception:
        pass
    return items


def _table_to_rows(table):
    """Convert Docling TableItem to list of lists."""
    rows = []
    try:
        grid = table.data.grid
        for row in grid:
            rows.append([cell.text if cell else "" for cell in row])
    except Exception:
        pass
    return rows


# Column label synonyms for invoice fields
_COL_SYNONYMS = {
    "item_name":   ["subscription", "item", "product", "service", "name"],
    "description": ["description", "desc", "particulars"],
    "quantity":    ["quantity", "qty", "units", "nos", "count"],
    "rate":        ["rate", "unit price", "price", "unit cost", "unit rate"],
    "amount":      ["amount", "total", "line total", "net amount", "value"],
    "tax":         ["tax", "gst", "vat", "cgst", "sgst", "igst", "tax%", "tax rate"],
}

def _map_columns(header):
    """Return dict mapping field_name → column_index, or empty if no useful match."""
    mapping = {}
    for field, synonyms in _COL_SYNONYMS.items():
        for idx, h in enumerate(header):
            if any(syn in h for syn in synonyms):
                mapping[field] = idx
                break
    # Need at least description OR amount OR item_name to be a usable table
    if "item_name" not in mapping and "description" not in mapping and "amount" not in mapping:
        return {}
    return mapping


def _row_to_item(row, col_map):
    """Convert a table row + column mapping to an item dict."""
    from doc_ocr.services.normalizer import normalize_float

    def get(field):
        idx = col_map.get(field)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    item_name = get("item_name")
    desc      = get("description")
    qty_raw   = get("quantity")
    rate_raw  = get("rate")
    amount_raw = get("amount")
    tax_raw   = get("tax")

    qty, _ = normalize_float(qty_raw or "1")
    rate, _ = normalize_float(rate_raw)
    amount, _ = normalize_float(amount_raw)
    tax, _ = normalize_float(tax_raw)

    # Need at least a name or an amount to be a real line item
    if not item_name and not desc and amount == 0:
        return None

    # If both name columns are blank, this is a summary row with no label — skip it
    if not item_name and not desc:
        return None

    # Use item_name as primary name; fall back to desc
    primary_name = item_name or desc or "OCR Item"
    detail       = desc or item_name or primary_name

    # Skip summary/footer rows
    _SUMMARY_KEYWORDS = {
        "subtotal", "sub total", "total", "grand total", "tax", "gst",
        "igst", "cgst", "sgst", "vat", "amount due", "balance due",
        "amount payable", "net total",
    }
    if any(kw in primary_name.lower() for kw in _SUMMARY_KEYWORDS) and qty <= 1:
        return None

    # Infer amount if missing
    if amount == 0 and qty > 0 and rate > 0:
        amount = round(qty * rate, 2)

    return {
        "item_name":   primary_name,
        "description": detail,
        "quantity":    qty or 1,
        "rate":        rate,
        "amount":      amount,
        "tax":         tax,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Summary Field Extraction from Markdown
# ─────────────────────────────────────────────────────────────────────────────
def _extract_summary_from_text(text):
    """Pull subtotal / tax / grand total from markdown text."""
    import re
    from doc_ocr.services.normalizer import normalize_float

    def first_match(patterns):
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                val, ok = normalize_float(m.group(1))
                if ok and val > 0:
                    return val
        return 0.0

    grand_total = first_match([
        r'(?:grand\s*total|total\s*amount|amount\s*due|amount\s*payable|total\s*payable)[\s:₹$£€]*([0-9,]+(?:\.[0-9]{1,2})?)',
        r'(?:net\s*total|total)[\s:₹$£€]*([0-9,]+(?:\.[0-9]{1,2})?)',
    ])
    tax_amount = first_match([
        r'(?:cgst|sgst|igst|gst|vat|tax(?:\s*amount)?)[\s:₹$£€]*([0-9,]+(?:\.[0-9]{1,2})?)',
    ])
    subtotal = first_match([
        r'(?:sub\s*total|taxable\s*amount|amount\s*before\s*tax)[\s:₹$£€]*([0-9,]+(?:\.[0-9]{1,2})?)',
    ])
    if subtotal == 0 and grand_total > 0 and tax_amount > 0:
        subtotal = round(grand_total - tax_amount, 2)

    return {
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "grand_total": grand_total,
    }
