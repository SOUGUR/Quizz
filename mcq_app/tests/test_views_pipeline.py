import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from mcq_app.models import Option, Question, QuestionPaper
from mcq_app.services.llm_service import LLMConfigError, LLMError
from mcq_app.services.ocr_service import OCRError, PageText
from mcq_app.services.parser_service import ParserService

from .helpers import SAMPLE_TEXT, FakeLLM, make_image, make_text_pdf

TMP_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP_MEDIA, TASK_BACKEND="sync")
class BaseCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def make_paper(self, data=None, name="paper.pdf"):
        return QuestionPaper.objects.create(
            title="Test", upload_file=SimpleUploadedFile(name, data or make_text_pdf())
        )


class UploadTests(BaseCase):
    def post(self, name, data, **extra):
        return self.client.post(
            reverse("upload_paper"),
            {"title": "GATE", "upload_file": SimpleUploadedFile(name, data), **extra},
        )

    def test_get_form(self):
        self.assertContains(self.client.get(reverse("upload_paper")), "Upload New Question Paper")

    def test_valid_pdf_upload(self):
        resp = self.post("paper.pdf", make_text_pdf())
        self.assertRedirects(resp, reverse("paper_list"))
        paper = QuestionPaper.objects.get()
        self.assertEqual(paper.status, QuestionPaper.Status.PENDING)

    def test_valid_image_upload(self):
        self.post("scan.png", make_image())
        self.assertEqual(QuestionPaper.objects.count(), 1)

    def test_rejects_bad_extension(self):
        resp = self.post("evil.exe", b"MZ....")
        self.assertContains(resp, "Unsupported file type")
        self.assertEqual(QuestionPaper.objects.count(), 0)

    def test_rejects_disguised_file(self):
        resp = self.post("fake.pdf", b"<html><script>alert(1)</script></html>")
        self.assertContains(resp, "does not match its extension")
        self.assertEqual(QuestionPaper.objects.count(), 0)

    def test_rejects_corrupt_pdf(self):
        resp = self.post("broken.pdf", b"%PDF-1.4 garbage garbage")
        self.assertContains(resp, "corrupted")

    @override_settings(MAX_UPLOAD_SIZE_MB=0)
    def test_rejects_oversize(self):
        resp = self.post("paper.pdf", make_text_pdf())
        self.assertContains(resp, "too large")

    @override_settings(MAX_PDF_PAGES=1)
    def test_rejects_too_many_pages(self):
        resp = self.post("paper.pdf", make_text_pdf(pages=3))
        self.assertContains(resp, "maximum is 1")

    def test_rejects_missing_title(self):
        resp = self.client.post(reverse("upload_paper"), {"title": "", "upload_file": SimpleUploadedFile("a.pdf", make_text_pdf())})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(QuestionPaper.objects.count(), 0)

    def test_upload_and_process_now(self):
        with mock.patch("mcq_app.services.parser_service.get_llm_provider", return_value=FakeLLM()):
            resp = self.post("paper.pdf", make_text_pdf(), process_now="1")
        paper = QuestionPaper.objects.get()
        self.assertRedirects(resp, reverse("paper_detail", args=[paper.id]))
        paper.refresh_from_db()
        self.assertEqual(paper.status, QuestionPaper.Status.COMPLETED)


