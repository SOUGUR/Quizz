from django.db import models
from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator
import uuid

User = get_user_model()

class Role(models.TextChoices):
    TEACHER = "TEACHER", _("Teacher")
    STUDENT = "STUDENT", _("Student")
    MANAGEMENT = "MANAGEMENT", _("Management")
    ADMINISTRATOR = "ADMINISTRATOR", _("Administrator")

    

class Institute(models.Model):
    """Represents an educational institution."""
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=20, unique=True) 
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
class Profile(models.Model):
    """Extends User with role and institute."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Role.choices)
    institute = models.ForeignKey(Institute, on_delete=models.CASCADE, related_name="profiles")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"