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

- **OCR extraction** from scanned PDFs and images using Tesseract
- **Automatic parsing** of invoice header and line items
- **Support for both Sales and Purchase invoices**
- **Seamless integration** with ERPNext Customers, Suppliers, and Invoices
- **Background OCR processing** with clear status tracking
- **Manual review before invoice creation (audit-safe)**

## Workflow

1. Upload a scanned or digital PDF/Image to an **Invoice OCR** document and click Save.
2. Click **Run OCR** to trigger background extraction.
3. Review the parsed lines and details in the document.
4. Set the **Invoice Type** and **Party**, then **Submit** the record.
5. Click **Create Invoice** to push the items natively to standard ERPNext modules.
