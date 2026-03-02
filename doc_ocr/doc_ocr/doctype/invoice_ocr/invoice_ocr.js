frappe.ui.form.on("Invoice OCR", {
    refresh: function (frm) {
        // Auto-populate immediately if OCR already done and data available
        if (frm.doc.ocr_status === "Extracted" && frm.doc.extracted_data && !frm._ocr_auto_populated) {
            frm._ocr_auto_populated = true;
            setTimeout(function () { frm.events._populate_from_extracted(frm); }, 200);
        }

        // ── Run OCR button ──────────────────────────────────────────────
        if (frm.doc.file
            && (!frm.doc.ocr_status || ["Pending", "Failed", ""].includes(frm.doc.ocr_status))
            && !frm.is_new()) {
            frm.add_custom_button(__("Run OCR"), function () {
                frappe.call({
                    method: "doc_ocr.api.run_ocr",
                    args: { doc_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Queuing OCR Job..."),
                    callback: function (r) {
                        if (!r.exc) {
                            frappe.show_alert({ message: __("OCR queued — processing in background…"), indicator: "blue" });
                            frm.set_value("ocr_status", "Processing");
                            frm.events._poll_ocr_status(frm);
                        }
                    }
                });
            }).addClass("btn-primary");
        }

        // Spinner while processing
        if (frm.doc.ocr_status === "Processing" && !frm.is_new()) {
            frm.dashboard.add_comment(__("OCR is running in the background. Fields will populate automatically."), "blue", true);
            frm.events._poll_ocr_status(frm);
        }

        // ── View Raw JSON ───────────────────────────────────────────────
        if (frm.doc.extracted_data) {
            frm.add_custom_button(__("View Raw JSON"), function () {
                let d = new frappe.ui.Dialog({
                    title: "Raw OCR JSON",
                    fields: [{ label: "Extracted Data", fieldname: "raw_json", fieldtype: "Code", read_only: 1, default: frm.doc.extracted_data }],
                    size: "large",
                    primary_action_label: "Close",
                    primary_action: function () { d.hide(); }
                });
                d.show();
            });

            frm.add_custom_button(__("Populate Fields from OCR"), function () {
                frm._ocr_auto_populated = false; // allow re-populate
                frm.events._populate_from_extracted(frm);
            });
        }

        // ── Create ERPNext Invoice ──────────────────────────────────────
        if (frm.doc.ocr_status === "Extracted" && frm.doc.docstatus === 1) {
            frm.toggle_display("create_invoice", true);
            frm.add_custom_button(__("Create ERPNext Invoice"), function () {
                frm.events.create_invoice(frm);
            }).addClass("btn-primary");
        } else {
            frm.toggle_display("create_invoice", false);
        }
    },

    // ── Poll backend every 2s; populate directly when done (no reload) ──
    _poll_ocr_status: function (frm) {
        if (frm._ocr_poll_timer) clearInterval(frm._ocr_poll_timer);
        let attempts = 0;
        frm._ocr_poll_timer = setInterval(function () {
            attempts++;
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Invoice OCR",
                    name: frm.doc.name,
                    fieldname: ["ocr_status", "extracted_data", "invoice_number",
                        "invoice_date", "name1", "tax_amount", "grand_total",
                        "ocr_engine"]
                },
                callback: function (r) {
                    if (!r.message) return;
                    let status = r.message.ocr_status;

                    if (status === "Extracted") {
                        clearInterval(frm._ocr_poll_timer);
                        frappe.show_alert({ message: __("OCR complete! Populating fields…"), indicator: "green" });

                        // Sync fetched values straight into the doc (no full reload)
                        ["invoice_number", "invoice_date", "name1",
                            "tax_amount", "grand_total", "ocr_engine",
                            "extracted_data"].forEach(function (f) {
                                if (r.message[f] !== undefined) frm.doc[f] = r.message[f];
                            });
                        frm.doc.ocr_status = "Extracted";
                        frm.refresh_fields();

                        // Populate from extracted_data without full page reload
                        frm._ocr_auto_populated = true;
                        frm.events._populate_from_extracted(frm);

                        // Silently sync child table state in background
                        frm.reload_doc();

                    } else if (status === "Failed") {
                        clearInterval(frm._ocr_poll_timer);
                        frappe.show_alert({ message: __("OCR failed. Check Error Log."), indicator: "red" });
                        frm.doc.ocr_status = "Failed";
                        frm.refresh_field("ocr_status");

                    } else if (attempts > 90) {
                        clearInterval(frm._ocr_poll_timer);
                        frappe.show_alert({ message: __("OCR taking longer than expected — refresh manually."), indicator: "orange" });
                    }
                }
            });
        }, 2000); // poll every 2 seconds
    },

    // ── Populate form fields from extracted_data JSON ───────────────────
    _populate_from_extracted: function (frm) {
        if (!frm.doc.extracted_data) {
            frappe.show_alert({ message: __("No extracted data found. Run OCR first."), indicator: "orange" });
            return;
        }
        let data;
        try {
            data = JSON.parse(frm.doc.extracted_data);
        } catch (e) {
            frappe.msgprint({ title: "Error", indicator: "red", message: "Could not parse extracted_data: " + e.message });
            return;
        }

        let warnings = data.warnings || [];
        let confidence = data.confidence || "N/A";

        // Map header fields
        if (data.invoice_number) frm.set_value("invoice_number", data.invoice_number);
        if (data.invoice_date) frm.set_value("invoice_date", data.invoice_date);
        if (data.party_name) frm.set_value("name1", data.party_name);
        if (data.tax_amount) frm.set_value("tax_amount", data.tax_amount);
        if (data.grand_total) frm.set_value("grand_total", data.grand_total);

        // Repopulate child table
        frm.clear_table("table_seys");
        (data.items || []).forEach(function (item) {
            let row = frm.add_child("table_seys");
            let name = item.item_name || item.description || "OCR Item";
            frappe.model.set_value(row.doctype, row.name, "item_name", name);
            frappe.model.set_value(row.doctype, row.name, "item_description", name);
            frappe.model.set_value(row.doctype, row.name, "quantity", item.quantity || 1);
            frappe.model.set_value(row.doctype, row.name, "rate", item.rate || 0);
            frappe.model.set_value(row.doctype, row.name, "sub_total", item.amount || 0);
            frappe.model.set_value(row.doctype, row.name, "tax_amount", item.tax || 0);
            frappe.model.set_value(row.doctype, row.name, "grand_total", (item.amount || 0) + (item.tax || 0));
        });
        frm.refresh_field("table_seys");

        // Summary banner
        let indicator = confidence === "High" ? "green" : confidence === "Medium" ? "blue" : "orange";
        let msg = `<b>Confidence: ${confidence}</b>`;
        if (warnings.length) {
            msg += `<br><br><b>Warnings (${warnings.length}):</b><ul><li>` + warnings.slice(0, 5).join("</li><li>") + "</li></ul>";
        }
        frappe.msgprint({ title: "OCR Data Populated", indicator: indicator, message: msg });
    },

    before_save: function (frm) {
        if (frappe._from_link && frappe._from_link.df && frappe._from_link.df.options === "party_type") {
            frappe._from_link = null;
        }
    },

    before_submit: function (frm) {
        if (frm.doc.ocr_status !== "Extracted") {
            frappe.msgprint({ title: "Validation Error", indicator: "red", message: "You cannot submit until OCR status is Extracted." });
            frappe.validated = false;
        }
    },

    create_invoice: function (frm) {
        if (frm.doc.ocr_status !== "Extracted") {
            frappe.msgprint({ title: "Validation Error", indicator: "red", message: "OCR must be extracted before creating an invoice." });
            return;
        }
        frappe.call({
            method: "doc_ocr.api.create_invoice",
            args: { doc_name: frm.doc.name },
            freeze: true,
            freeze_message: __("Creating Invoice…"),
            callback: function (r) {
                if (!r.exc && r.message && r.message.status === "success") {
                    let msg = __('Created {0}: <a href="/app/{1}/{2}"><b>{2}</b></a>', [
                        r.message.invoice_type,
                        frappe.router.slug(r.message.invoice_type),
                        r.message.invoice_name
                    ]);
                    frappe.msgprint({ title: "Invoice Created", indicator: "green", message: msg });
                    frm.reload_doc();
                }
            }
        });
    }
});
