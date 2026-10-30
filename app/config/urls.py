from django.http import HttpResponse
from django.urls import path
from django.views.generic import TemplateView

from accounts import views as accounts
from collect import submit
from collect import views as collect
from spaces import views as spaces


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("", spaces.home),
    path("new/school", spaces.pick_school),
    path("new/class", spaces.new_class),
    path("s/<str:space_token>/", spaces.grade),
    path("s/<str:space_token>/members", spaces.members),
    path("s/<str:space_token>/invite", spaces.new_invite),
    path("s/<str:space_token>/made", spaces.made),
    path("s/<str:space_token>/welcome", spaces.welcome),
    path("s/<str:space_token>/c/new", collect.new),
    path("s/<str:space_token>/c/new/preview", collect.preview),
    path("s/<str:space_token>/c/<str:key>/", collect.detail),
    path("s/<str:space_token>/c/<str:key>/live", collect.live),
    path("s/<str:space_token>/c/<str:key>/due", collect.due),
    path("c/<str:link_token>", submit.pick),
    path("c/<str:link_token>/outside", submit.outside),
    path("c/<str:link_token>/u/<str:key>", submit.unit),
    path("j/<str:invite_token>", spaces.invite),
    path("join", spaces.join),
    path("auth/google/login", accounts.google_login),
    path("auth/google/callback", accounts.google_callback),
    path("auth/logout", accounts.logout_view),
    path("privacy", TemplateView.as_view(template_name="legal/privacy.html")),
    path("terms", TemplateView.as_view(template_name="legal/terms.html")),
    path("healthz", healthz),
]
