import frappe
import json

@frappe.whitelist()
def run_ocr(doc_name):
    frappe.enqueue('doc_ocr.services.ocr_service.process_invoice_ocr', doc_name=doc_name, queue='default')
    return {"status": "queued"}


@frappe.whitelist()
def create_invoice(doc_name):
    doc = frappe.get_doc("Invoice OCR", doc_name)

    # ── Party / Type validation ────────────────────────────────────────
    if not doc.invoice_type:
        frappe.throw("Please select an Invoice Type (Sales Invoice or Purchase Invoice)")
    if not doc.party_type or not doc.name1:
        frappe.throw("Please select a Party Type and a Party Name before creating an invoice")

    if doc.invoice_type == "Sales Invoice" and doc.party_type != "Customer":
        frappe.throw("Sales Invoice requires Party Type to be 'Customer'")
    if doc.invoice_type == "Purchase Invoice" and doc.party_type != "Supplier":
        frappe.throw("Purchase Invoice requires Party Type to be 'Supplier'")

    # ── Duplicate prevention ──────────────────────────────────────────
    extracted_data = {}
    if doc.extracted_data:
        try:
            extracted_data = json.loads(doc.extracted_data)
        except Exception:
            pass

    if extracted_data.get("created_invoice_ref"):
        frappe.throw(
            f"An invoice has already been created for this document: "
            f"{extracted_data.get('created_invoice_ref')}"
        )

    # ── Pre-flight safety checks ──────────────────────────────────────
    _preflight_check(doc, extracted_data)

    # ── Build the ERPNext invoice ─────────────────────────────────────
    inv = frappe.new_doc(doc.invoice_type)

    if doc.invoice_type == "Sales Invoice":
        inv.customer = doc.name1
    else:
        inv.supplier = doc.name1

    if doc.invoice_date:
        inv.posting_date = doc.invoice_date
        if hasattr(inv, "bill_date"):
            inv.bill_date = doc.invoice_date

    if doc.invoice_number and doc.invoice_type == "Purchase Invoice":
        inv.bill_no = doc.invoice_number

    # ── Copy line items ────────────────────────────────────────────────
    items = doc.get("table_seys") or []
    if not items:
        # Fallback: create a single summary item from grand total
        inv.append("items", {
            "item_name": "Invoice Amount",
            "description": f"Invoice {doc.invoice_number or doc.name}",
            "qty": 1,
            "rate": doc.grand_total or 0,
        })
    else:
        for item in items:
            inv.append("items", {
                "item_name": item.item_name or item.item_description or "OCR Item",
                "description": item.item_description or item.item_name or "OCR Item",
                "qty": item.quantity or 1,
                "rate": item.rate or item.sub_total or 0,
            })

    # ── Insert and lock ────────────────────────────────────────────────
    try:
        inv.insert(ignore_permissions=True)

        extracted_data["created_invoice_ref"] = inv.name
        doc.db_set("extracted_data", json.dumps(extracted_data, indent=2))

        return {
            "status": "success",
            "invoice_type": doc.invoice_type,
            "invoice_name": inv.name
        }
    except Exception:
        frappe.log_error(title="Invoice OCR Creation Error", message=frappe.get_traceback())
        frappe.throw(f"Error creating invoice: {frappe.get_traceback()}")


def _preflight_check(doc, extracted_data):
    """Block invoice creation if critical data issues exist."""
    errors = []
    warnings = extracted_data.get("warnings", [])
    confidence = extracted_data.get("confidence", "")

    # Confidence check — Low means OCR had significant trouble
    if confidence == "Low":
        errors.append(
            "OCR confidence is Low — please review extracted data in 'View Raw JSON' "
            "and correct any errors before creating an invoice."
        )

    if errors:
        msg = "<b>Cannot create invoice due to the following issues:</b><ul>"
        for err in errors:
            msg += f"<li>{err}</li>"
        msg += "</ul>"
        if warnings:
            msg += "<b>Warnings present:</b><ul>"
            for w in warnings[:5]:
                msg += f"<li>{w}</li>"
            msg += "</ul>"
        frappe.throw(msg, frappe.ValidationError)


def validate_ocr_before_submit(doc, method):
    if doc.ocr_status != "Extracted":
        frappe.throw(
            "You cannot submit this document until the OCR status is 'Extracted'. "
            "Please run OCR first."
        )
