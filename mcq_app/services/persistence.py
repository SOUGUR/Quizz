"""Database persistence for extracted questions."""

from django.db import transaction

from ..models import Option, Question


def replace_questions(paper, validated_questions):
    """
    Atomically replace all questions of `paper`.

    Delete + insert inside one transaction means re-processing a paper can
    never leave duplicates or a half-written result: readers see either the
    old set or the new set.
    """
    with transaction.atomic():
        Question.objects.filter(question_paper=paper).delete()
        questions = Question.objects.bulk_create(
            [
                Question(
                    question_paper=paper,
                    question_number=vq.question_number[:50],
                    question_text=vq.question_text,
                    order=index,
                    fingerprint=vq.fingerprint,
                )
                for index, vq in enumerate(validated_questions, start=1)
            ]
        )
        Option.objects.bulk_create(
            [
                Option(question=question, option_label=label, text=text)
                for question, vq in zip(questions, validated_questions)
                for label, text in vq.options.items()
            ]
        )
    return len(questions)