@mock.patch("mcq_app.services.parser_service.get_llm_provider")
class PipelineTests(BaseCase):
    def test_end_to_end_with_real_text_layer(self, get_llm):
        llm = FakeLLM()
        get_llm.return_value = llm
        paper = self.make_paper()
        self.assertTrue(ParserService.claim(paper.id))
        self.assertTrue(ParserService.process_paper(paper.id))

        paper.refresh_from_db()
        self.assertEqual(paper.status, QuestionPaper.Status.COMPLETED)
        self.assertEqual(paper.page_count, 1)
        self.assertIn("binary search", paper.ocr_text)
        self.assertEqual(paper.llm_provider, "fake:llm")
        questions = list(paper.questions.all())
        self.assertEqual([q.question_number for q in questions], ["Q.1", "Q.2"])
        self.assertEqual(
            {o.option_label: o.text for o in questions[0].options.all()},
            {"A": "O(n)", "B": "O(log n)", "C": "O(n log n)", "D": "O(1)"},
        )

    def test_reprocessing_does_not_duplicate(self, get_llm):
        get_llm.return_value = FakeLLM()
        paper = self.make_paper()
        for _ in range(3):
            ParserService.claim(paper.id)
            ParserService.process_paper(paper.id)
        self.assertEqual(paper.questions.count(), 2)
        self.assertEqual(Option.objects.filter(question__question_paper=paper).count(), 8)

    def test_reuse_ocr_skips_ocr(self, get_llm):
        get_llm.return_value = FakeLLM()
        paper = self.make_paper()
        ParserService.claim(paper.id)
        ParserService.process_paper(paper.id)
        with mock.patch("mcq_app.services.parser_service.OCRService") as ocr:
            ParserService.claim(paper.id)
            self.assertTrue(ParserService.process_paper(paper.id, reuse_ocr=True))
            ocr.assert_not_called()

    def test_claim_is_exclusive(self, get_llm):
        paper = self.make_paper()
        self.assertTrue(ParserService.claim(paper.id))
        self.assertFalse(ParserService.claim(paper.id))

    def test_incomplete_and_fabricated_items_rejected(self, get_llm):
        get_llm.return_value = FakeLLM(results=[
            {"question_number": "Q.9", "question_text": "Which is a prime?", "options": {"A": "4", "B": "6"}},
            {"question_number": "Q.7", "question_text": "Who painted the Mona Lisa in Florence?",
             "options": {"A": "Leonardo", "B": "Raphael", "C": "Donatello", "D": "Michelangelo"}},
        ])
        paper = self.make_paper()
        ParserService.claim(paper.id)
        self.assertFalse(ParserService.process_paper(paper.id))
        paper.refresh_from_db()
        self.assertEqual(paper.status, QuestionPaper.Status.FAILED)
        self.assertIn("No complete 4-option MCQs", paper.error_message)
        self.assertEqual(Question.objects.count(), 0)

    def test_llm_config_error_marks_failed(self, get_llm):
        llm = FakeLLM()
        llm.check = mock.Mock(side_effect=LLMConfigError("Ollama model 'x' is not installed."))
        get_llm.return_value = llm
        paper = self.make_paper()
        ParserService.claim(paper.id)
        ParserService.process_paper(paper.id)
        paper.refresh_from_db()
        self.assertEqual(paper.status, QuestionPaper.Status.FAILED)
        self.assertIn("not installed", paper.error_message)

    def test_all_chunks_failing(self, get_llm):
        get_llm.return_value = FakeLLM(error=LLMError("timeout"))
        paper = self.make_paper()
        ParserService.claim(paper.id)
        ParserService.process_paper(paper.id)
        paper.refresh_from_db()
        self.assertEqual(paper.status, QuestionPaper.Status.FAILED)

    def test_ocr_error(self, get_llm):
        get_llm.return_value = FakeLLM()
        paper = self.make_paper()
        with mock.patch("mcq_app.services.parser_service.OCRService") as ocr:
            ocr.return_value.extract_pages.side_effect = OCRError("No text could be read")
            ParserService.claim(paper.id)
            ParserService.process_paper(paper.id)
        paper.refresh_from_db()
        self.assertEqual(paper.status, QuestionPaper.Status.FAILED)
        self.assertIn("No text", paper.error_message)

    def test_unexpected_error_hides_details(self, get_llm):
        get_llm.return_value = FakeLLM(error=ZeroDivisionError("secret internals"))
        paper = self.make_paper()
        ParserService.claim(paper.id)
        ParserService.process_paper(paper.id)
        paper.refresh_from_db()
        self.assertEqual(paper.status, QuestionPaper.Status.FAILED)
        self.assertNotIn("secret", paper.error_message)

    def test_multi_page_question_spanning_pages(self, get_llm):
        """A question split over a page break must reach the LLM in one piece."""
        seen = []

        class Spy(FakeLLM):
            def extract(self, chunk):
                seen.append(chunk)
                return super().extract(chunk)

        get_llm.return_value = Spy()
        paper = self.make_paper()
        pages = [PageText(1, SAMPLE_TEXT[:150], "ocr"), PageText(2, SAMPLE_TEXT[150:], "ocr")]
        with mock.patch("mcq_app.services.parser_service.OCRService") as ocr:
            ocr.return_value.extract_pages.return_value = pages
            ParserService.claim(paper.id)
            ParserService.process_paper(paper.id)
        self.assertTrue(any("binary search" in c and "(D) O(1)" in c for c in seen))


    def test_page_headers_never_reach_llm(self, get_llm):
        """Regression: the pipeline itself (not just the helper) must strip headers."""
        seen = []

        class Spy(FakeLLM):
            def extract(self, chunk):
                seen.append(chunk)
                return super().extract(chunk)

        get_llm.return_value = Spy()
        paper = self.make_paper()
        header = "Organising Institute: IIT Roorkee   Page {} of 4"
        pages = [PageText(i, header.format(i) + "\n" + SAMPLE_TEXT, "text_layer") for i in range(1, 5)]
        with mock.patch("mcq_app.services.parser_service.OCRService") as ocr:
            ocr.return_value.extract_pages.return_value = pages
            ParserService.claim(paper.id)
            ParserService.process_paper(paper.id)
        self.assertTrue(seen)
        self.assertFalse(any("Roorkee" in c for c in seen))


