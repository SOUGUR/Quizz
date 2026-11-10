"""Regression tests: platform pages must never crash for anonymous users or
for logged-in users without a Profile (e.g. a fresh superuser)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

PROTECTED = ["create_classroom", "join_class", "approve_class", "create_profile"]


class AnonymousAccessTests(TestCase):
    def test_protected_pages_redirect_to_login(self):
        for name in PROTECTED:
            url = reverse(name)
            resp = self.client.get(url)
            self.assertRedirects(
                resp, f"{reverse('login')}?next={url}", msg_prefix=name, fetch_redirect_response=False
            )

    def test_login_page_and_home_render(self):
        self.assertContains(self.client.get(reverse("login")), "Log in")
        home = self.client.get(reverse("home"))
        self.assertContains(home, reverse("login"))
        self.assertContains(home, reverse("paper_list"))


class LoggedInWithoutProfileTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@example.com", "pass12345")

    def test_login_flow(self):
        resp = self.client.post(reverse("login"), {"username": "admin", "password": "pass12345"})
        self.assertRedirects(resp, reverse("home"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("home")), "Log out (admin)")

    def test_pages_do_not_crash_without_profile(self):
        self.client.force_login(self.user)
        for name in ["create_classroom", "join_class"]:
            resp = self.client.get(reverse(name))
            self.assertRedirects(resp, reverse("home"), msg_prefix=name, fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("create_profile")).status_code, 200)

    def test_logout_is_post(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse("logout"))
        self.assertRedirects(resp, reverse("home"), fetch_redirect_response=False)
        self.assertNotContains(self.client.get(reverse("home")), "Log out")
