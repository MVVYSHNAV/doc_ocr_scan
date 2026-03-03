"""
OCR Processing Service
Optimized Single-Pass Extraction Pipeline
"""
import frappe
from pdf2image import convert_from_path
import pytesseract
from pytesseract import Output
import os
import json
import re
import cv2
import numpy as np
import time

from doc_ocr.services.normalizer import normalize_and_validate, normalize_float
from doc_ocr.services import docling_service

def process_invoice_ocr(doc_name):
    start_time = time.time()
    doc = frappe.get_doc("Invoice OCR", doc_name)
    doc.db_set("ocr_status", "Processing")

    try:
        file_path = _resolve_file_path(doc)
        
        # 1. Single OCR pass with bounding box extraction (No re-reads)
        images, ocr_data_pages, unified_text = run_ocr_once(file_path)

        # 2. Parse Headers & Totals from unified text
        raw = parse_invoice_headers_and_totals(unified_text)
        
        engine_used = 1
        
        # 3. Pass 1: Extract Line Items via Spatial Clustering & Native Regex
        raw["items"] = cluster_table_from_ocr(ocr_data_pages)
        if raw["items"]:
            engine_used = 1
            
        # 4. Pass 2: Docling (Only run if no table found by cluster/regex)
        if not raw["items"] or len(raw["items"]) < 1:
            docling_raw = run_docling_wrapper(file_path)
            if docling_raw and docling_raw.get("items"):
                raw["items"] = docling_raw["items"]
                for key in ("subtotal", "tax_amount", "grand_total"):
                    if not raw[key] and docling_raw.get("summary", {}).get(key):
                        raw[key] = docling_raw["summary"][key]
                engine_used = 2
                
        # 5. Pass 3: Currency Line Fallback
        if not raw["items"] or len(raw["items"]) < 1:
            raw["items"] = fallback_raw_text_currency(unified_text)
            if raw["items"]:
                engine_used = 3
                
        # 6. Pass 4: OpenCV Morphological Contour Fallback (USES CACHED OCR, NO RE-READ)
        if not raw["items"] or len(raw["items"]) < 1:
            raw["items"] = cv_fallback_cached(images, ocr_data_pages)
            if raw["items"]:
                engine_used = 4

        # 7. Normalise Data & Calculate Confidence
        data = normalize_and_validate(raw)
        
        # 8. Add Performance Profiling Meta
        processing_time_ms = int((time.time() - start_time) * 1000)
        data["extraction_pass"] = engine_used
        data["processing_time_ms"] = processing_time_ms
        
        engine_names = {
            1: "Tesseract Spatial Cluster",
            2: "Tesseract + Docling",
            3: "Text Currency Fallback",
            4: "CV Contour Fallback"
        }
        data["engine"] = engine_names.get(engine_used, "Unknown")

        frappe.logger("invoice_ocr").info(f"[{doc_name}] Pass: {engine_used}, Time: {processing_time_ms}ms, Items: {len(data['items'])}")

        _persist(doc_name, data, data["engine"])

    except Exception as e:
        frappe.db.rollback()
        frappe.db.set_value("Invoice OCR", doc_name, {
            "ocr_status": "Failed",
            "extracted_data": json.dumps({"error": str(e)}, indent=2),
        })
        frappe.db.commit()
        frappe.log_error(f"OCR Processing Failed for {doc_name}:\n{frappe.get_traceback()}", "Invoice OCR")


# ─────────────────────────────────────────────────────────────────────────────
# Core Extraction Functions
# ─────────────────────────────────────────────────────────────────────────────

