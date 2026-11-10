import json
import os
import tempfile
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from mcq_app.services import llm_service
from mcq_app.services.llm_service import (
    LLMConfigError,
    LLMError,
    OllamaProvider,
    get_llm_provider,
    parse_json_response,
)
from mcq_app.services.ocr_service import OCRError, OCRService, boxes_to_text

from .helpers import FakeOCRProvider, VALID_LLM_RESULT, make_image, make_scanned_pdf, make_text_pdf


class JSONParsingTests(SimpleTestCase):
    def test_plain(self):
        data = parse_json_response(json.dumps({"questions": VALID_LLM_RESULT}))
        self.assertEqual(len(data["questions"]), 2)

    def test_code_fence_and_chatter(self):
        raw = "Sure! Here you go:\n```json\n" + json.dumps({"questions": VALID_LLM_RESULT}) + "\n```\nDone."
        self.assertEqual(len(parse_json_response(raw)["questions"]), 2)

    def test_surrounding_text_without_fence(self):
        raw = "Result: " + json.dumps({"questions": []}) + " thanks"
        self.assertEqual(parse_json_response(raw), {"questions": []})

    def test_bare_list(self):
        self.assertEqual(len(parse_json_response(json.dumps(VALID_LLM_RESULT))["questions"]), 2)

    def test_invalid(self):
        for bad in ["", "not json", '{"questions": "nope"}', "{broken"]:
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                parse_json_response(bad)


class ProviderRetryTests(SimpleTestCase):
    def cfg(self, **extra):
        return {**settings.MCQ_LLM, "RETRIES": 2, **extra}

    @mock.patch.object(llm_service.time, "sleep")
    def test_retries_on_bad_json_then_succeeds(self, _sleep):
        provider = OllamaProvider(self.cfg())
        with mock.patch.object(provider, "generate", side_effect=["garbage", json.dumps({"questions": VALID_LLM_RESULT})]) as gen:
            self.assertEqual(len(provider.extract("text")), 2)
        self.assertEqual(gen.call_count, 2)

    @mock.patch.object(llm_service.time, "sleep")
    def test_gives_up_after_retries(self, _sleep):
        provider = OllamaProvider(self.cfg())
        with mock.patch.object(provider, "generate", return_value="garbage") as gen:
            with self.assertRaises(LLMError):
                provider.extract("text")
        self.assertEqual(gen.call_count, 3)

    def test_config_error_not_retried(self):
        provider = OllamaProvider(self.cfg())
        with mock.patch.object(provider, "generate", side_effect=LLMConfigError("no model")) as gen:
            with self.assertRaises(LLMConfigError):
                provider.extract("text")
        self.assertEqual(gen.call_count, 1)

    def test_provider_selection(self):
        with override_settings(MCQ_LLM={**settings.MCQ_LLM, "PROVIDER": "gemini"}):
            self.assertEqual(get_llm_provider().name, "gemini")
        with override_settings(MCQ_LLM={**settings.MCQ_LLM, "PROVIDER": "bogus"}):
            with self.assertRaises(LLMConfigError):
                get_llm_provider()

    def test_gemini_requires_key(self):
        with override_settings(MCQ_LLM={**settings.MCQ_LLM, "PROVIDER": "gemini", "GEMINI_API_KEY": ""}):
            with self.assertRaises(LLMConfigError):
                get_llm_provider().check()

    def test_ollama_unreachable_is_config_error(self):
        provider = OllamaProvider(self.cfg(OLLAMA_BASE_URL="http://127.0.0.1:9"))
        with self.assertRaises(LLMConfigError):
            provider.check()


class OCRTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_boxes_reading_order(self):
        items = [
            ("(B) two", 300, 102, 400, 118),
            ("Q.1 What?", 10, 50, 200, 66),
            ("(A) one", 10, 100, 120, 116),
        ]
        self.assertEqual(boxes_to_text(items), "Q.1 What?\n(A) one (B) two")

    def test_text_layer_pdf_skips_ocr(self):
        provider = FakeOCRProvider()
        pages = OCRService(provider=provider).extract_pages(self.write("a.pdf", make_text_pdf(pages=2)))
        self.assertEqual([p.source for p in pages], ["text_layer", "text_layer"])
        self.assertIn("binary search", pages[0].text)
        self.assertEqual(provider.calls, 0)

    def test_scanned_pdf_uses_ocr(self):
        provider = FakeOCRProvider()
        pages = OCRService(provider=provider).extract_pages(self.write("s.pdf", make_scanned_pdf()))
        self.assertEqual(pages[0].source, "ocr")
        self.assertEqual(provider.calls, 1)

    def test_image_uses_ocr(self):
        provider = FakeOCRProvider()
        pages = OCRService(provider=provider).extract_pages(self.write("i.png", make_image()))
        self.assertEqual(len(pages), 1)
        self.assertIn("recognised", pages[0].text)

    def test_page_failure_is_isolated_but_all_empty_raises(self):
        class Broken:
            def recognize(self, image):
                raise RuntimeError("engine crashed")

        with self.assertRaises(OCRError):
            OCRService(provider=Broken()).extract_pages(self.write("s.pdf", make_scanned_pdf()))

    def test_missing_file(self):
        with self.assertRaises(OCRError):
            OCRService(provider=FakeOCRProvider()).extract_pages(os.path.join(self.tmp, "nope.pdf"))

    def test_nvidia_requires_key(self):
        cfg = {**settings.MCQ_OCR, "PROVIDER": "nvidia", "NVIDIA_API_KEY": ""}
        with override_settings(MCQ_OCR=cfg):
            with self.assertRaises(OCRError):
                OCRService().provider

    def test_nvidia_response_parsing(self):
        from mcq_app.services.ocr_service import NvidiaPaddleProvider

        data = {"data": [{"index": 0, "text_detections": [
            {"text_prediction": {"text": "(A) one"}, "bounding_box": {"points": [{"x": 0.1, "y": 0.5}, {"x": 0.2, "y": 0.52}]}},
            {"text_prediction": {"text": "Q.1 What?"}, "bounding_box": {"points": [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.12}]}},
        ]}]}
        self.assertEqual(NvidiaPaddleProvider._parse(data), "Q.1 What?\n(A) one")
