from django.http import HttpResponse
from django.urls import path

from accounts import views as accounts
from spaces import views as spaces


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("", spaces.home),
    path("new/school", spaces.pick_school),
    path("new/class", spaces.new_class),
    path("s/<str:space_token>/", spaces.grade),
    path("auth/google/login", accounts.google_login),
    path("auth/google/callback", accounts.google_callback),
    path("auth/logout", accounts.logout_view),
    path("healthz", healthz),
]
