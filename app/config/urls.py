import os

from django.http import HttpResponse
from django.urls import include, path
from django.views.generic import TemplateView

from accounts import views as accounts
from collect import bell, export, lists, push, submit
from collect import views as collect
from library import views as library
from spaces import views as spaces


# 무중단 교체(ops/rollout.sh)가 내릴 컨테이너에 이 파일을 만들면 503을 줘서 Traefik이 먼저 뺀다
DRAIN_FILE = "/tmp/gt-drain"


def healthz(request):
    if os.path.exists(DRAIN_FILE):
        return HttpResponse("draining", content_type="text/plain", status=503)
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
    path("s/<str:space_token>/bell", bell.index),
    path("s/<str:space_token>/bell/<str:key>", bell.read),
    path("s/<str:space_token>/push", push.subscribe),
    path("s/<str:space_token>/push/off", push.unsubscribe),
    path("s/<str:space_token>/push/mine", push.mine),
    path("s/<str:space_token>/c/", lists.index),
    path("s/<str:space_token>/c/new", collect.new),
    path("s/<str:space_token>/c/new/preview", collect.preview),
    path("s/<str:space_token>/c/new/hwp/<str:key>", collect.hwp_fields),  # E6 칸 확인
    path("s/<str:space_token>/c/<str:key>/", collect.detail),
    path("s/<str:space_token>/c/<str:key>/live", collect.live),
    path("s/<str:space_token>/c/<str:key>/due", collect.due),
    path("s/<str:space_token>/c/<str:key>/lock", collect.lock),
    path("s/<str:space_token>/c/<str:key>/delete", collect.delete),
    path("s/<str:space_token>/c/<str:key>/export.csv", export.export_csv),
    path("s/<str:space_token>/c/<str:key>/copy", export.copy),
    path("s/<str:space_token>/c/<str:key>/footer", export.footer),
    path("s/<str:space_token>/c/<str:key>/again", lists.again),
    path("c/<str:link_token>", submit.pick),
    path("c/<str:link_token>/outside", submit.outside),
    path("c/<str:link_token>/u/<str:key>", submit.unit),
    path("j/<str:invite_token>", spaces.invite),
    path("join", spaces.join),
    path("start", accounts.home),  # 시작 화면(구글로 시작하기). 로그인 전 / 는 소개 페이지
    path("auth/google/login", accounts.google_login),
    path("auth/google/callback", accounts.google_callback),
    path("auth/logout", accounts.logout_view),
    path("privacy", TemplateView.as_view(template_name="legal/privacy.html")),
    path("terms", TemplateView.as_view(template_name="legal/terms.html")),
    path("s/<str:space_token>/", include("hwpfiles.urls")),  # E2 한글 파일 올리기·상태
    path("s/<str:space_token>/", include("today.urls")),  # G1 간이 오늘
    path("forms", library.index),  # G4 양식 자료실(로그인 없이)
    path("forms/<str:slug>", library.index),
    path("forms/<str:slug>/download", library.download),
    path("sitemap.xml", library.sitemap),
    path("healthz", healthz),
]
