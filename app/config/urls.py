from django.http import HttpResponse
from django.urls import path

from accounts import views as accounts


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("", accounts.home),
    path("auth/google/login", accounts.google_login),
    path("auth/google/callback", accounts.google_callback),
    path("auth/logout", accounts.logout_view),
    path("healthz", healthz),
]
