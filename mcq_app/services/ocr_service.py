"""
OCR service: turns an uploaded PDF/image into per-page text.

Design:
* Pages are processed one at a time (low memory, per-page error isolation).
* For PDFs, the embedded text layer is used when a page has one (digital
  PDFs) - this is exact and instant. OCR runs only on scanned pages.
* PDF rendering uses PyMuPDF by default (pip-only, works on Windows/Linux/mac
  without Poppler). Poppler/pdf2image is still supported via PDF_RENDERER.
* The OCR engine is pluggable (OCR_PROVIDER): local PaddleOCR or NVIDIA's
  hosted PaddleOCR API.
"""

import base64
import io
import logging
import os
import threading
import time
from dataclasses import dataclass
from statistics import median

import requests
from django.conf import settings
from PIL import Image

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


class OCRError(Exception):
    """Raised with a user-safe message when OCR cannot produce any text."""


@dataclass
class PageText:
    page_number: int
    text: str
    source: str  # "text_layer" | "ocr" | "failed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def boxes_to_text(items):
    """
    Rebuild reading order from OCR detections.

    `items` is a list of (text, x0, y0, x1, y1). Detections whose vertical
    centres are close are merged into one line (sorted left-to-right); lines
    are emitted top-to-bottom. This keeps "A) foo   B) bar" on one line and
    keeps question text before its options.
    """
    items = [i for i in items if i[0] and i[0].strip()]
    if not items:
        return ""
    heights = [max(1e-6, y1 - y0) for _, _, y0, _, y1 in items]
    tolerance = median(heights) * 0.6

    items.sort(key=lambda i: ((i[2] + i[4]) / 2, i[1]))
    lines, current, current_y = [], [], None
    for item in items:
        yc = (item[2] + item[4]) / 2
        if current and abs(yc - current_y) > tolerance:
            lines.append(current)
            current = []
        if not current:
            current_y = yc
        current.append(item)
    if current:
        lines.append(current)

    return "\n".join(
        " ".join(t.strip() for t, *_ in sorted(line, key=lambda i: i[1])) for line in lines
    )


# ---------------------------------------------------------------------------
# OCR providers
# ---------------------------------------------------------------------------
class BaseOCRProvider:
    name = "base"

    def recognize(self, image: Image.Image) -> str:
        raise NotImplementedError


class PaddleLocalProvider(BaseOCRProvider):
    """Runs PaddleOCR (PP-OCRv5 mobile models) on the local CPU."""

    name = "paddle_local"
    _engine = None
    _lock = threading.Lock()

    @classmethod
    def _get_engine(cls):
        # Model loading is slow (~seconds) and memory-heavy: load once and
        # share across requests.
        if cls._engine is None:
            with cls._lock:
                if cls._engine is None:
                    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
                    try:
                        from paddleocr import PaddleOCR
                    except ImportError as exc:
                        raise OCRError(
                            "Local OCR is not installed. Run `pip install paddleocr paddlepaddle` "
                            "or set OCR_PROVIDER=nvidia."
                        ) from exc
                    logger.info("Loading local PaddleOCR models (first run downloads them)...")
                    cls._engine = PaddleOCR(
                        lang=os.getenv("PADDLE_OCR_LANG", "en"),
                        text_detection_model_name=os.getenv(
                            "PADDLE_DET_MODEL", "PP-OCRv5_mobile_det"
                        ),
                        text_recognition_model_name=os.getenv(
                            "PADDLE_REC_MODEL", "en_PP-OCRv5_mobile_rec"
                        ),
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        # paddlepaddle 3.3's oneDNN CPU backend crashes on
                        # Windows ("ConvertPirAttribute2RuntimeAttribute not
                        # support"); plain CPU inference is reliable.
                        enable_mkldnn=os.getenv("PADDLE_ENABLE_MKLDNN", "false").lower() == "true",
                    )
        return cls._engine

    def recognize(self, image):
        import numpy as np

        engine = self._get_engine()
        # PaddleOCR expects BGR ndarrays (OpenCV convention).
        arr = np.array(image.convert("RGB"))[:, :, ::-1].copy()
        with self._lock:  # the engine is not guaranteed thread-safe
            results = engine.predict(arr)

        items = []
        for res in results:
            texts = res["rec_texts"]
            boxes = res["rec_boxes"]
            for text, box in zip(texts, boxes):
                x0, y0, x1, y1 = (float(v) for v in box[:4])
                items.append((text, x0, y0, x1, y1))
        return boxes_to_text(items)


