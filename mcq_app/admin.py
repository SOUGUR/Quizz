from django.contrib import admin

from .models import Option, Question, QuestionPaper


class OptionInline(admin.TabularInline):
    model = Option
    extra = 0
    max_num = 4


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ("order", "question_number", "question_text")
    show_change_link = True


@admin.register(QuestionPaper)
class QuestionPaperAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "page_count", "llm_provider", "created_at", "finished_at")
    list_filter = ("status", "llm_provider")
    search_fields = ("title",)
    readonly_fields = ("created_at", "started_at", "finished_at", "ocr_text")
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    inlines = [OptionInline]
    list_display = ("question_number", "question_paper", "question_text_short")
    list_filter = ("question_paper",)
    search_fields = ("question_text",)

    @admin.display(description="Question Text")
    def question_text_short(self, obj):
        return obj.question_text[:100]


admin.site.register(Option)
