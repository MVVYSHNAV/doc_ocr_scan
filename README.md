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

# Invoice OCR for Frappe/ERPNext

A highly optimized OCR-powered invoice extraction and document creation engine for standard Frappe / ERPNext environments.

## Core Features

- **Hybrid AI Extraction Engine:** Uses `Tesseract` for text parsing, AI-powered IBM `Docling` specifically for tabular line-item understanding, and custom `OpenCV` morphological mapping as an ultimate fallback for chaotic unstructured documents.
- **Smart Line Item Mapping:** Extracts Item Name, Description, Quantity, Rate, Subtotal, and Tax perfectly into distinct line fields.
- **Auto-Detection for Sales/Purchase:** Automatically classifies uploaded documents into **Sales Invoices** or **Purchase Invoices** based on the configured Host Company in ERPNext.
- **Auto-Matches Customers & Suppliers:** Queries your Frappe database dynamically to auto-link the detected party with a live Customer or Supplier.
- **Instant Pre-flight Validation:** Automatically generates an OCR Confidence Score. Warns if totals do not reconcile, ensuring high data cleanliness before submission.
- **Create Invoices Natively:** One-click generation of native ERPNext Sales or Purchase invoices, automatically mapping items to default income/expense accounts.

---

## 🚀 Easy Installation Guide

Follow these steps to seamlessly pull the OCR Engine into your active Frappe/ERPNext site.

### 1. Install System Dependencies

Because the App utilizes `Tesseract` and `Computer Vision`, you must install the native C++ libraries required by your Linux distribution for PDF processing and image parsing:
```bash
sudo apt-get update
sudo apt-get install tesseract-ocr
sudo apt-get install poppler-utils
sudo apt-get install libgl1
```

### 2. Fetch the Application

Pull the OCR App into your `frappe-bench`:
```bash
cd /path/to/your/frappe-bench
bench get-app https://github.com/MVVYSHNAV/doc_ocr_scan.git --branch develop
```

### 3. Install Python Dependencies

The backend engine requires several advanced packages not typically packaged with Frappe base (like IBM Docling & PyTesseract).
Install them directly to the active bench virtual environment using `pip`:
```bash
./env/bin/pip install pytesseract pdf2image opencv-python numpy docling rapidocr-onnxruntime
```

### 4. Install App to Your Site

Execute the installation onto your active site database:
```bash
bench --site <your-site-name> install-app doc_ocr
bench --site <your-site-name> clear-cache
bench restart
```

---

## 🛠️ Typical Usage Workflow

1. Open **Invoice OCR** inside your ERPNext deployment and click **Add Invoice OCR**.
2. **Upload** a scanned or digital PDF/Image of an invoice and click **Save**.
3. **Wait briefly.** The backend will automatically farm the extraction out to a background Job. *You do not need to refresh the page; the frontend UI will auto-populate and notify you instantly as soon as the results are returned.*
4. Review the parsed data, line items, and Confidence Score. Notice the **Party Type** and **Invoice Type** will be auto-set, and your **Party Name** mapped. If you want, click **View Raw JSON** to debug the ML output.
5. **Submit** the record.
6. Click **Create Invoice** to natively push the extracted data into a standard ERPNext modules system object.
