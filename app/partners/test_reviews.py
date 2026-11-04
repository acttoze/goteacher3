"""N2 배지 후기: 응답 어디에도 작성자·학교가 없음, 손님 거부, 학교당 1건, 입력 검사, 집계, 운영자 내리기.

표식은 이름·학교에 심어 응답 전체(본문·헤더)를 뒤진다. 동시 제출은 PostgreSQL에서만 돈다.
"""
import threading
from datetime import timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import CommandError, call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.models import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from config.test_ratelimit import RL_CACHES
from hwpfiles.retention import purge
from spaces.merge import open_school
from spaces.models import Member, School, Space

from .management.commands import partners as partners_command
from .models import BADGES, CATS, Partner, Review, ReviewAction, school_labels

User = get_user_model()
WHO = "송표식재"
WHERE = "표식학교초등학교"
TAKEN = "우리 학교 선생님이 이미 후기를 남겼어요"
WARNING = "이 후기는 학교 밖에 공개됩니다"
REVIEW_OPEN = {"PARTNER_REVIEW_MIN_DAYS": 0, "PARTNER_REVIEW_MIN_GOOGLE": 1, "PARTNER_REVIEW_PER_DAY": 1000}  # 자격·한도는 아래 전용 테스트가 실제 값으로 본다


def partner(name="참된인쇄", **kw):
    kw.setdefault("cat", CATS[0])
    kw.setdefault("area", "전국")
    kw.setdefault("desc", "소개 글")
    kw.setdefault("is_public", True)
    return Partner.objects.create(name=name, **kw)


def teacher(space, sub, name, cls="4-1"):
    user = User.objects.create(username=f"google:{sub}", first_name=name, email=f"{sub}@example.com")
    member = Member.objects.create(space=space, user=user, name=name, cls=cls)
    c = Client()
    c.force_login(user)
    return member, c


def new_school(n, name, atpt="B10"):
    school = School.objects.create(neis_atpt=atpt, neis_code=f"75{n:05d}", name=name)
    return school, Space.objects.create(school=school, grade=4, name=f"{name} 4학년")


@override_settings(CACHES=RL_CACHES, **REVIEW_OPEN)
class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.p = partner()
        self.school, self.space = new_school(1, WHERE)
        self.me, self.mc = teacher(self.space, "1", WHO)
        self.mate, self.matec = teacher(self.space, "2", "윤동료님", "4-2")
        self.other_school, other_space = new_school(2, "다른학교초등학교", "J10")
        self.other, self.oc = teacher(other_space, "3", "강다른샘")
        self.anon = Client()
        self.guest_user = User.objects.create(username="guest:abc", first_name="손님")
        Member.objects.create(space=self.space, user=self.guest_user, name="박손님샘", cls="4-3")
        self.gc = Client()
        self.gc.force_login(self.guest_user)
        self.url = f"/partners/{quote(self.p.slug)}/review"
        self.detail = f"/partners/{quote(self.p.slug)}"

    def post(self, client=None, badges=("0", "1"), text="", url=None):
        """내고, 운영자가 한 줄을 확인한 상태로 둔다(대기 동작은 TextStateTests)."""
        r = (client or self.mc).post(url or self.url, {"badges": list(badges), "text": text})
        Review.objects.filter(text_state=Review.TextState.PENDING).update(text_state=Review.TextState.OK)
        return r

    def made(self, school=None, member=None, badges=(0, 1), text="", p=None, **kw):
        area, level = school_labels(school or self.school)
        return Review.objects.create(partner=p or self.p, school=school or self.school, member=member, badges=list(badges), text=text,
                                     text_state=Review.TextState.OK, area_label=area, level_label=level, **kw)


class AccessTests(Base):
    def test_anonymous_goes_to_login_and_comes_back(self):
        for r in (self.anon.get(self.url), self.post(self.anon), self.anon.post(self.url + "/delete")):
            self.assertEqual(r.status_code, 302)
            self.assertIn("/start?next=", r["Location"])
            self.assertIn("review", r["Location"])
        self.assertEqual(Review.objects.count(), 0)

    def test_guest_refused(self):
        self.assertEqual(self.gc.get(self.url).status_code, 403)
        self.assertEqual(self.post(self.gc).status_code, 403)
        self.assertEqual(self.gc.post(self.url + "/delete").status_code, 403)
        self.assertEqual(Review.objects.count(), 0)

    def test_google_user_without_space_refused(self):
        c = Client()
        c.force_login(User.objects.create(username="google:9", first_name="혼자"))
        r = c.get(self.url)
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "학교", status_code=403)
        self.assertEqual(self.post(c).status_code, 403)
        self.assertEqual(Review.objects.count(), 0)

    def test_private_or_missing_partner_404(self):
        hidden = partner("숨은업체", is_public=False)
        for path in (f"/partners/{quote(hidden.slug)}/review", "/partners/없는업체/review"):
            self.assertEqual(self.mc.get(path).status_code, 404)
            self.assertEqual(self.post(url=path).status_code, 404)
            self.assertEqual(self.mc.post(path + "/delete").status_code, 404)

    def test_csrf_enforced(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.me.user)
        self.assertEqual(self.post(c).status_code, 403)

    def test_get_only_for_delete(self):
        self.assertEqual(self.mc.get(self.url + "/delete").status_code, 405)