class ViewTests(BaseCase):
    def test_list_and_detail_render(self):
        paper = self.make_paper()
        q = Question.objects.create(question_paper=paper, question_number="Q.1", question_text="What is 2+2?", order=1)
        for label, text in zip("ABCD", ["3", "4", "5", "6"]):
            Option.objects.create(question=q, option_label=label, text=text)
        QuestionPaper.objects.filter(pk=paper.pk).update(status="completed")

        resp = self.client.get(reverse("paper_list"))
        self.assertContains(resp, "Completed (1 MCQs)")
        resp = self.client.get(reverse("paper_detail", args=[paper.id]))
        self.assertContains(resp, "What is 2+2?")
        self.assertContains(resp, "<strong>(B)</strong> 4", html=False)

    def test_process_requires_post(self):
        paper = self.make_paper()
        self.assertEqual(self.client.get(reverse("process_paper", args=[paper.id])).status_code, 405)

    def test_process_post_runs_pipeline(self):
        paper = self.make_paper()
        with mock.patch("mcq_app.services.parser_service.get_llm_provider", return_value=FakeLLM()):
            resp = self.client.post(reverse("process_paper", args=[paper.id]))
        self.assertRedirects(resp, reverse("paper_detail", args=[paper.id]))
        self.assertEqual(paper.questions.count(), 2)

    def test_process_while_busy_is_refused(self):
        paper = self.make_paper()
        ParserService.claim(paper.id)
        with mock.patch("mcq_app.views.dispatch_processing") as dispatch:
            self.client.post(reverse("process_paper", args=[paper.id]))
            dispatch.assert_not_called()

    def test_status_endpoint(self):
        paper = self.make_paper()
        data = self.client.get(reverse("paper_status", args=[paper.id])).json()
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["question_count"], 0)

    def test_stale_processing_recovered(self):
        from datetime import timedelta

        from django.utils import timezone

        paper = self.make_paper()
        QuestionPaper.objects.filter(pk=paper.pk).update(
            status="processing", started_at=timezone.now() - timedelta(days=1)
        )
        data = self.client.get(reverse("paper_status", args=[paper.id])).json()
        self.assertEqual(data["status"], "failed")
        self.assertIn("interrupted", data["error"])

    def test_404(self):
        self.assertEqual(self.client.get(reverse("paper_detail", args=[999])).status_code, 404)