class NvidiaPaddleProvider(BaseOCRProvider):
    """NVIDIA-hosted PaddleOCR (ai.api.nvidia.com). Requires an API key."""

    name = "nvidia"
    # NVIDIA rejects inline base64 payloads above roughly 180 KB.
    MAX_B64_CHARS = 180_000

    def __init__(self, cfg):
        self.url = cfg["NVIDIA_API_URL"]
        self.key = cfg["NVIDIA_API_KEY"]
        self.timeout = cfg["TIMEOUT"]
        self.retries = cfg["RETRIES"]
        if not self.url or not self.key:
            raise OCRError(
                "NVIDIA OCR is selected but NVIDIA_OCR_API_URL / NVIDIA_OCR_API_KEY are not set."
            )

    def _encode(self, image):
        """JPEG-encode, shrinking quality then size until under the API limit."""
        img = image.convert("RGB")
        for _ in range(8):
            for quality in (85, 70, 55):
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=quality, optimize=True)
                b64 = base64.b64encode(buf.getvalue()).decode()
                if len(b64) <= self.MAX_B64_CHARS:
                    return b64
            img = img.resize((int(img.width * 0.8), int(img.height * 0.8)), Image.LANCZOS)
        raise OCRError("Page image could not be compressed small enough for the NVIDIA OCR API.")

    def recognize(self, image):
        payload = {
            "input": [{"type": "image_url", "url": f"data:image/jpeg;base64,{self._encode(image)}"}]
        }
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        }
        last_error = None
        for attempt in range(1, self.retries + 2):
            try:
                resp = requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"network error: {exc.__class__.__name__}"
            else:
                if resp.status_code in (401, 403):
                    # Not retryable. Never log the key or the auth header.
                    raise OCRError("NVIDIA OCR rejected the API key (HTTP %s)." % resp.status_code)
                if resp.status_code == 200:
                    return self._parse(resp.json())
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code < 500 and resp.status_code != 429:
                    break
            logger.warning("NVIDIA OCR attempt %s failed: %s", attempt, last_error)
            time.sleep(2 ** attempt)
        raise OCRError(f"NVIDIA OCR failed ({last_error}).")

    @staticmethod
    def _parse(data):
        items = []
        for entry in data.get("data", []):
            for det in entry.get("text_detections", []):
                text = det.get("text_prediction", {}).get("text", "")
                points = det.get("bounding_box", {}).get("points", [])
                if points:
                    xs = [p["x"] for p in points]
                    ys = [p["y"] for p in points]
                    items.append((text, min(xs), min(ys), max(xs), max(ys)))
                else:
                    items.append((text, 0, len(items), 1, len(items) + 1))
        return boxes_to_text(items)


def get_ocr_provider():
    cfg = settings.MCQ_OCR
    provider = cfg["PROVIDER"]
    if provider in ("paddle_local", "paddle", "local"):
        return PaddleLocalProvider()
    if provider == "nvidia":
        return NvidiaPaddleProvider(cfg)
    raise OCRError(f"Unknown OCR_PROVIDER '{provider}'. Use 'paddle_local' or 'nvidia'.")


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------
class OCRService:
    def __init__(self, provider=None, progress=None):
        self.cfg = settings.MCQ_OCR
        self._provider = provider
        self.progress = progress or (lambda msg: None)

    @property
    def provider(self):
        # Created lazily so digital PDFs never load an OCR engine at all.
        if self._provider is None:
            self._provider = get_ocr_provider()
        return self._provider

    def extract_pages(self, file_path):
        if not os.path.exists(file_path):
            raise OCRError("The uploaded file could not be found on disk.")
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            pages = self._extract_pdf(file_path)
        elif ext in IMAGE_EXTENSIONS:
            pages = [self._ocr_image(1, Image.open(file_path))]
        else:
            raise OCRError(f"Unsupported file format: {ext}")

        if not any(p.text.strip() for p in pages):
            raise OCRError("No text could be read from the file. Is the scan blank or too blurry?")
        return pages

    # -- PDF -----------------------------------------------------------------
    def _extract_pdf(self, path):
        import pymupdf as fitz

        pages = []
        with fitz.open(path) as doc:
            total = doc.page_count
            for index in range(total):
                number = index + 1
                page = doc[index]
                text = page.get_text("text") if self.cfg["USE_PDF_TEXT_LAYER"] else ""
                if len(text.strip()) >= self.cfg["MIN_TEXT_LAYER_CHARS"]:
                    self.progress(f"Reading text layer: page {number}/{total}")
                    pages.append(PageText(number, text.strip(), "text_layer"))
                    continue

                self.progress(f"Running OCR: page {number}/{total}")
                try:
                    image = self._render_page(path, page, number)
                except Exception:
                    logger.exception("Failed to render page %s of %s", number, path)
                    pages.append(PageText(number, "", "failed"))
                    continue
                pages.append(self._ocr_image(number, image))
        return pages

    def _render_page(self, path, page, number):
        dpi = self.cfg["DPI"]
        if self.cfg["PDF_RENDERER"] == "poppler":
            from pdf2image import convert_from_path

            images = convert_from_path(
                path,
                dpi=dpi,
                first_page=number,
                last_page=number,
                poppler_path=self.cfg["POPPLER_PATH"],
            )
            return images[0]
        pix = page.get_pixmap(dpi=dpi)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    # -- Images --------------------------------------------------------------
    def _ocr_image(self, number, image):
        try:
            text = self.provider.recognize(image)
            return PageText(number, text.strip(), "ocr")
        except OCRError as exc:
            # Configuration / auth problems affect every page: fail fast.
            if "API key" in str(exc) or "not set" in str(exc) or "not installed" in str(exc):
                raise
            logger.warning("OCR failed on page %s: %s", number, exc)
        except Exception:
            logger.exception("Unexpected OCR error on page %s", number)
        return PageText(number, "", "failed")