class WriteTests(Base):
    def test_form_shows_warning_with_my_labels_and_all_badges(self):
        r = self.mc.get(self.url)
        self.assertContains(r, WARNING)
        self.assertContains(r, "고티처 선생님 · 서울 · 초등")
        self.assertContains(r, "학생·교직원 이름, 비방은 적지 마세요")
        for b in BADGES:
            self.assertContains(r, b)
        self.assertNotContains(r, "티로드")

    def test_saves_labels_from_school_not_from_form(self):
        r = self.mc.post(self.url, {"badges": ["2", "5"], "text": " 안전 교육이 좋았어요 ", "area_label": "가짜", "level_label": "가짜",
                                    "is_hidden": "on", "school": self.other_school.pk, "member": self.other.pk})
        self.assertEqual(r.status_code, 303)
        rv = Review.objects.get()
        self.assertEqual((rv.partner, rv.school, rv.member), (self.p, self.school, self.me))
        self.assertEqual((rv.badges, rv.text, rv.area_label, rv.level_label, rv.is_hidden), ([2, 5], "안전 교육이 좋았어요", "서울", "초등", False))
        self.assertEqual(rv.author_user, self.me.user)
        self.assertRegex(rv.report_no, r"^R-[A-Z2-9]{8}$")

    def test_labels_table(self):
        cases = [("J10", "새솔초등학교", "경기", "초등"), ("T10", "제주여자중학교", "제주", "중등"), ("I10", "세종여자고등학교", "세종", "고등"),
                 ("V10", "외국학교", "", ""), ("B10", "한빛학교", "", ""), ("B10", "서울맹학교", "", ""), ("J10", "새솔중고등학교", "", ""),
                 ("J10", "새솔초중학교", "", ""), ("J10", "새솔통합학교", "", "")]
        for atpt, name, area, level in cases:
            self.assertEqual(school_labels(School(neis_atpt=atpt, name=name)), (area, level))

    def test_unknown_labels_are_left_out(self):
        school, space = new_school(3, "한빛학교", "V10")
        _, c = teacher(space, "7", "한빛샘님")
        self.assertContains(c.get(self.url), "고티처 선생님")
        self.assertEqual(self.post(c).status_code, 303)
        r = self.anon.get(self.detail)
        self.assertContains(r, "고티처 선생님")
        self.assertContains(r, '고티처 선생님 · <span class="num">')  # 뒤에는 월 단위 날짜만

    def test_badge_rules(self):
        bad = [(), ("0", "1", "2", "3", "4", "5"), ("8",), ("-1",), ("나쁨",), ("0", "0"), ("٣",), ("1.0",), ("", ), ("0", "99")]
        for badges in bad:
            self.assertEqual(self.post(badges=badges).status_code, 400, badges)
        self.assertEqual(Review.objects.count(), 0)
        self.assertEqual(self.post(badges=("0", "1", "2", "3", "7")).status_code, 303)
        self.assertEqual(Review.objects.get().badges, [0, 1, 2, 3, 7])

    def test_no_rating_or_bad_badge_path(self):
        self.mc.post(self.url, {"badges": ["0"], "r": "1", "a": "1", "star": "1", "rating": "1"})
        rv = Review.objects.get()
        names = {f.name for f in Review._meta.get_fields()}
        self.assertFalse(names & {"r", "a", "star", "stars", "rating", "score"})
        self.assertEqual(rv.badges, [0])
        for page in (self.mc.get(self.url), self.anon.get(self.detail), self.anon.get("/partners")):
            html = page.content.decode()
            self.assertNotIn("★", html)
            self.assertNotIn('name="rating"', html)

    def test_text_length(self):
        self.assertEqual(self.post(text="가" * 41).status_code, 400)
        self.assertEqual(Review.objects.count(), 0)
        self.assertEqual(self.post(text="가" * 40).status_code, 303)

    def test_text_is_optional(self):
        self.assertEqual(self.post(text="").status_code, 303)

    def test_roster_names_refused_only_my_school(self):
        for text in (f"{self.mate.name} 선생님이 좋아했어요", "윤 동 료 님과 통화했어요", "윤동\u200b료님 친절"):
            r = self.post(text=text)
            self.assertEqual(r.status_code, 400, text)
            self.assertContains(r, "이름", status_code=400)
        self.assertEqual(self.post(text=f"{WHO} 설명이 좋아요").status_code, 400)
        self.assertEqual(Review.objects.count(), 0)
        self.assertEqual(self.post(text=f"{self.other.name} 씨가 친절해요").status_code, 303)  # 다른 학교 명부로는 검사하지 않는다

    def test_two_letter_name_not_matched_across_spaces(self):
        Member.objects.create(space=self.space, name="이안", cls="4-4")
        self.assertEqual(self.post(text="이안 선생님 좋아요").status_code, 400)
        self.assertEqual(self.post(text="늘 이 안전 교육이 좋아요").status_code, 303)

    def test_roster_covers_whole_school_after_merge(self):
        open_school(self.school, None, manager=self.me)
        self.assertEqual(self.post(text=f"{self.mate.name} 칭찬").status_code, 400)

    def test_phone_refused(self):
        for text in ("010-1234-5678", "01012345678", "02) 123 4567", "1588-1234", "０１０-１２３４-５６７８", "연락 010.1234.5678 로", "031/123/4567"):
            self.assertEqual(self.post(text=text).status_code, 400, text)
        self.assertEqual(Review.objects.count(), 0)
        for text in ("2026.10.29 답사", "1000000원", "버스 3대를 12명이 탔어요", "123-4567번 영수증"):
            self.assertEqual(self.post(text=text).status_code, 303, text)

    def test_student_rule_not_applied_to_sentences(self):
        """칸 이름용 규칙을 문장에 쓰면 오탐이 많다. 학생 실명은 운영자 확인이 막는다."""
        self.assertEqual(self.post(badges=("5",), text="학부모 이메일로 안내해 줘요").status_code, 303)

    def test_refused_text_never_in_log(self):
        with self.assertLogs("django.request", level="WARNING") as cm:
            self.post(text="010-1234-5678")
        self.assertNotIn("010-1234-5678", "".join(cm.output))

    def test_xss_escaped_everywhere(self):
        attack = '<script>alert(1)</script>'
        self.assertEqual(self.post(text=attack).status_code, 303)
        for r in (self.anon.get(self.detail), self.mc.get(self.url)):
            self.assertNotContains(r, attack)
            self.assertContains(r, "&lt;script&gt;")
        r = self.post(text=attack + "가" * 40)  # 오류로 되돌아온 화면
        self.assertNotContains(r, attack, status_code=400)

    def test_keeps_input_on_error(self):
        r = self.post(badges=("3", "4"), text="가" * 41)
        html = r.content.decode()
        self.assertEqual(html.count(" checked>"), 2)


