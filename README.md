### Doc Ocr

OCR-based document extraction and document creation engine

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app doc_ocr
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/doc_ocr
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit
# Invoice OCR App

OCR-powered invoice extraction and ERPNext invoice creation from scanned PDFs and images.

## Features

- **Hybrid Extraction Engine**: Uses `Tesseract` for raw text and high-level header extraction, and IBM's `Docling` specifically for state-of-the-art tabular line item extraction.
- **Automatic Parsing & Normalization**: Automatically extracts Party Name, Invoice Number, Date, Subtotal, Tax, and Grand Total. Normalizes currencies and dates.
- **Smart Line Item Mapping**: Maps both Item Name (e.g., "Workspace Subscription") and Item Description (e.g., "Usage") perfectly into separate line fields.
- **Fast UI Polling**: The frontend automatically polls the background OCR job and populates the screen instantly upon completion without requiring a page reload.
- **Pre-flight Validation**: Blocks submission if the extracted Party Name doesn't actually exist as a Customer/Supplier in your ERPNext database, ensuring high data cleanliness.
- **Create Invoices Natively**: One-click generation of Sales or Purchase invoices, automatically falling back to your Company's default Income/Expense accounts to ensure smooth insertion.

## Workflow

1. Upload a scanned or digital PDF/Image to an **Invoice OCR** document and click Save (the OCR extraction triggers automatically in the background).
2. The page will auto-update as soon as the text, totals, and line items are populated.
3. Review the parsed lines and details in the document. You can also click **View Raw JSON** to see the raw extracted structure and OCR Confidence Score.
4. Set the **Party Type** (Customer/Supplier). Ensure the extracted **Party Name** exists in your system exactly as written (or edit it to match). 
5. **Submit** the record.
6. Click **Create Invoice** to push the items natively to standard ERPNext modules. If successful, you will be automatically redirected to the newly created invoice document.
