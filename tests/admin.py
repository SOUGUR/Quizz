from django.contrib import admin
from .models import Paper,Question, Option, Attempt, Answer


admin.site.register(Paper)
admin.site.register(Attempt)
admin.site.register(Answer)
admin.site.register(Option)
admin.site.register(Question)