class OnePerSchoolTests(Base):
    def test_db_refuses_second(self):
        self.made()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.made(badges=(1,))

    def test_mate_cannot_write_or_change_and_sees_guide(self):
        self.post(text="먼저 쓴 글")
        r = self.matec.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, TAKEN)
        self.assertNotContains(r, "먼저 쓴 글")
        self.assertNotContains(r, 'name="badges"')
        r = self.post(self.matec, badges=("7",), text="덮어쓰기")
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, TAKEN, status_code=409)
        self.assertEqual(self.matec.post(self.url + "/delete").status_code, 403)
        rv = Review.objects.get()
        self.assertEqual((rv.badges, rv.text, rv.member), ([0, 1], "먼저 쓴 글", self.me))

    def test_other_school_writes_its_own_and_cannot_touch_mine(self):
        self.post(text="내 글")
        self.assertEqual(self.post(self.oc, badges=("6",), text="다른 글").status_code, 303)
        self.assertEqual(Review.objects.count(), 2)
        self.assertEqual(self.oc.post(self.url + "/delete").status_code, 303)  # 지워지는 것은 제 후기뿐
        self.assertEqual(Review.objects.get().text, "내 글")
        self.assertEqual(self.oc.post(self.url + "/delete").status_code, 403)
        self.assertEqual(Review.objects.get().text, "내 글")

    def test_forged_key_does_nothing(self):
        self.post(text="내 글")
        no = Review.objects.get().report_no
        self.matec.post(self.url, {"badges": ["7"], "text": "덮어쓰기", "report_no": no})
        self.matec.post(self.url + "/delete", {"report_no": no})
        self.assertEqual(Review.objects.get().text, "내 글")
        self.assertEqual(self.mc.get(f"/partners/{quote(self.p.slug)}/review/{no}").status_code, 404)

    def test_writer_edits_keeps_creation_time_and_labels(self):
        self.post(text="처음")
        before = Review.objects.get()
        Review.objects.filter(pk=before.pk).update(area_label="옛지역")
        r = self.mc.get(self.url)
        self.assertContains(r, "처음")
        self.assertEqual(self.post(badges=("7",), text="고침").status_code, 303)
        after = Review.objects.get()
        self.assertEqual((after.pk, after.badges, after.text, after.report_no, after.created_at, after.area_label),
                         (before.pk, [7], "고침", before.report_no, before.created_at, "옛지역"))
        self.assertGreater(after.updated_at, before.updated_at)

    def test_writer_deletes_then_school_can_write_again(self):
        self.post()
        r = self.mc.post(self.url + "/delete")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(Review.objects.count(), 0)
        self.assertEqual(self.post(self.matec).status_code, 303)
        self.assertEqual(Review.objects.get().member, self.mate)

    def test_delete_via_htmx_redirects_whole_page(self):
        self.post()
        r = self.mc.post(self.url + "/delete", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("deleted=1", r["HX-Redirect"])

    def test_delete_says_nothing_about_other_reviews(self):
        self.assertEqual(self.mc.post(self.url + "/delete").status_code, 403)
        self.made(school=self.school, member=self.mate)
        self.assertEqual(self.mc.post(self.url + "/delete").status_code, 403)

    def test_writer_leaves_review_stays_nobody_edits(self):
        self.post(text="남는 글")
        self.me.delete()
        rv = Review.objects.get()
        self.assertIsNone(rv.member)
        self.assertEqual(self.post(self.matec, text="새 글").status_code, 409)
        self.assertContains(self.matec.get(self.url), TAKEN)
        self.assertContains(self.anon.get(self.detail), "남는 글")
        self.assertEqual(Review.objects.get().text, "남는 글")

    def test_space_deleted_with_last_member_keeps_review(self):
        self.post(text="남는 글")
        Space.objects.filter(pk=self.space.pk).delete()  # 마지막 회원이 나갈 때 공간째 지운다(spaces.views.leave)
        rv = Review.objects.get()
        self.assertEqual((rv.text, rv.member, rv.school), ("남는 글", None, self.school))

    def test_school_is_protected(self):
        self.made()
        Space.objects.filter(school=self.school).delete()
        with self.assertRaises(ProtectedError):
            self.school.delete()

    def test_writer_keeps_review_through_merge(self):
        self.post(text="내 글")
        open_school(self.school, None, manager=self.me)
        self.assertEqual(self.post(badges=("7",), text="합친 뒤 고침").status_code, 303)
        rv = Review.objects.get()
        self.assertEqual((rv.text, rv.member.user_id), ("합친 뒤 고침", self.me.user_id))


class AnonymityTests(Base):
    """이름·학교 표식을 심고 공개 응답 전체를 뒤진다."""

    def leak_targets(self):
        return [self.detail, self.detail + "?page=1", "/partners", "/partners?sort=many", "/sitemap.xml"]

    def assert_clean(self, client):
        marks = [WHO, WHERE, self.me.key, self.space.token, self.school.neis_code, self.me.user.username, self.me.user.email]
        for path in self.leak_targets():
            r = client.get(path)
            self.assertEqual(r.status_code, 200, path)
            blob = r.content.decode() + "".join(f"{k}:{v}" for k, v in r.headers.items())
            for mark in marks:
                self.assertNotIn(mark, blob, path)
            for attr in ("data-id", "data-key", "data-school", "data-member", '"member"', '"school"', "pk="):
                self.assertNotIn(attr, blob, path)

    def test_public_responses_have_no_author_or_school(self):
        self.post(text="문구 표시 확인")
        for c in (self.anon, self.oc):
            self.assert_clean(c)
        body = self.anon.get(self.detail).content.decode()
        self.assertIn("문구 표시 확인", body)
        self.assertIn("고티처 선생님 · 서울 · 초등", body)
        self.assertNotIn("티로드", body)

    def test_htmx_response_clean(self):
        self.post()
        r = self.anon.get("/partners", headers={"HX-Request": "true"})
        self.assertNotIn(WHERE, r.content.decode())

    def test_mate_page_names_nobody(self):
        self.post(text="먼저 쓴 글")
        body = self.matec.get(self.url).content.decode()
        for mark in (WHO, Review.objects.get().report_no, "먼저 쓴 글"):
            self.assertNotIn(mark, body)

    def test_review_in_no_url_or_id_on_public_page(self):
        self.post()
        rv = Review.objects.get()
        body = self.anon.get(self.detail).content.decode()
        self.assertNotIn(f"/review/{rv.pk}", body)
        self.assertNotIn('id="review', body)

    def test_no_cookie_for_anonymous(self):
        self.post()
        self.assertNotIn("Set-Cookie", self.anon.get(self.detail).headers)


class ListTests(Base):
    def test_card_shows_top_three_and_total(self):
        for n, badges in enumerate([(0, 1, 2), (1, 2), (1, 3)]):
            school, _ = new_school(10 + n, f"학교{n}초등학교")
            self.made(school=school, badges=badges)
        body = self.anon.get("/partners").content.decode()
        for name in (BADGES[1], BADGES[2]):
            self.assertIn(name, body)
        self.assertEqual(sum(b in body for b in BADGES), 3)
        self.assertIn("좋은 점 <b", body)
        self.assertRegex(body, r"좋은 점 <b[^>]*>7</b>")
        self.assertRegex(body, r"후기 <b[^>]*>3</b>")

    def test_empty_card(self):
        self.assertContains(self.anon.get("/partners"), "아직 고른 배지가 없어요")

    def test_default_is_name_many_only_when_chosen(self):
        partner("가나다")
        na = partner("나다라")
        school, _ = new_school(20, "학교초등학교")
        self.made(school=school, p=na, badges=(0,))
        self.made(school=self.school, p=self.p, badges=(0, 1, 2))
        order = [p.name for p in self.anon.get("/partners?sort=many").context["page_obj"].object_list]
        self.assertEqual(order, ["참된인쇄", "나다라", "가나다"])
        order = [p.name for p in self.anon.get("/partners").context["page_obj"].object_list]
        self.assertEqual(order, ["가나다", "나다라", "참된인쇄"])

    def test_sort_is_badge_sum_not_review_count(self):
        many = partner("가후기둘")
        s2, _ = new_school(21, "둘초등학교")
        self.made(school=s2, p=many, badges=(0,))
        self.made(school=self.other_school, p=many, badges=(1,))
        self.made(school=self.school, p=self.p, badges=(0, 1, 2))
        order = [p.name for p in self.anon.get("/partners?sort=many").context["page_obj"].object_list]
        self.assertEqual(order[0], "참된인쇄")

    def test_no_reviews_is_name_order_and_odd_sort_harmless(self):
        partner("가나다")
        for q in ("", "?sort=../../x", "?sort=%00"):
            r = self.anon.get("/partners" + q)
            self.assertEqual([p.name for p in r.context["page_obj"].object_list], ["가나다", "참된인쇄"])

    def test_paging_keeps_sort(self):
        for i in range(20):
            partner(f"업체{i:02d}")
        self.assertContains(self.anon.get("/partners?sort=many"), "sort=many")

    def test_hidden_excluded_from_list_and_sort(self):
        ga = partner("가나다")
        self.made(school=self.school, p=self.p, badges=(0, 1, 2), is_hidden=True)
        self.made(school=self.other_school, p=ga, badges=(5,))
        body = self.anon.get("/partners").content.decode()
        self.assertNotIn(BADGES[0], body)
        self.assertRegex(body, r"좋은 점 <b[^>]*>1</b>")
        self.assertEqual(self.anon.get("/partners?sort=many").context["page_obj"].object_list[0].name, "가나다")

    def test_private_partner_reviews_nowhere(self):
        hid = partner("숨은업체", is_public=False)
        self.made(p=hid, text="숨은 글")
        self.assertNotContains(self.anon.get("/partners"), "숨은업체")
        self.assertEqual(self.anon.get(f"/partners/{quote(hid.slug)}").status_code, 404)


class DetailTests(Base):
    def test_counts_exclude_hidden_and_deleted(self):
        s2, _ = new_school(30, "둘초등학교")
        s3, _ = new_school(31, "셋초등학교")
        self.made(school=self.other_school, badges=(0, 1))
        self.made(school=s2, badges=(0,))
        self.made(school=s3, badges=(0, 7), is_hidden=True, text="내린 글")
        self.post(badges=("0",))
        self.mc.post(self.url + "/delete")
        r = self.anon.get(self.detail)
        counts = r.context["badge_rows"]
        self.assertEqual(dict((c["name"], c["n"]) for c in counts)[BADGES[0]], 2)
        self.assertEqual(dict((c["name"], c["n"]) for c in counts)[BADGES[1]], 1)
        self.assertEqual(dict((c["name"], c["n"]) for c in counts)[BADGES[7]], 0)
        self.assertEqual(r.context["review_count"], 2)
        self.assertNotContains(r, "내린 글")

    def test_all_eight_badges_listed_and_no_bad_ones(self):
        r = self.anon.get(self.detail)
        self.assertEqual([c["name"] for c in r.context["badge_rows"]], BADGES)
        self.assertContains(r, "점수·나쁜 배지는 없어요")

    def test_reviews_latest_first_and_paged(self):
        for n in range(12):
            school, _ = new_school(100 + n, f"학교{n}초등학교")
            self.made(school=school, text=f"글번호{n:02d}끝")
        r = self.anon.get(self.detail)
        self.assertContains(r, "글번호11끝")
        self.assertNotContains(r, "글번호01끝")
        self.assertContains(r, "?page=2")
        r2 = self.anon.get(self.detail + "?page=2")
        self.assertContains(r2, "글번호01끝")
        self.assertNotContains(r2, "글번호11끝")
        self.assertEqual(self.anon.get(self.detail + "?page=zzz").status_code, 200)
        self.assertContains(r2, 'rel="canonical" href="http')

    def test_write_button_by_viewer(self):
        self.assertContains(self.anon.get(self.detail), "/start?next=")
        self.assertContains(self.mc.get(self.detail), f'href="{self.url}"')
        self.assertContains(self.mc.get(self.detail), "후기 쓰기")
        self.post()
        self.assertContains(self.mc.get(self.detail), "내 후기 고치기")
        self.assertNotContains(self.matec.get(self.detail), "내 후기 고치기")

    def test_report_path(self):
        self.assertContains(self.anon.get(self.detail), "후기 신고는 운영자에게")
        self.assertContains(self.anon.get(self.detail), "mailto:admin@goteacher.xyz")

    def test_saved_note_only_for_logged_in(self):
        self.assertNotContains(self.anon.get(self.detail + "?saved=1"), "후기를 올렸어요")
        self.assertContains(self.mc.get(self.detail + "?saved=1"), "후기를 올렸어요")


class QueryCountTests(Base):
    def seed(self, n_partners, n_reviews):
        partners = [Partner(name=f"대량{i:03d}", slug=f"bulk-{i}", cat=CATS[0], area="전국", desc="글", is_public=True) for i in range(n_partners)]
        Partner.objects.bulk_create(partners)
        partners = list(Partner.objects.filter(slug__startswith="bulk-"))
        schools = School.objects.bulk_create([School(neis_atpt="B10", neis_code=f"9{i:06d}", name=f"대량{i}초등학교") for i in range(n_reviews)])
        Review.objects.bulk_create([
            Review(partner=partners[i % n_partners], school=schools[i], badges=[i % 8, (i + 1) % 8], text=f"글{i}")
            for i in range(n_reviews)
        ])
        return partners[0]

    def count(self, path):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.anon.get(path).status_code, 200)
        return len(ctx)

    def test_list_and_detail_flat_at_100_partners_1000_reviews(self):
        first = self.seed(5, 10)
        small_list, small_detail = self.count("/partners?sort=many"), self.count(f"/partners/{first.slug}")
        Review.objects.all().delete()
        Partner.objects.filter(slug__startswith="bulk-").delete()
        School.objects.filter(neis_code__startswith="9").delete()
        first = self.seed(100, 1000)
        self.assertEqual(self.count("/partners?sort=many"), small_list)
        self.assertEqual(self.count(f"/partners/{first.slug}"), small_detail)
        self.assertLessEqual(self.count("/partners?sort=many"), 6)

    def test_sum_is_right_at_scale(self):
        first = self.seed(100, 1000)
        r = self.anon.get("/partners?sort=many")
        self.assertEqual(r.context["page_obj"].paginator.count, 101)
        top = r.context["page_obj"].object_list[0]
        self.assertEqual(top.n_badges, 20)
        self.assertEqual(top.n_reviews, 10)
        r = self.anon.get(f"/partners/{first.slug}")
        self.assertEqual(sum(c["n"] for c in r.context["badge_rows"]), 20)


