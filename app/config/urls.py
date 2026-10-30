from django.http import HttpResponse
from django.urls import path


def home(request):
    return HttpResponse("hello 고티처", content_type="text/plain; charset=utf-8")


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("", home),
    path("healthz", healthz),
]
