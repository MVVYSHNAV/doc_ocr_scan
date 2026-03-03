import frappe
from doc_ocr.services.ocr_service import process_invoice_ocr
import json

def run():
    frappe.init(site="doc_ocr.local")
    frappe.connect()

    docs = frappe.get_all("Invoice OCR", filters={"file": ("like", "%DigitalOcean%")}, limit=1)
    if docs:
        doc_name = docs[0].name
        print(f"Testing performance on document: {doc_name}")
        process_invoice_ocr(doc_name=doc_name)
        
        doc = frappe.get_doc("Invoice OCR", doc_name)
        print("\n=== FINAL EXTRACTED JSON ===")
        print(doc.extracted_data)
    else:
        print("No test document found. Please test from UI.")
