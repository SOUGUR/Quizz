from django.test import SimpleTestCase

from mcq_app.services.chunking import chunk_text, split_into_blocks
from mcq_app.services.validation import validate_and_dedupe, validate_item

from .helpers import SAMPLE_TEXT, VALID_LLM_RESULT


def make_paper(n):
    return "\n".join(
        f"Q.{i} Question number {i} asks something long enough to matter here?\n"
        f"(A) alpha {i}\n(B) beta {i}\n(C) gamma {i}\n(D) delta {i}"
        for i in range(1, n + 1)
    )


class ChunkingTests(SimpleTestCase):
    def test_short_text_is_single_chunk(self):
        self.assertEqual(chunk_text(SAMPLE_TEXT, max_chars=5000), [SAMPLE_TEXT.strip()])

    def test_empty(self):
        self.assertEqual(chunk_text("   "), [])

    def test_blocks_detect_question_starts(self):
        preamble, blocks = split_into_blocks(SAMPLE_TEXT)
        self.assertIn("General Instructions", preamble)
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0].startswith("Q.1"))
        self.assertIn("(D) O(1)", blocks[0])

    def test_numbering_styles(self):
        text = "1. first?\n(a) x\n2) second?\n(a) y\nQuestion 3: third?\nQ4 fourth?"
        _, blocks = split_into_blocks(text)
        self.assertEqual(len(blocks), 4)

    def test_questions_never_split_across_chunks(self):
        chunks = chunk_text(make_paper(30), max_chars=600, overlap=1)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            _, blocks = split_into_blocks(chunk)
            for block in blocks:
                # every question block in every chunk carries all four options
                for label in "ABCD":
                    self.assertIn(f"({label})", block)

    def test_overlap_repeats_last_question(self):
        chunks = chunk_text(make_paper(30), max_chars=600, overlap=1)
        last_of_first = split_into_blocks(chunks[0])[1][-1]
        first_of_second = split_into_blocks(chunks[1])[1][0]
        self.assertEqual(last_of_first, first_of_second)

    def test_every_question_covered(self):
        chunks = chunk_text(make_paper(30), max_chars=600)
        joined = "\n".join(chunks)
        for i in range(1, 31):
            self.assertIn(f"Q.{i} ", joined)

    def test_fallback_without_numbering(self):
        text = ("lorem ipsum dolor sit amet " * 10 + "\n") * 40
        chunks = chunk_text(text, max_chars=1000)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertGreater(len(chunks), 3)


class ValidationTests(SimpleTestCase):
    def test_valid_item(self):
        vq, reason = validate_item(VALID_LLM_RESULT[0], SAMPLE_TEXT)
        self.assertIsNone(reason)
        self.assertEqual(vq.options["B"], "O(log n)")
        self.assertEqual(len(vq.fingerprint), 64)

    def test_rejects_missing_option(self):
        item = {"question_text": "Which is it?", "options": {"A": "1", "B": "2", "C": "3"}}
        vq, reason = validate_item(item)
        self.assertIsNone(vq)
        self.assertIn("D", reason)

    def test_rejects_empty_option(self):
        item = {"question_text": "Which?", "options": {"A": "1", "B": "", "C": "3", "D": "4"}}
        self.assertIsNone(validate_item(item)[0])

    def test_rejects_missing_question(self):
        item = {"question_text": " ", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}}
        self.assertIsNone(validate_item(item)[0])

    def test_accepts_list_and_lowercase_and_strips_labels(self):
        item = {"question_text": "Pick one", "options": ["(A) one", "(B) two", "(C) three", "(D) four"]}
        vq, _ = validate_item(item)
        self.assertEqual(vq.options, {"A": "one", "B": "two", "C": "three", "D": "four"})
        item = {"question_text": "Pick one", "options": {"a": "x", "b": "y", "c": "z", "d": "w"}}
        self.assertEqual(validate_item(item)[0].options["D"], "w")

    def test_preserves_math(self):
        item = {"question_text": "Evaluate ∫ x^2 dx from 0 to 1", "options": {"A": "1/3", "B": "1/2", "C": "x³/3", "D": "∞"}}
        vq, _ = validate_item(item)
        self.assertIn("∫ x^2", vq.question_text)
        self.assertEqual(vq.options["C"], "x³/3")

    def test_rejects_fabricated_question(self):
        fabricated = {
            "question_text": "Who invented the telephone in Scotland during Victorian times?",
            "options": {"A": "Bell", "B": "Edison", "C": "Tesla", "D": "Marconi"},
        }
        vq, reason = validate_item(fabricated, SAMPLE_TEXT)
        self.assertIsNone(vq)
        self.assertIn("grounding", reason)

    def test_dedupe_exact_and_fuzzy(self):
        noisy = dict(VALID_LLM_RESULT[0], question_text=VALID_LLM_RESULT[0]["question_text"] + " ")
        near = dict(VALID_LLM_RESULT[0], question_text="What is the time complexity of binary search on n elements")
        items = [(VALID_LLM_RESULT[0], SAMPLE_TEXT), (noisy, SAMPLE_TEXT), (near, SAMPLE_TEXT),
                 (VALID_LLM_RESULT[1], SAMPLE_TEXT)]
        report = validate_and_dedupe(items)
        self.assertEqual(len(report.accepted), 2)
        self.assertEqual(report.duplicates, 2)
        self.assertEqual([q.question_number for q in report.accepted], ["Q.1", "Q.2"])

    def test_same_stem_different_options_kept(self):
        a = {"question_number": "5", "question_text": "Which of the following is true?",
             "options": {"A": "cats bark", "B": "dogs meow", "C": "fish fly", "D": "birds swim"}}
        b = {"question_number": "6", "question_text": "Which of the following is true?",
             "options": {"A": "sun is cold", "B": "ice is hot", "C": "water is wet", "D": "fire is cold"}}
        report = validate_and_dedupe([(a, None), (b, None)])
        self.assertEqual(len(report.accepted), 2)


