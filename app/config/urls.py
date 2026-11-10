import os

from django.conf import settings
from django.http import HttpResponse
from django.templatetags.static import static
from django.urls import include, path
from django.views.decorators.http import require_safe
from django.views.generic import RedirectView, TemplateView

from accounts import device
from accounts import views as accounts
from collect import bell, export, lists, push, submit
from collect import views as collect
from community import views as community
from library import views as library
from meals import phone, tv
from notices import views as notices
from partners import views as partners
from repairs import vendor
from spaces import attendance as attendance_page
from spaces import plan as school_plan
from spaces import school as school_page
from spaces import summary as school_summary
from spaces import views as spaces


# 무중단 교체(ops/rollout.sh)가 내릴 컨테이너에 이 파일을 만들면 503을 줘서 Traefik이 먼저 뺀다
DRAIN_FILE = "/tmp/gt-drain"


def healthz(request):
    if os.path.exists(DRAIN_FILE):
        return HttpResponse("draining", content_type="text/plain", status=503)
    return HttpResponse("ok", content_type="text/plain")


ROBOTS_OFF = ("/s/", "/c/", "/v/", "/tv/", "/m/", "/n/", "/j/", "/community")  # 교내·받는 사람·비밀 링크·회원 전용


@require_safe
def robots(request):
    lines = ["User-agent: *", "Allow: /", *(f"Disallow: {p}" for p in ROBOTS_OFF), f"Sitemap: {settings.SITE_URL}/sitemap.xml"]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