class CommandTests(Base):
    def cmd(self, *args):
        out = StringIO()
        call_command("partners", *args, stdout=out)
        return out.getvalue()

    def test_hide_show_cycle_keeps_reason_and_history(self):
        self.post(text="문제 글")
        rv = Review.objects.get()
        with self.assertRaises(CommandError):
            call_command("partners", "review-hide", rv.report_no)
        self.cmd("review-hide", rv.report_no, "--reason", "명예훼손 신고", "--note", "업체 대표 메일")
        rv.refresh_from_db()
        self.assertEqual((rv.is_hidden, rv.hidden_reason), (True, "명예훼손 신고"))
        self.assertNotContains(self.anon.get(self.detail), "문제 글")
        self.assertNotContains(self.anon.get(self.detail), rv.report_no)
        self.assertNotContains(self.anon.get("/partners"), BADGES[0])
        self.assertNotContains(self.anon.get(self.detail), "명예훼손 신고")
        self.cmd("review-show", rv.report_no)
        rv.refresh_from_db()
        self.assertEqual((rv.is_hidden, rv.hidden_reason), (False, "명예훼손 신고"))
        self.assertContains(self.anon.get(self.detail), "문제 글")
        acts = list(ReviewAction.objects.order_by("pk").values_list("action", "reason", "note", "by", "report_no"))
        self.assertEqual(acts, [("hide", "명예훼손 신고", "업체 대표 메일", "운영자", rv.report_no), ("show", "", "", "운영자", rv.report_no)])

    def test_delete_frees_seat_and_history_stays_without_text(self):
        self.post(text="지울 원문 표식")
        no = Review.objects.get().report_no
        with self.assertRaises(CommandError):
            call_command("partners", "review-delete", no)
        self.cmd("review-delete", no, "--reason", "삭제 요구")
        self.assertEqual(Review.objects.count(), 0)
        act = ReviewAction.objects.get()
        self.assertEqual((act.review, act.report_no, act.action, act.reason), (None, no, "delete", "삭제 요구"))
        self.assertEqual(self.post(self.matec).status_code, 303)  # 학교 자리가 풀린다
        self.assertNotIn("지울 원문 표식", str(ReviewAction.objects.values()))

    def test_lowercase_number_found(self):
        self.post()
        rv = Review.objects.get()
        self.cmd("review-hide", rv.report_no.lower(), "--reason", "x")
        self.assertTrue(Review.objects.get().is_hidden)

    def test_empty_reason_and_missing_number_error(self):
        self.post()
        no = Review.objects.get().report_no
        for args in (("review-hide", no, "--reason", "  "), ("review-hide", "R-NOPE", "--reason", "x"), ("review-show", "R-NOPE"),
                     ("review-hide", no, "--reason", "가" * 201), ("review-delete", "R-NOPE", "--reason", "x"), ("reviews", "--partner", "없는업체")):
            with self.assertRaises(CommandError):
                self.cmd(*args)
        with self.assertRaises(CommandError):
            call_command("partners", "reviews")

    def test_reviews_list_is_one_line_per_row_and_names_nobody(self):
        self.post(text="한 줄 글")
        rv = Review.objects.get()
        Review.objects.filter(pk=rv.pk).update(text="터미널\x1b[31m글\n둘째")
        out = self.cmd("reviews", "--partner", self.p.slug)
        self.assertIn(rv.report_no, out)
        self.assertNotIn("\x1b", out)
        self.assertEqual(len(out.strip().splitlines()), 1)
        for mark in (WHO, WHERE):
            self.assertNotIn(mark, out)

    def test_hidden_review_locked_for_writer_and_blocks_school_and_account(self):
        self.post(text="내린 글")
        self.cmd("review-hide", Review.objects.get().report_no, "--reason", "신고")
        r = self.mc.get(self.url)
        self.assertContains(r, "운영자가 이 후기를 내렸어요")
        self.assertNotContains(r, 'name="badges"')
        self.assertEqual(self.post(badges=("7",), text="우회").status_code, 409)
        self.assertEqual(self.mc.post(self.url + "/delete").status_code, 409)
        self.assertEqual(self.post(self.matec).status_code, 409)
        rv = Review.objects.get()
        self.assertEqual((rv.text, rv.is_hidden), ("내린 글", True))
        d = self.mc.get(self.detail)
        self.assertContains(d, "운영자가 내렸어요")
        self.assertNotContains(d, "내 후기 고치기")

    def test_edit_race_cannot_unhide(self):
        self.post()
        rv = Review.objects.get()
        Review.objects.filter(pk=rv.pk).update(is_hidden=True)
        self.post(badges=("7",))
        rv.refresh_from_db()
        self.assertTrue(rv.is_hidden)
        self.assertEqual(rv.badges, [0, 1])

    def test_help_warns_about_personal_data(self):
        self.assertIn("개인정보", partners_command.__doc__)
        self.assertIn("개인정보", partners_command.NO_PII)


