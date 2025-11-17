from django.db import models
from institute.models import Institute
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator

User = get_user_model()

class Paper(models.Model):
    """Metadata and file for a question paper."""
    institute = models.ForeignKey(Institute, on_delete=models.CASCADE, related_name="papers")
    uploader = models.ForeignKey(User, on_delete=models.CASCADE, related_name="uploaded_papers")
    title = models.CharField(max_length=255)
    upload_date = models.DateTimeField(auto_now_add=True)
    original_image = models.ImageField(upload_to="paper_images/")
    is_shared = models.BooleanField(default=False)
    is_processed = models.BooleanField(default=False)

    def __str__(self):
        return self.title
    
class Type(models.TextChoices):
    MCQ = "MCQ", _("Multiple Choice")
    TEXT = "TEXT", _("Short Answer")
    MATH = "MATH", _("Math Problem")

class Question(models.Model):
    """Stores OCR-processed question data."""
    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="questions")
    question_text = models.TextField()
    marks = models.FloatField(default=1.0)
    negative_marks = models.FloatField(default=0.0)
    image_segment = models.ImageField(upload_to="question_segments/", null=True, blank=True)
    type = models.CharField(max_length=10, choices=Type.choices, default=Type.MCQ)
    correct_answer_index = models.IntegerField(null=True, blank=True)
    correct_answer_text = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Q{self.id} - {self.paper.title}"


class Option(models.Model):
    """Multiple choice options for a question."""
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    option_text = models.TextField()
    index = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(25)],
        help_text=_("0=A, 1=B, etc."),
    )

    class Meta:
        unique_together = ("question", "index")
        ordering = ["index"]

    def __str__(self):
        return f"Option {self.index} for Q{self.question.id}"
    


class Attempt(models.Model):
    """Represents one student's attempt of a paper (can retry multiple times)."""
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attempts")
    paper = models.ForeignKey('Paper', on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    total_score = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("student", "paper", "attempt_number")
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.student.username} - {self.paper.title} (Attempt {self.attempt_number})"

    def calculate_score(self):
        """Recalculate total score from all answers."""
        total = 0.0
        for ans in self.answers.all():
            total += ans.score
        self.total_score = total
        self.save()
        return total
    

class Answer(models.Model):
    """Stores a student's answer for a specific question in a specific attempt."""
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey('Question', on_delete=models.CASCADE, related_name="answers")
    
    # For MCQ: stores selected option index (e.g., 0=A, 1=B)
    selected_option_index = models.IntegerField(null=True, blank=True)
    
    # For TEXT: stores student's written answer
    written_answer = models.TextField(null=True, blank=True)
    
    # Auto-evaluated score (based on marking rules)
    score = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("attempt", "question")

    def __str__(self):
        return f"Answer by {self.attempt.student.username} - Q{self.question.id}"

    def evaluate(self):
        """Simple evaluation logic for MCQ/Text."""
        q = self.question
        if q.type == "MCQ":
            if self.selected_option_index == q.correct_answer_index:
                self.score = q.marks
            else:
                self.score = -q.negative_marks
        elif q.type == "TEXT":
            # TODO: NLP similarity or keywords
            if q.correct_answer_text and q.correct_answer_text.strip().lower() == (self.written_answer or "").strip().lower():
                self.score = q.marks
            else:
                self.score = -q.negative_marks
        self.save()