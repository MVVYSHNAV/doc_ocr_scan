# Copyright (c) 2026, Tridz and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class InvoiceOCR(Document):
    def before_submit(self):
        """Validate that the extracted party exists before allowing submission."""
        if not self.party_type or not self.name1:
            frappe.throw("Party Type and Party Name are required before submitting.")

        # Check if party name exists as the selected type
        doctype = self.party_type  # Customer or Supplier
        exists = frappe.db.exists(doctype, {"name": self.name1})

        if not exists:
            msg = f"The {doctype} <b>'{self.name1}'</b> does not exist in the system.<br><br>"
            msg += f"Please create this {doctype} first, or edit the Party Name field to match an existing {doctype}."
            frappe.throw(msg, title=f"Unknown {doctype}")

    def after_save(self):
        """Auto-queue OCR when a new file is uploaded and OCR hasn't run yet."""
        trigger_statuses = {"", None, "Pending", "Failed"}
        if self.file and self.ocr_status in trigger_statuses and self.docstatus == 0:
            # enqueue_after_commit ensures the current transaction is committed first
            # so the background worker can read the saved file reference
            frappe.enqueue(
                "doc_ocr.services.ocr_service.process_invoice_ocr",
                doc_name=self.name,
                queue="default",
                enqueue_after_commit=True,
            )
            frappe.msgprint(frappe._("OCR job queued automatically."), alert=True, indicator="blue")