class TextStateTests(Base):
    """한 줄은 운영자 확인 전 비공개, 배지는 바로 공개."""

    def raw(self, text="견적이 빨라요", badges=("0",)):
        return self.mc.post(self.url, {"badges": list(badges), "text": text})

    def cmd(self, *args):
        out = StringIO()
        call_command("partners", *args, stdout=out)
        return out.getvalue()

    def test_pending_text_hidden_badges_shown(self):
        self.raw()
        rv = Review.objects.get()
        self.assertEqual(rv.text_state, "pending")
        r = self.anon.get(self.detail)
        self.assertNotContains(r, "견적이 빨라요")
        self.assertContains(r, BADGES[0])
        self.assertContains(r, rv.report_no)
        self.assertNotContains(self.oc.get(self.detail), "견적이 빨라요")

    def test_writer_told(self):
        self.assertContains(self.mc.get(self.url), "한 줄은 운영자가 확인한 뒤 보여요")
        self.raw()
        self.assertContains(self.mc.get(self.detail + "?saved=1"), "한 줄은 운영자가 확인한 뒤 보여요")

    def test_ok_no_and_pending_list(self):
        self.raw()
        rv = Review.objects.get()
        self.assertIn(rv.report_no, self.cmd("reviews", "--pending"))
        self.cmd("review-text-ok", rv.report_no)
        self.assertContains(self.anon.get(self.detail), "견적이 빨라요")
        self.assertNotIn(rv.report_no, self.cmd("reviews", "--pending"))
        self.cmd("review-text-no", rv.report_no, "--reason", "학생 이름")
        self.assertNotContains(self.anon.get(self.detail), "견적이 빨라요")
        self.assertContains(self.mc.get(self.url), "공개하지 않기로")
        self.assertEqual(list(ReviewAction.objects.order_by("pk").values_list("action", flat=True)), ["text_ok", "text_no"])

    def test_edit_text_goes_back_to_pending_same_text_keeps_state(self):
        self.raw()
        no = Review.objects.get().report_no
        self.cmd("review-text-ok", no)
        self.raw(badges=("1",))  # 배지만 고침: 한 줄은 그대로
        self.assertEqual(Review.objects.get().text_state, "ok")
        self.raw(text="다른 글")
        self.assertEqual(Review.objects.get().text_state, "pending")
        self.assertNotContains(self.anon.get(self.detail), "다른 글")
        self.cmd("review-text-no", no)
        self.raw(text="다른 글", badges=("2",))  # 반려를 다시 저장해 대기로 돌리지 못한다
        self.assertEqual(Review.objects.get().text_state, "no")

    def test_no_text_is_never_pending(self):
        self.raw(text="")
        self.assertEqual(Review.objects.get().text_state, "ok")
        self.assertEqual(self.cmd("reviews", "--pending"), "")

    def test_edited_mark_and_month_date(self):
        self.raw(text="")
        r = self.anon.get(self.detail)
        self.assertNotContains(r, "고침")
        rv = Review.objects.get()
        self.assertContains(r, f"{rv.created_at.year}년 {timezone.localtime(rv.created_at).month}월")
        Review.objects.update(created_at=timezone.now() - timedelta(days=2))
        self.raw(text="", badges=("3",))
        self.assertContains(self.anon.get(self.detail), "고침")