class HeaderFooterTests(SimpleTestCase):
    def test_repeated_headers_removed_questions_kept(self):
        from mcq_app.services.chunking import strip_repeated_lines

        pages = [
            f"GATE CS 2025\nOrganising Institute: IIT Roorkee   Page {i} of 5\nQ.{i} Question {i}?\n(A) a\n(B) b\n(C) c\n(D) d"
            for i in range(1, 6)
        ]
        cleaned = strip_repeated_lines(pages)
        for i, page in enumerate(cleaned, start=1):
            self.assertNotIn("Roorkee", page)
            self.assertNotIn("GATE CS 2025", page)
            self.assertIn(f"Q.{i} Question {i}?", page)
            self.assertIn("(A) a", page)

    def test_few_pages_untouched(self):
        from mcq_app.services.chunking import strip_repeated_lines

        self.assertEqual(strip_repeated_lines(["h\nx", "h\ny"]), ["h\nx", "h\ny"])

    def test_question_number_lines_on_every_page_kept(self):
        """Regression: 'Q.1', 'Q.2'... normalize to the same key but must survive."""
        from mcq_app.services.chunking import strip_repeated_lines

        pages = [f"Q.{i}\nText {i}\n(A)\nx\n(B)\ny\n(C)\nz\n(D)\nw" for i in range(1, 6)]
        self.assertEqual(strip_repeated_lines(pages), pages)

    def test_short_repeated_option_values_kept(self):
        """Regression: option values like '4' or 'True' repeat across pages but are not headers."""
        from mcq_app.services.chunking import strip_repeated_lines

        pages = [f"Header line for the exam\nQ.{i} Pick\n(A)\nTrue\n(B)\nFalse\n(C)\n4\n(D)\n5" for i in range(1, 6)]
        cleaned = strip_repeated_lines(pages)
        for page in cleaned:
            self.assertNotIn("Header line", page)
            for value in ("True", "False", "4", "5"):
                self.assertIn(value, page)


class NumberingTests(SimpleTestCase):
    def test_restores_printed_prefix(self):
        item = dict(VALID_LLM_RESULT[1], question_number="2")
        vq, _ = validate_item(item, SAMPLE_TEXT)
        self.assertEqual(vq.question_number, "Q.2")

    def test_leaves_number_when_not_in_source(self):
        item = dict(VALID_LLM_RESULT[1], question_number="7")
        vq, _ = validate_item(item, SAMPLE_TEXT)
        self.assertEqual(vq.question_number, "7")


class LayoutTests(SimpleTestCase):
    def test_joins_label_and_value_lines(self):
        from mcq_app.services.chunking import normalize_layout

        text = "Q.2 Pick\n\n\n\n(A) \nvisionary \n(B)\nshortsighted\n(C) progressive\n(D)\n42"
        out = normalize_layout(text)
        self.assertIn("(A) visionary", out)
        self.assertIn("(B) shortsighted", out)
        self.assertIn("(C) progressive", out)
        self.assertIn("(D) 42", out)
        self.assertNotIn("\n\n\n", out)


class MaxBlocksTests(SimpleTestCase):
    def test_one_question_per_chunk(self):
        chunks = chunk_text(make_paper(5), max_chars=10_000, max_blocks=1)
        self.assertEqual(len(chunks), 5)
        for i, c in enumerate(chunks, start=1):
            self.assertTrue(c.startswith(f"Q.{i} "))

    def test_overlap_clamped_below_max_blocks(self):
        chunks = chunk_text(make_paper(4), max_chars=10_000, overlap=5, max_blocks=2)
        self.assertEqual(len(chunks), 3)  # overlap clamped to 1: [1,2] [2,3] [3,4]

    def test_section_header_merged_not_its_own_chunk(self):
        text = "Q.1 - Q.2 Carry ONE mark Each\n" + make_paper(2)
        chunks = chunk_text(text, max_chars=10_000, max_blocks=1)
        self.assertEqual(len(chunks), 2)
        self.assertIn("Carry ONE mark", chunks[0])
        self.assertIn("(D) delta 1", chunks[0])

    def test_preamble_not_dropped(self):
        text = "What is unnumbered?\n(A) w\n(B) x\n(C) y\n(D) z\n" + make_paper(2)
        joined = "\n".join(chunk_text(text, max_chars=10_000, max_blocks=1))
        self.assertIn("What is unnumbered?", joined)

    def test_math_styled_letters_plain_but_math_symbols_kept(self):
        from mcq_app.services.chunking import normalize_layout

        out = normalize_layout("(A) The relation 𝑡𝑒𝑎𝑚 is NOT in BCNF; x² + √2 − 1 ≤ ∞")
        self.assertIn("relation team is", out)
        self.assertIn("x² + √2 − 1 ≤ ∞", out)
