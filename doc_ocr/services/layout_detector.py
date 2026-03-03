import cv2
import numpy as np
import pytesseract
from pdf2image import convert_from_path
import os

class LayoutDetector:
    """
    A full Computer Vision-based Layout Detection Algorithm using OpenCV.
    This bypasses generalized OCR by first identifying the structural layout 
    (Blocks, Paragraphs, Table Cells) using morphological operations, and then 
    extracting text from each specific bounding box.
    """

    def __init__(self, tesseract_config="--psm 6"):
        self.tesseract_config = tesseract_config

    def process_file(self, file_path):
        """Processes a PDF or Image and returns structured layout blocks."""
        images = []
        if file_path.lower().endswith(".pdf"):
            images = convert_from_path(file_path, dpi=300)
        else:
            images = [cv2.imread(file_path)]

        all_pages_data = []

        for page_num, img in enumerate(images):
            # Convert PIL image to OpenCV format if PDF
            if not isinstance(img, np.ndarray):
                img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            
            blocks = self._detect_layout_blocks(img)
            all_pages_data.append({
                "page": page_num + 1,
                "blocks": blocks
            })

        return all_pages_data

    def _detect_layout_blocks(self, image):
        # 1. Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # 2. Apply Gaussian Blur to remove noise
        blur = cv2.GaussianBlur(gray, (5, 5), 0)

        # 3. Apply adaptive thresholding to get a binary image (black background, white text)
        thresh = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
        )

        # 4. Morphological Operations to connect text into distinct layout blocks
        # We use a rectangular kernel to bridge gaps between words in a line, and lines in a paragraph
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 10))
        dilated = cv2.dilate(thresh, kernel, iterations=1)

        # 5. Find contours (bounding boxes around the connected text blocks)
        contours, hierarchy = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        layout_blocks = []
        
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            
            # Filter out tiny noise contours
            if w > 30 and h > 15:
                layout_blocks.append({
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "area": w * h
                })

        # 6. Sort blocks top-to-bottom, then left-to-right
        # This mirrors natural reading order and table structures
        layout_blocks = sorted(layout_blocks, key=lambda b: (b['y'] // 20, b['x']))

        # 7. Extract text from each isolated block
        parsed_data = []
        for block in layout_blocks:
            x, y, w, h = block['x'], block['y'], block['w'], block['h']
            
            # Crop the block from the original high-res grayscale image
            roi = gray[y:y+h, x:x+w]
            
            # Run OCR specifying that we are looking at a single unifrom block of text
            text = pytesseract.image_to_string(roi, config=self.tesseract_config).strip()
            
            if text:
                parsed_data.append({
                    "rect": [x, y, w, h],
                    "text": text,
                    "type": self._classify_block(w, h, text)
                })

        return parsed_data

    def _classify_block(self, w, h, text):
        """Heuristic classification of a layout block."""
        if h < 50 and len(text) < 50:
            return "field_label"
        elif w > 400 and h > 100:
            return "paragraph_or_table_row"
        elif "\n" in text:
            return "multi_line_block"
        else:
            return "value"

# ─────────────────────────────────────────────────────────────────────────────
# Usage Example
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Test runner script
    print("Initializing Layout Detector...")
    detector = LayoutDetector()
    
    # Example usage (point to a real image or PDF path to test):
    # result = detector.process_file('/path/to/invoice.pdf')
    # for page in result:
    #     for block in page['blocks']:
    #         print(f"Type: {block['type']} | Text: {block['text'].replace(chr(10), ' ')}")
    print("Algorithm ready for integration.")
