"""V7 도움말(/help). 로그인 없이 보이고, 로그인한 회원은 공통 틀(shell.html)에 '도움말'이 켜진 채로 보인다(양식 자료실과 같은 틀).

그림은 ops/help/capture.py가 가상 학교(manage.py seed_help_demo)로 찍은 web/static/help/<이름>.png.
영상(V8)은 ops/help/record.py가 녹화한 web/static/help/<주제>.mp4(+ 포스터 <주제>.jpg)가 있을 때만 보인다.
"""
from django.conf import settings
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_safe

from .views import _frame

LEGACY_NOTE = "예전 고티처에서 옮겨 온 학교는 일정·복무·게시판 쓰기를 예전 고티처에서 해요. 여기서는 보기만 해요."

# (주제 키, 제목, 단계, [(그림 이름, 그림 설명)], 덧붙임)
TOPICS = [
    ("start", "처음 들어오기", [
        "예전 고티처에서 '새 고티처 이용해 보기'를 누르면 로그인 없이 바로 들어와요.",
        "따로 들어올 때는 아이디·비밀번호를 쓰고 '로그인'을 눌러요.",
        "내 PC라면 '이 PC 기억하기'를 켜 두세요. 1년 동안 다시 묻지 않아요.",
    ], [("start-1", "로그인 화면: 아이디·비밀번호 칸과 이 PC 기억하기")], ""),
    ("today", "오늘 화면 보기", [
        "왼쪽 메뉴에서 '오늘'을 눌러요.",
        "오늘 일정·복무·점심과 내가 낼 것이 한 화면에 모여 있어요.",
        "위 칸의 숫자를 누르면 바뀐 것·낼 것·걷는 것을 바로 봐요.",
    ], [("today-1", "오늘 화면: 바뀐 것·낼 것·걷는 것 숫자와 오늘 일과표")], ""),
    ("schedule", "일정 보기", [
        "왼쪽 메뉴에서 '일정'을 눌러요.",
        "위 탭으로 학교·우리 소속·개인 일정을 바꿔 봐요. 개인 일정은 나만 봐요.",
        "빈칸의 +를 누르고 제목을 쓴 뒤 Enter를 누르면 일정이 들어가요.",
    ], [("schedule-1", "일정 화면: 학교 탭의 이번 주 일정"), ("schedule-2", "개인 탭: 나만 보는 일정")], LEGACY_NOTE),
    ("collect_new", "취합 만들기", [
        "'취합' → '새 취합'을 누르고 제목을 써요.",
        "'한글 양식 올리기'에서 hwp 파일을 올리고 채울 표를 한 번 눌러요.",
        "'만들고 링크 받기'를 누른 뒤 링크나 쪽지 문구를 복사해 메신저로 보내요.",
    ], [("collect_new-1", "한글 양식을 올리고 채울 표를 고른 모습"), ("collect_new-2", "받는 링크와 쪽지 문구 복사 단추")], ""),
    ("collect_send", "취합 내기(받는 선생님)", [
        "메신저로 받은 링크를 열어요. 로그인은 없어요.",
        "내 이름을 쓰고 '찾기'를 눌러요.",
        "칸을 채우고 '제출하기'를 눌러요. 바쁜 분 것은 그분 이름으로 대신 내도 돼요.",
    ], [("collect_send-1", "링크를 연 첫 화면: 이름 찾기"), ("collect_send-2", "내 칸 채우기"),
        ("collect_send-3", "낸 뒤 화면")], ""),
    ("hwp", "한글로 받기", [
        "취합 화면에서 모인 값을 실시간으로 봐요. 안 낸 분 이름을 누르면 독촉 문구가 복사돼요.",
        "아래 '한글로 받기'에서 '한글 파일로 받기'를 눌러요.",
        "올린 양식의 표 칸만 채운 한글 파일을 내려받아요.",
    ], [("hwp-1", "취합 합본: 낸 사람과 값"), ("hwp-2", "한글로 받기: 채운 쪽 미리보기와 내려받기")], ""),
    ("board", "게시판(공지 확인)", [
        "왼쪽 메뉴에서 '게시판'을 눌러요.",
        "공지를 눌러 읽어요.",
        "'확인 필수' 공지는 다 읽고 '확인했어요'를 눌러요.",
    ], [("board-1", "게시판 목록"), ("board-2", "확인 필수 공지와 확인했어요 단추")], LEGACY_NOTE),
    ("meals", "급식", [
        "왼쪽 메뉴 '급식'을 누르고 '이번 주 사진' 탭으로 가요(급식 담당·관리자에게 보여요).",
        "메뉴는 나이스에서 저절로 와요. 매일 할 일은 식판 사진 한 장이에요.",
        "오늘 칸의 '사진 고르기'나 '폰으로 올리기'로 올려요.",
    ], [("meals-1", "급식 화면: 이번 주 메뉴와 오늘 사진 칸")], ""),
    ("settings", "설정(비밀번호·알림)", [
        "왼쪽 아래 '설정'을 눌러요.",
        "'내 정보' 탭에서 비밀번호를 바꿔요.",
        "'알림' 탭에서 알림 묶음 시간을 켜고 꺼요.",
    ], [("settings-1", "내 정보 탭: 비밀번호 바꾸기"), ("settings-2", "알림 탭: 묶음 시간")], ""),
    ("feedback", "의견 보내기", [
        "화면 위 '의견 보내기'를 눌러요.",
        "불편한 점이나 바라는 점을 한 줄 쓰고 '보내기'를 눌러요.",
    ], [("feedback-1", "의견 보내기 창")], ""),
]


def _video(key):
    return (settings.STATICFILES_DIRS[0] / "help" / f"{key}.mp4").is_file()


@require_safe
def page(request):
    topics = [{"key": k, "title": t, "steps": steps, "shots": shots, "note": note, "video": _video(k)}
              for k, t, steps, shots, note in TOPICS]
    r = render(request, "library/help.html", {**_frame(request, page="help"), "topics": topics})
    if request.user.is_authenticated:  # 로그인한 틀에는 내 이름·공간 주소가 있다(양식 자료실과 같다)
        add_never_cache_headers(r)
    return r