def run_ocr_once(file_path):
    """Loads image once, converts to CV format, and runs image_to_data."""
    images = []
    if file_path.lower().endswith(".pdf"):
        # Returns PIL images
        pil_images = convert_from_path(file_path, dpi=200)
        for img in pil_images:
            images.append(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
    else:
        images = [cv2.imread(file_path)]

    ocr_data_pages = []
    unified_text_lines = []
    
    for img in images:
        # Convert BGR to RGB for tesseract (which expects RGB)
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # Output.DICT gives words and bounding boxes
        data = pytesseract.image_to_data(rgb_img, config="--psm 6", output_type=Output.DICT)
        ocr_data_pages.append(data)
        
        # Reconstruct text block-by-block for regex matching
        page_text = build_text_from_data(data)
        unified_text_lines.append(page_text)
        
    unified_text = "\n".join(unified_text_lines)
    return images, ocr_data_pages, unified_text

def build_text_from_data(data):
    """Reconstructs single string document from spatial data dict."""
    lines = []
    current_line = []
    last_line_id = None
    
    for i in range(len(data['text'])):
        word = data['text'][i].strip()
        if not word: continue
        line_id = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        
        if line_id != last_line_id:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            last_line_id = line_id
        else:
            current_line.append(word)
            
    if current_line:
        lines.append(" ".join(current_line))
        
    return "\n".join(lines)

def parse_invoice_headers_and_totals(text):
    """Extract headers and totals using regex over reconstructed text."""
    data = {
        "raw_text": text,
        "party_name": "",
        "invoice_number": "",
        "invoice_date": "",
        "subtotal": 0.0,
        "tax_amount": 0.0,
        "grand_total": 0.0,
        "items": []
    }

    # Invoice Number
    for pat in [
        r'(?i)invoice\s*(?:no|num|number|#|id)[\s:\.]+([A-Z0-9][A-Z0-9/_-]{2,})',
        r'(?i)bill\s*(?:no|number)[\s:\.]+([A-Z0-9][A-Z0-9/_-]{2,})',
        r'(?i)receipt\s*(?:no|number)[\s:\.]+([A-Z0-9][A-Z0-9/_-]{2,})',
        r'(?i)invoice[\s:]+([A-Z0-9][A-Z0-9/_-]{4,})',
    ]:
        m = re.search(pat, text)
        if m:
            data["invoice_number"] = m.group(1).strip()
            break

    # Invoice Date
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

    # Totals
    _CURR = r'(?:[₹$£€]|[%@#&~J2])?\s*'
    _NUM  = r'([0-9][0-9,]*(?:\.[0-9]{1,2})?)'

    for pat in [
        rf'(?i)(?:grand\s*total|total\s*amount|amount\s*due|total\s*payable|amount\s*payable|total\s*due)[\s:]+{_CURR}{_NUM}',
        rf'(?i)(?:total\s+in\s+(?:inr|usd|gbp|eur))[\s:]+{_CURR}{_NUM}',
        rf'(?i)(?:net\s*total)[\s:]+{_CURR}{_NUM}',
    ]:
        matches = re.findall(pat, text)
        if matches:
            val, ok = normalize_float(matches[-1])
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

    # Party Name
    data["party_name"] = _extract_party_name(text)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Clustering & Fallback Handlers
# ─────────────────────────────────────────────────────────────────────────────

def cluster_table_from_ocr(ocr_data_pages):
    """Clusters boxes temporally & spatially on Y-axis to build perfect strings for regex."""
    items = []
    
    for data in ocr_data_pages:
        words = []
        for i in range(len(data['text'])):
            text = data['text'][i].strip()
            if text:
                words.append({
                    'text': text,
                    'left': data['left'][i],
                    'top': data['top'][i],
                    'cy': data['top'][i] + (data['height'][i] // 2)
                })
        
        words.sort(key=lambda w: (w['cy'], w['left']))
        
        rows = []
        current_row = []
        last_cy = None
        
        for w in words:
            if last_cy is None or abs(w['cy'] - last_cy) < 15:
                current_row.append(w)
                # Moving average of cluster threshold
                last_cy = sum(x['cy'] for x in current_row) / len(current_row)
            else:
                current_row.sort(key=lambda x: x['left'])
                rows.append(current_row)
                current_row = [w]
                last_cy = w['cy']
                
        if current_row:
            current_row.sort(key=lambda x: x['left'])
            rows.append(current_row)
            
        # Inspect rows for matches
        for row in rows:
            line_str = " ".join([w['text'] for w in row])
            m = re.search(r'^(.{3,60}?)\s+(\d+(?:\.\d+)?)\s+([0-9,]+(?:\.[0-9]{1,2})?)\s+([0-9,]+(?:\.[0-9]{1,2})?)$', line_str)
            if m:
                qty, _ = normalize_float(m.group(2))
                rate, _ = normalize_float(m.group(3))
                amount, _ = normalize_float(m.group(4))
                if qty > 0 or rate > 0 or amount > 0:
                    items.append({
                        "description": m.group(1).strip()[:140],
                        "quantity": qty,
                        "rate": rate,
                        "amount": amount,
                        "tax": 0
                    })
    return items

def run_docling_wrapper(file_path):
    try:
        result = docling_service.extract_with_docling(file_path)
        return result
    except Exception as e:
        frappe.log_error(f"Docling failed: {e}", "Invoice OCR")
        return None

def fallback_raw_text_currency(text):
    fallback_items = []
    for line in text.split("\n"):
        m = re.search(r'^([A-Za-z0-9_( \)-]+(?:(?:20\d{2}|[0-3]\d-[0-1]\d)[ \d:]+)*)\s+(?:[₹$£€])?([\d.,]+)$', line.strip())
        if m:
            desc_raw = m.group(1).strip()
            amt_str = m.group(2)
            amt, _ = normalize_float(amt_str)
            skip_words = ["total", "due", "payable", "summary", "tax", "gst", "page", "invoice"]
            if any(w in desc_raw.lower() for w in skip_words):
                continue
            if amt > 0 and len(desc_raw) > 3:
                fallback_items.append({
                    "description": desc_raw[:140],
                    "quantity": 1,
                    "rate": amt,
                    "amount": amt,
                    "tax": 0
                })
    return fallback_items

def cv_fallback_cached(images, ocr_data_pages):
    """Uses OpenCV merely to find structural contours, then layers cached OCR words inside."""
    cv_items = []
    
    for i, img in enumerate(images):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        thresh = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 10))
        dilated = cv2.dilate(thresh, kernel, iterations=1)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        blocks = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w > 30 and h > 15:
                blocks.append({"rect": [x, y, w, h]})
                
        blocks.sort(key=lambda b: (b['rect'][1] // 20, b['rect'][0]))
        data = ocr_data_pages[i]
        
        for block in blocks:
            bx, by, bw, bh = block["rect"]
            words_in_block = []
            
            for j in range(len(data['text'])):
                text = data['text'][j].strip()
                if not text: continue
                cx, cy = data['left'][j] + data['width'][j]//2, data['top'][j] + data['height'][j]//2
                
                if bx <= cx <= bx+bw and by <= cy <= by+bh:
                    words_in_block.append((data['left'][j], data['top'][j], text))
                    
            if not words_in_block:
                continue
                
            words_in_block.sort(key=lambda w: (w[1] // 10, w[0]))
            block_text = " ".join([w[2] for w in words_in_block])
            
            m = re.search(r'(?:[₹$£€])?([\d.,]+)$', block_text.strip())
            if m:
                amt, _ = normalize_float(m.group(1))
                skip_words = ["total", "due", "payable", "summary", "tax", "gst"]
                if amt > 0 and len(block_text) > 3 and not any(w in block_text.lower() for w in skip_words):
                    cv_items.append({
                        "description": block_text.replace('\n', ' ')[:140],
                        "quantity": 1,
                        "rate": amt,
                        "amount": amt,
                        "tax": 0
                    })
    return cv_items

# ─────────────────────────────────────────────────────────────────────────────
# Helper Modules
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_file_path(doc):
    if not doc.file:
        frappe.throw("No file attached")
    if doc.file.startswith(("/files/", "/private/files/")):
        file_doc = frappe.get_last_doc("File", {"file_url": doc.file})
        return file_doc.get_full_path()
    path = frappe.get_site_path("public", doc.file.lstrip("/"))
    if not os.path.exists(path):
        frappe.throw(f"File not found: {path}")
    return path

_SKIP_LINE_RE = re.compile(r'^(?:IRN|GSTIN|PAN|CIN|TAN|UIN|HSN|SAC|GST|Page)\s*[:0-9a-f]', re.IGNORECASE)
def _extract_party_name(text):
    m = re.search(r'(?i)bill(?:ed)?\s*to\s*[\n:]+\s*(.+)', text)
    if m:
        candidate = m.group(1).strip()
        if candidate and not _SKIP_LINE_RE.match(candidate):
            return candidate
    for line in text.split("\n"):
        line = line.strip()
        if len(line) >= 5 and not _SKIP_LINE_RE.match(line):
            return line
    return ""

def _persist(doc_name, data, engine):
    items_list      = data.get("items", [])
    parent_tax      = data.get("tax_amount", 0) or 0
    parent_subtotal = data.get("subtotal",   0) or 0
    parent_grand    = data.get("grand_total", 0) or 0
    total_item_amt  = sum(i.get("amount", 0) for i in items_list) or parent_subtotal or 1

    enriched_items = []
    items_list = [item for item in items_list if bool(item.get("description") or item.get("item_name"))]
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

    data["items"] = enriched_items   

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