urlpatterns = [
    path("", spaces.home),
    path("new/school", spaces.pick_school),
    path("new/class", spaces.new_class),
    path("s/<str:space_token>/", spaces.grade),
    path("s/<str:space_token>/school", school_page.school),
    path("s/<str:space_token>/school/summary", school_summary.sheet),
    path("s/<str:space_token>/school/me", school_page.edit_me),
    path("s/<str:space_token>/school/members/<str:key>/manager", school_page.set_manager),
    path("s/<str:space_token>/school/members/<str:key>/password", school_page.temp_password),  # P1 임시 비밀번호
    path("s/<str:space_token>/school/members/<str:key>/attendance", attendance_page.set_writer),
    path("s/<str:space_token>/school/attendance", attendance_page.page),
    path("s/<str:space_token>/school/attendance/preview", attendance_page.preview),
    path("s/<str:space_token>/school/attendance/clear", attendance_page.clear),
    path("s/<str:space_token>/school/duties", school_page.duties),
    path("s/<str:space_token>/school/duties/preview", school_page.duties_preview),
    path("s/<str:space_token>/school/arrange", school_page.arrange),  # Q2 순서·소속 바꾸기
    path("s/<str:space_token>/members", spaces.members),
    path("s/<str:space_token>/members/<str:key>/out", spaces.remove_member),
    path("s/<str:space_token>/settings", device.settings_page),
    path("s/<str:space_token>/settings/remember", device.remember),
    path("s/<str:space_token>/settings/slots", device.slots),
    path("s/<str:space_token>/settings/days", device.days),
    path("s/<str:space_token>/settings/logout-all", device.logout_all),
    path("s/<str:space_token>/settings/me", spaces.update_me),
    path("s/<str:space_token>/settings/password", device.change_password),  # P1 아이디 회원
    path("s/<str:space_token>/settings/leave", spaces.leave),
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
    path("s/<str:space_token>/c/new/aud", collect.aud_summary),  # H4 받는 곳 요약
    path("s/<str:space_token>/c/new/hwp/<str:key>", collect.hwp_fields),  # E6 칸 확인
    path("s/<str:space_token>/c/<str:key>/", collect.detail),
    path("s/<str:space_token>/c/<str:key>/live", collect.live),
    path("s/<str:space_token>/c/<str:key>/due", collect.due),
    path("s/<str:space_token>/c/<str:key>/lock", collect.lock),
    path("s/<str:space_token>/c/<str:key>/link", collect.relink),
    path("s/<str:space_token>/c/<str:key>/delete", collect.delete),
    path("s/<str:space_token>/c/<str:key>/export.csv", export.export_csv),
    path("s/<str:space_token>/c/<str:key>/copy", export.copy),
    path("s/<str:space_token>/c/<str:key>/footer", export.footer),
    path("s/<str:space_token>/c/<str:key>/again", lists.again),
    path("c/<str:link_token>", submit.pick),
    path("c/<str:link_token>/outside", submit.outside),
    path("c/<str:link_token>/find", submit.find),  # H4 학교 공간 이름 찾기
    path("c/<str:link_token>/u/<str:key>", submit.unit),
    path("n/<str:token>", notices.link),  # I2 쪽지 링크
    path("v/", vendor.home),  # K2 업체 화면(기기 쿠키)
    path("v/out", vendor.out),
    path("v/r/<str:key>/state", vendor.state),
    path("v/r/<str:key>/photo", vendor.photo),
    path("v/<str:token>", vendor.enter),
    path("tv/<str:token>", tv.page),  # M2 급식 TV(로그인 없음)
    path("tv/<str:token>/data", tv.data),
    path("tv/<str:token>/photo/<str:ymd>", tv.photo),
    path("m/<str:token>", phone.page),  # M3 폰으로 식판 사진 올리기(로그인 없음)
    path("j/<str:invite_token>", spaces.invite),
    path("join", spaces.join),
    path("start", accounts.home),  # 시작 화면(구글·아이디로 시작하기). 로그인 전 / 는 소개 페이지
    path("auth/login", accounts.id_login),  # P1 아이디 로그인
    path("signup", accounts.signup),  # P1 아이디 가입
    path("account/password", accounts.force_password),  # P1 임시 비밀번호 뒤 바꾸기
    path("auth/google/login", accounts.google_login),
    path("auth/google/callback", accounts.google_callback),
    path("auth/logout", accounts.logout_view),
    path("privacy", TemplateView.as_view(template_name="legal/privacy.html")),
    path("terms", TemplateView.as_view(template_name="legal/terms.html")),
    path("s/<str:space_token>/", include("hwpfiles.urls")),  # E2 한글 파일 올리기·상태
    path("s/<str:space_token>/", include("today.urls")),  # G1 간이 오늘
    path("s/<str:space_token>/", include("notices.urls")),  # I1 공지
    path("s/<str:space_token>/", include("repairs.urls")),  # K1 수리 요청
    path("s/<str:space_token>/", include("meals.urls")),  # M1 급식 입력
    path("forms", library.index),  # G4 양식 자료실(로그인 없이)
    path("forms/<str:slug>", library.index),
    path("forms/<str:slug>/download", library.download),
    path("forms/<str:slug>/like", library.like),  # Q9 양식 추천
    path("partners", partners.index),  # N1 협력업체(로그인 없이)
    path("partners/suggest", partners.suggest),
    path("partners/<str:slug>", partners.detail),
    path("partners/<str:slug>/review", partners.review),  # N2 후기(내 학교의 이 업체 후기 하나만 다룬다)
    path("partners/<str:slug>/review/delete", partners.review_delete),
    path("school-plan", school_plan.page),  # O1 소개는 로그인 없이, 신청은 계정 회원
    path("school-plan/q/<str:key>", school_plan.quote),
    path("community", community.index),  # Q10 커뮤니티(계정 회원, 전국 하나)
    path("community/nick", community.nick),
    path("community/new", community.new),
    path("community/c/<str:key>/delete", community.comment_delete),
    path("community/c/<str:key>/report", community.comment_report),
    path("community/<str:key>", community.detail),
    path("community/<str:key>/comment", community.comment),
    path("community/<str:key>/delete", community.post_delete),
    path("community/<str:key>/report", community.post_report),
    path("sitemap.xml", library.sitemap),
    path("robots.txt", robots),
    path("favicon.ico", RedirectView.as_view(url=static("favicon.ico"))),  # 링크 태그를 안 읽는 브라우저·검색 로봇
    path("healthz", healthz),
]
