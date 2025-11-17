from django.db import models
from institute.models import Institute, Role
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
import uuid

User = get_user_model()

def generate_class_code():
    return uuid.uuid4().hex[:8]


class Class(models.Model):
    """Represents a classroom managed by a teacher."""
    institute = models.ForeignKey(Institute, on_delete=models.CASCADE, related_name="classes")
    name = models.CharField(max_length=100)
    class_code = models.CharField(max_length=8, unique=True, default=generate_class_code)
    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="classes_taught",
        limit_choices_to={"profile__role": Role.TEACHER},
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Classes"

    def __str__(self):
        return f"{self.name} ({self.class_code})"

class Enrollment(models.Model):
    """Links a student to a class."""
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="enrollments",
        limit_choices_to={"profile__role": Role.STUDENT},
    )
    class_in = models.ForeignKey(Class, on_delete=models.CASCADE, related_name="enrollments")
    joined_at = models.DateTimeField(auto_now_add=True)
    approved = models.BooleanField(default=False)

    class Meta:
        unique_together = ("student", "class_in")
        verbose_name_plural = "Enrollments"

    def __str__(self):
        return f"{self.student.username} → {self.class_in.name}"