@override_settings(CACHES=RL_CACHES)
class EligibilityTests(TestCase):
    """실제 설정값: 14일, 구글 회원 2명, 하루 5건."""

    def setUp(self):
        caches["ratelimit"].clear()
        self.p = partner()
        self.url = f"/partners/{quote(self.p.slug)}/review"
        self.school, self.space = new_school(1, WHERE)
        self.me, self.mc = teacher(self.space, "1", WHO)

    def age(self, member, days=15):
        Member.objects.filter(pk=member.pk).update(joined_at=timezone.now() - timedelta(days=days))

    def post(self, text="", badges=("0",)):
        return self.mc.post(self.url, {"badges": list(badges), "text": text})

    def assert_not_yet(self):
        for r in (self.mc.get(self.url), self.post()):
            self.assertEqual(r.status_code, 403)
            self.assertContains(r, "14일 넘게 함께 쓰면", status_code=403)
            self.assertNotContains(r, 'name="badges"', status_code=403)
        self.assertEqual(Review.objects.count(), 0)

    def test_new_or_alone_refused(self):
        self.assert_not_yet()  # 새 회원·혼자
        self.age(self.me)
        self.assert_not_yet()  # 혼자
        teacher(self.space, "2", "윤동료님")
        Member.objects.filter(pk=self.me.pk).update(joined_at=timezone.now() - timedelta(days=13))
        self.assert_not_yet()  # 13일

    def test_guests_do_not_count(self):
        self.age(self.me)
        guest = User.objects.create(username="guest:z", first_name="손님")
        Member.objects.create(space=self.space, user=guest, name="박손님샘", cls="4-2")
        self.assert_not_yet()

    def test_two_google_members_and_15_days_ok(self):
        self.age(self.me)
        teacher(self.space, "2", "윤동료님")
        self.assertEqual(self.mc.get(self.url).status_code, 200)
        self.assertEqual(self.post().status_code, 303)

    def test_school_plan_space_ok_at_once_expired_falls_back(self):
        hub = open_school(self.school, timezone.localdate() + timedelta(days=30), manager=self.me)
        self.assertTrue(hub.is_school)
        self.assertEqual(self.mc.get(self.url).status_code, 200)
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.mc.get(self.url).status_code, 403)

    def test_refused_person_learns_nothing(self):
        """자격이 없으면 '이미 남김'도 이름 검사도 답하지 않는다."""
        mate, _ = teacher(self.space, "2", "윤동료님")
        Review.objects.create(partner=self.p, school=self.school, member=mate, author_user=mate.user, badges=[0])
        self.assertNotContains(self.mc.get(self.url), TAKEN, status_code=403)
        r = self.post(text=f"{mate.name} 칭찬")
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(r, "이름", status_code=403)
        self.assertNotContains(self.post(), TAKEN, status_code=403)
        self.assertEqual(self.mc.post(self.url + "/delete").status_code, 403)
        self.assertNotContains(self.mc.post(self.url + "/delete"), TAKEN, status_code=403)

    def test_refused_posts_count_and_limit_hides_everything(self):
        self.age(self.me)
        mate, _ = teacher(self.space, "2", "윤동료님")
        for _ in range(5):  # 이름 거부도 하루 한도를 쓴다
            self.assertEqual(self.post(text=f"{mate.name} 칭찬").status_code, 400)
        r = self.post(text=f"{mate.name} 칭찬")
        self.assertEqual(r.status_code, 429)
        self.assertNotContains(r, "이름", status_code=429)
        self.assertEqual(Review.objects.count(), 0)

    def test_edit_after_seat_moves_to_new_space_within_14_days(self):
        self.age(self.me)
        teacher(self.space, "2", "윤동료님")
        self.post(text="처음")
        _, new_space = new_school(2, "옮긴초등학교")
        Member.objects.create(space=new_space, user=self.me.user, name=WHO, cls="4-1")
        self.assertEqual(self.mc.get(self.url).status_code, 200)
        self.assertEqual(self.post(text="고침").status_code, 303)
        rv = Review.objects.get()
        self.assertEqual((rv.text, rv.school), ("고침", self.school))

    def test_one_review_per_account_even_after_leaving_and_new_school(self):
        self.age(self.me)
        teacher(self.space, "2", "윤동료님")
        self.post()
        self.me.delete()
        _, new_space = new_school(2, "옮긴초등학교")
        teacher(new_space, "9", "옮긴동료샘")
        again = Member.objects.create(space=new_space, user=self.me.user, name=WHO, cls="4-1")
        self.age(again)
        r = self.mc.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "이미 후기를 남기셨어요")
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(Review.objects.count(), 1)

    def test_hidden_writer_cannot_write_after_moving(self):
        self.age(self.me)
        teacher(self.space, "2", "윤동료님")
        self.post()
        no = Review.objects.get().report_no
        call_command("partners", "review-hide", no, "--reason", "x", stdout=StringIO())
        self.me.delete()
        _, new_space = new_school(2, "옮긴초등학교")
        teacher(new_space, "9", "옮긴동료샘")
        again = Member.objects.create(space=new_space, user=self.me.user, name=WHO, cls="4-1")
        self.age(again)
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(Review.objects.count(), 1)

    def test_account_removed_cuts_author(self):
        self.age(self.me)
        teacher(self.space, "2", "윤동료님")
        self.post()
        self.me.user.delete()
        rv = Review.objects.get()
        self.assertIsNone(rv.author_user)

    def test_db_refuses_two_by_one_account(self):
        other, _ = new_school(2, "둘초등학교")
        Review.objects.create(partner=self.p, school=self.school, author_user=self.me.user, badges=[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Review.objects.create(partner=self.p, school=other, author_user=self.me.user, badges=[0])


class LeaveAndLegalTests(Base):
    def test_leave_dialog_offers_delete(self):
        self.post()
        r = self.mc.get(f"/s/{self.space.token}/settings?a=space")
        self.assertContains(r, "내 후기 지우기")
        self.assertContains(r, f"/partners/{self.p.slug}/review/delete")
        self.assertNotContains(self.matec.get(f"/s/{self.space.token}/settings?a=space"), "내 후기 지우기")

    def test_delete_from_settings_returns_there(self):
        self.post()
        r = self.mc.post(self.url + "/delete", {"back": "settings"}, headers={"HX-Request": "true"})
        self.assertEqual(r["HX-Redirect"], f"/s/{self.space.token}/settings?a=space")
        self.assertEqual(Review.objects.count(), 0)

    def test_terms_and_privacy_describe_it(self):
        terms = self.anon.get("/terms").content.decode()
        self.assertIn("임시로 내릴 수", terms)
        self.assertIn("신고 번호", terms)
        privacy = self.anon.get("/privacy").content.decode()
        for word in ("협력업체 후기", "3년", "익명으로 남으며", "메일"):
            self.assertIn(word, privacy)

    def test_no_certified_wording_or_false_promise(self):
        self.post()
        for path in (self.detail, "/partners"):
            body = self.anon.get(path).content.decode()
            for word in ("인증 교사", "아쉬운 점", "후기를 지울 수 없고"):
                self.assertNotIn(word, body)
        self.assertContains(self.anon.get("/partners"), "신고되면 운영자가 내릴 수 있어요")
        r = self.mc.get(self.url)
        self.assertContains(r, "다른 학교 선생님과 업체도 봅니다")

    def test_field_err_hidden_rule_exists(self):
        css = (Path(settings.REPO_DIR) / "web" / "static" / "css" / "gt.css").read_text(encoding="utf-8")
        self.assertIn(".field-err[hidden] { display: none; }", css)

    def test_purge_three_years(self):
        self.post()
        rv = Review.objects.get()
        call_command("partners", "review-hide", rv.report_no, "--reason", "x", stdout=StringIO())
        now = timezone.now()
        self.assertEqual(purge(now)["reviews"], 0)
        Review.objects.update(created_at=now - timedelta(days=3 * 365 + 1))
        ReviewAction.objects.update(created_at=now - timedelta(days=3 * 365 + 1))
        self.assertEqual(purge(now, dry_run=True)["reviews"], 1)
        self.assertEqual(purge(now)["reviews"], 1)
        self.assertEqual((Review.objects.count(), ReviewAction.objects.count()), (0, 0))


@override_settings(CACHES=RL_CACHES, **REVIEW_OPEN)
@skipUnlessDBFeature("has_select_for_update")
class ConcurrentTests(TransactionTestCase):
    def test_two_mates_at_once_one_review(self):
        p = partner()
        school, space = new_school(1, WHERE)
        clients = [teacher(space, str(i), f"동시회원{i}")[1] for i in range(6)]
        codes, gate = [], threading.Barrier(len(clients))

        def go(c):
            close_old_connections()
            gate.wait()
            codes.append(c.post(f"/partners/{quote(p.slug)}/review", {"badges": ["0"]}).status_code)
            connection.close()

        threads = [threading.Thread(target=go, args=(c,)) for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(Review.objects.filter(partner=p, school=school).count(), 1)
        self.assertEqual(sorted(codes), [303] + [409] * 5)
