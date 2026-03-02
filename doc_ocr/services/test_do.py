import frappe
import json
from doc_ocr.services.ocr_service import process_invoice_ocr

def run():
    docs = frappe.get_all("Invoice OCR", filters={"file": ("like", "%DigitalOcean%")}, limit=1)
    if docs:
        doc_name = docs[0].name
        print(f"Testing on document: {doc_name}")
        process_invoice_ocr(doc_name=doc_name)
        
        doc = frappe.get_doc("Invoice OCR", doc_name)
        print("\n--- Extracted JSON ---")
        print(doc.extracted_data)
        
        print("\n--- Parsed Party Name ---")
        try:
            print(json.loads(doc.extracted_data).get('party_name'))
        except:
            pass
    else:
        print("Could not find the Invoice OCR record for the DigitalOcean file.")
