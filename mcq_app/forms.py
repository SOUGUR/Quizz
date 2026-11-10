import os

from django import forms
from django.conf import settings

from .models import QuestionPaper

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

# Magic-byte signatures. Checking the real file header (not the browser's
# Content-Type or the filename) stops renamed/disguised files.
SIGNATURES = {
    ".pdf": [b"%PDF-"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".tif": [b"II*\x00", b"MM\x00*"],
    ".tiff": [b"II*\x00", b"MM\x00*"],
    ".bmp": [b"BM"],
}


class UploadPaperForm(forms.ModelForm):
    class Meta:
        model = QuestionPaper
        fields = ["title", "upload_file"]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Enter paper title"}
            ),
            "upload_file": forms.ClearableFileInput(
                attrs={"class": "form-control", "accept": ",".join(sorted(ALLOWED_EXTENSIONS))}
            ),
        }
        labels = {
            "title": "Paper Title (e.g., GATE 2023 CSE)",
            "upload_file": "Select PDF or Image File",
        }

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Please enter a title.")
        return title

    def clean_upload_file(self):
        f = self.cleaned_data["upload_file"]
        ext = os.path.splitext(f.name)[1].lower()

        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"Unsupported file type '{ext or 'none'}'. Allowed: PDF, PNG, JPG, TIFF, BMP."
            )

        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if f.size > max_bytes:
            raise forms.ValidationError(
                f"File is too large ({f.size / 1024 / 1024:.1f} MB). "
                f"Maximum is {settings.MAX_UPLOAD_SIZE_MB} MB."
            )
        if f.size == 0:
            raise forms.ValidationError("The uploaded file is empty.")

        f.seek(0)
        header = f.read(16)
        f.seek(0)
        if not any(header.startswith(sig) for sig in SIGNATURES[ext]):
            raise forms.ValidationError(
                "The file content does not match its extension. Please upload a real PDF or image."
            )

        if ext == ".pdf":
            self._validate_pdf(f)
        else:
            self._validate_image(f)
        f.seek(0)
        return f

    @staticmethod
    def _validate_pdf(f):
        import pymupdf as fitz

        try:
            data = f.read()
            with fitz.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise forms.ValidationError("Password-protected PDFs are not supported.")
                if doc.page_count == 0:
                    raise forms.ValidationError("The PDF has no pages.")
                if doc.page_count > settings.MAX_PDF_PAGES:
                    raise forms.ValidationError(
                        f"The PDF has {doc.page_count} pages; the maximum is {settings.MAX_PDF_PAGES}."
                    )
        except forms.ValidationError:
            raise
        except Exception:
            raise forms.ValidationError("The PDF appears to be corrupted and cannot be opened.")
        finally:
            f.seek(0)

    @staticmethod
    def _validate_image(f):
        from PIL import Image

        try:
            with Image.open(f) as img:
                img.verify()
        except Exception:
            raise forms.ValidationError("The image appears to be corrupted and cannot be opened.")
        finally:
            f.seek(0)
