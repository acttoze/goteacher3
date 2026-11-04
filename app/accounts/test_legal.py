"""A6 개인정보처리방침·이용약관 게시본, B6 동학년 화면의 새로 모으기 링크."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from spaces.models import Member, School, Space


class LegalPageTests(TestCase):
    def test_public_pages_filled(self):
        for url in ("/privacy", "/terms"):
            r = Client().get(url)
            self.assertEqual(r.status_code, 200, url)
            body = r.content.decode()
            for want in ("정이점", "admin@goteacher.xyz", "시행일: 2026년 12월 14일"):
                self.assertIn(want, body, url)
            for bad in ('class="blank"', 'class="draft"', "초안", "[", "티로드"):
                self.assertNotIn(bad, body, url)

    def test_privacy_keeps_retention_and_processor(self):
        body = Client().get("/privacy").content.decode()
        self.assertIn("Oracle(Oracle Cloud Infrastructure, 춘천 리전)", body)
        self.assertIn("마지막 제출·수정) 후 1년", body)
        self.assertIn("만든 날부터 3년", body)
        self.assertIn("(7일)", body)

    def test_home_links_legal(self):
        body = Client().get("/").content.decode()
        self.assertIn('href="/privacy"', body)
        self.assertIn('href="/terms"', body)


class O1DocumentTests(TestCase):
    """처리방침·약관이 지금 코드와 같은 값을 말한다. 코드 상수와 문장을 함께 본다(바뀌면 여기서 걸린다)."""

    def setUp(self):
        self.privacy = Client().get("/privacy").content.decode()
        self.terms = Client().get("/terms").content.decode()

    def test_retention_matches_code(self):
        from hwpfiles import retention as r
        from accounts.management.commands.cleanup_sessions import GUEST_IDLE
        for delta, days, phrase in (
            (r.ATTENDANCE_DAYS, 7, "복무: 그날부터 7일"),
            (r.REPAIR_DONE, 90, "완료 90일"),
            (r.REPAIR_MAX, 365, "그 밖의 요청은 마지막으로 바뀐 날부터 1년"),
            (r.VENDOR_DAYS, 30, "접속 기록은 30일"),
            (r.VENDOR_DAYS, 30, "해제되거나 기간이 끝난 뒤 30일"),
            (r.MEAL_PHOTO_DAYS, 30, "급식 날짜부터 30일"),
            (r.MEMBER_LOG_AGE, 365, "고친 기록(이력): 1년"),
            (r.SUGGESTION_AGE, 365, "확인 전이면 1년"),
            (r.NOTICE_AFTER_PLAN, 365, "학교 플랜이 끝난 날부터 1년"),
            (r.PLAN_REQUEST_AGE, 365, "처리한 날부터 1년"),
            (r.RETENTION, 365, "마지막 제출·수정) 후 1년"),
            (r.MAX_AGE, 3 * 365, "만든 날부터 3년"),
            (GUEST_IDLE, 30, "30일 넘게"),
            (r.READ_BELL_AGE, 90, "읽은 알림은 90일"),
            (r.UNREAD_BELL_AGE, 180, "안 읽은 알림은 180일"),
        ):
            self.assertEqual(delta.days, days, phrase)
            self.assertIn(phrase, self.privacy)

    def test_privacy_names_what_is_handled(self):
        for word in ("유형·시간", "사유", "사진 정보(촬영 위치", "요청한 사람 이름", "봄 기록", "첨부", "식판 사진", "TV", "직책은 보이지", "이름·소속·직책",
                     "업체 추천", "학교 플랜 신청", "담당자 이름·직위·연락 메일", "최대 7일", "국외", "웹 푸시", "jsDelivr", "대조",
                     "화면·사진을 열어 본 기록", "그날·이번 주 급식, 식판 사진, 위생·표어, 학교 이름", "시간, 입력한 사람", "쓴 사람 이름·소속", "웹 푸시 구독 정보: 기간을 정해 두지 않고"):
            self.assertIn(word, self.privacy, word)

    def test_merge_and_account_deletion_disclosed(self):
        from accounts.management.commands.delete_account import GONE_NAME
        self.assertIn("옛 모으기도 학교 공간 관리자에게 열립니다", self.privacy)
        self.assertIn(GONE_NAME, self.privacy)
        self.assertIn("이름이 든 입장 알림도 함께 지웁니다", self.privacy)

    def test_no_stale_claims(self):
        for stale in ("접속 기록: 1년", "서비스 안에서 회원 탈퇴를", "브라우저 종류", "접속 기록 보관과", "CSAP"):
            self.assertNotIn(stale, self.privacy, stale)
        self.assertNotIn("언제든 탈퇴할 수 있습니다", self.terms)

    def test_terms_cover_school_plan_without_invented_money(self):
        for word in ("학교 플랜", "기간", "해지", "읽기", "견적서에 따릅니다"):
            self.assertIn(word, self.terms, word)
        self.assertNotRegex(self.terms, r"\d+\s*(만\s*)?원")
        self.assertNotRegex(self.terms, r"환불.{0,20}(\d+일|전액|일할)")


class GradeNewCollectLinkTests(TestCase):
    def test_grade_has_new_collect_link(self):
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2"])
        p.start()
        self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        space = Space.objects.create(school=school, grade=4, name="새솔초 4학년")
        u = get_user_model().objects.create(username="google:1", first_name="송민재")
        Member.objects.create(space=space, user=u, name="송민재", cls="4-1")
        self.client.force_login(u)
        r = self.client.get(f"/s/{space.token}/")
        self.assertContains(r, f'href="/s/{space.token}/c/new"')
        self.assertContains(r, "새로 모으기")
