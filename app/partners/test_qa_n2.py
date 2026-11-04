"""N2 독립 검증(gt-qa). test_reviews.py에 없는 경계만: 시도 코드 전체·학교 이름 표, 무작위 집계 대조(prototype topBadges),
실제 나가기·내보내기·자리 합치기 뒤 동작, 입력 우회, 동시 제출(다른 학교·같은 사람).
"""
import random
import threading
from collections import Counter
from io import StringIO
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature

from config.test_ratelimit import RL_CACHES
from spaces.merge import open_school
from spaces.models import Member, School, Space, absorb_guest

from .models import BADGES, CATS, Partner, Review, review_label, school_labels

User = get_user_model()
AREAS = {"B10": "서울", "C10": "부산", "D10": "대구", "E10": "인천", "F10": "광주", "G10": "대전", "H10": "울산", "I10": "세종", "J10": "경기",
         "K10": "강원", "M10": "충북", "N10": "충남", "P10": "전북", "Q10": "전남", "R10": "경북", "S10": "경남", "T10": "제주"}  # NEIS 공식 코드표


REVIEW_OPEN = {"PARTNER_REVIEW_MIN_DAYS": 0, "PARTNER_REVIEW_MIN_GOOGLE": 1, "PARTNER_REVIEW_PER_DAY": 1000}  # 자격·한도는 test_reviews.py가 실제 값으로 본다


def vendor(name="참된인쇄", **kw):
    kw.setdefault("cat", CATS[0])
    kw.setdefault("area", "전국")
    kw.setdefault("desc", "소개")
    kw.setdefault("is_public", True)
    return Partner.objects.create(name=name, **kw)


def school_space(n, name="새솔초등학교", atpt="J10"):
    school = School.objects.create(neis_atpt=atpt, neis_code=f"88{n:05d}", name=name)
    return school, Space.objects.create(school=school, grade=4, name=f"{name} 4학년")


def teacher(space, sub, name, user=None):
    user = user or User.objects.create(username=f"google:{sub}", first_name=name)
    m = Member.objects.create(space=space, user=user, name=name, cls="4-1")
    c = Client()
    c.force_login(user)
    return m, c


@override_settings(CACHES=RL_CACHES, **REVIEW_OPEN)
class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.p = vendor()
        self.url = f"/partners/{quote(self.p.slug)}/review"
        self.detail = f"/partners/{quote(self.p.slug)}"
        self.school, self.space = school_space(1)
        self.me, self.mc = teacher(self.space, "me", "홍길표식")
        self.anon = Client()

    def post(self, c=None, badges=("0",), text=""):
        """내고, 운영자가 한 줄을 확인한 상태로 둔다(대기 동작은 test_reviews.py가 본다)."""
        r = (c or self.mc).post(self.url, {"badges": list(badges), "text": text})
        Review.objects.filter(text_state=Review.TextState.PENDING).update(text_state=Review.TextState.OK)
        return r


class LabelTests(TestCase):
    def test_every_atpt_code(self):
        for code, area in AREAS.items():
            self.assertEqual(school_labels(School(neis_atpt=code, name="새솔초등학교"))[0], area)

    def test_overseas_or_unknown_code_is_left_out(self):
        for code in ("V10", "", "Z99", "b10"):
            self.assertEqual(school_labels(School(neis_atpt=code, name="새솔초등학교"))[0], "")

    def test_level_by_name(self):
        for name, level in [("새솔초등학교", "초등"), ("한국교원대학교부설월곡초등학교", "초등"), ("제주여자중학교", "중등"), ("서울과학고등학교", "고등"),
                            ("부산방송통신고등학교", "고등")]:
            self.assertEqual(school_labels(School(neis_atpt="B10", name=name))[1], level, name)

    def test_name_not_judged_gives_no_level_never_a_wrong_one(self):
        for name in ("서울맹학교", "인천은광학교", "세종누리학교", "한빛학교", "서울고", "고등기술학교부설", "초등교육원", "중학교부설방송대"):
            self.assertEqual(school_labels(School(neis_atpt="B10", name=name))[1], "", name)

    def test_label_never_has_dangling_separator(self):
        self.assertEqual(review_label("", ""), "고티처 선생님")
        self.assertEqual(review_label("서울", ""), "고티처 선생님 · 서울")
        self.assertEqual(review_label("", "초등"), "고티처 선생님 · 초등")


class AggregateTests(Base):
    """무작위 후기를 넣고 prototype vCounts·vTotal·topBadges와 같은 계산으로 대조한다."""

    def test_random_against_prototype_formula(self):
        rnd = random.Random(20261104)
        partners = [vendor(f"업체{i:02d}", area=rnd.choice(["전국", "서울", "경기 · 인천"]), cat=rnd.choice(CATS[:3])) for i in range(25)]
        want = {p.pk: [] for p in partners}
        for n in range(150):
            p, hidden = rnd.choice(partners), rnd.random() < 0.2
            badges = sorted(rnd.sample(range(8), rnd.randint(1, 5)))
            school = School.objects.create(neis_atpt="B10", neis_code=f"77{n:05d}", name=f"대조{n}초등학교")
            Review.objects.create(partner=p, school=school, badges=badges, is_hidden=hidden)
            if not hidden:
                want[p.pk].append(badges)

        def tops(lists):
            c = Counter(i for b in lists for i in b)
            pairs = sorted(((i, c[i]) for i in range(8) if c[i]), key=lambda x: -x[1])  # JS sort는 안정 정렬
            return [{"name": BADGES[i], "n": n} for i, n in pairs[:3]]

        seen = []
        for page in (1, 2):
            for card in self.anon.get(f"/partners?sort=many&page={page}").context["page_obj"].object_list:
                seen.append(card)
                lists = want.get(card.pk, [])
                self.assertEqual((card.n_reviews, card.n_badges), (len(lists), sum(map(len, lists))), card.name)
                self.assertEqual(card.top_badges, tops(lists), card.name)
        scores = [c.n_badges for c in seen]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len({c.pk for c in seen}), 26)  # 쪽 사이에 겹치거나 빠지지 않는다
        for p in partners[:5]:
            r = self.anon.get(f"/partners/{quote(p.slug)}")
            c = Counter(i for b in want[p.pk] for i in b)
            self.assertEqual([x["n"] for x in r.context["badge_rows"]], [c[i] for i in range(8)])
            self.assertEqual(r.context["review_count"], len(want[p.pk]))
            top = max(list(c.values()) + [1])
            self.assertEqual([x["pct"] for x in r.context["badge_rows"]], [c[i] * 100 // top for i in range(8)])

    def test_filter_does_not_change_score(self):
        a, b = vendor("가서울", area="서울", tags="x"), vendor("나서울", area="서울")
        school, _ = school_space(2, "둘초등학교")
        Review.objects.create(partner=b, school=school, badges=[0, 1])
        Review.objects.create(partner=a, school=self.school, badges=[0])
        for q in ("?sort=many&area=서울", "?sort=many&q=서울", f"?sort=many&cat={quote(CATS[0])}&area=서울"):
            cards = self.anon.get("/partners" + q).context["page_obj"].object_list
            self.assertEqual([(c.name, c.n_badges) for c in cards][:2], [("나서울", 2), ("가서울", 1)], q)


class InputEdgeTests(Base):
    def test_badge_non_canonical_refused(self):
        for bad in ("00", "01", "+1", " 1", "1 ", "0x1", "１"):
            self.assertEqual(self.post(badges=(bad,)).status_code, 400, repr(bad))
        self.assertEqual(self.post(badges=[str(i % 8) for i in range(500)]).status_code, 400)
        self.assertEqual(Review.objects.count(), 0)

    def test_other_methods_refused(self):
        for method in ("put", "patch", "delete"):
            self.assertEqual(getattr(self.mc, method)(self.url).status_code, 405)
            self.assertEqual(getattr(self.mc, method)(self.url + "/delete").status_code, 405)
        self.assertEqual(self.anon.delete(self.url).status_code, 405)

    def test_nul_and_controls_in_text_and_path(self):
        self.assertEqual(self.post(text="가\x00나\x07다\n라‮마").status_code, 303)
        self.assertEqual(Review.objects.get().text, "가 나 다 라마")
        for path in ("/partners/a%00b/review", "/partners/a%00b/review/delete"):
            self.assertIn(self.mc.post(path, {"badges": ["0"]}).status_code, (400, 404), path)

    def test_40_counts_after_trim_and_decomposed_stored_within_limit(self):
        self.assertEqual(self.post(text="  " + "가" * 40 + "  ").status_code, 303)
        self.assertEqual(len(Review.objects.get().text), 40)
        self.mc.post(self.url + "/delete")
        self.assertEqual(self.post(text="😀" * 40).status_code, 303)

    def test_refused_page_keeps_no_other_school_data(self):
        other, _ = school_space(2, "남의초등학교", "B10")
        Review.objects.create(partner=self.p, school=other, badges=[1], text="남의 글 표식")
        r = self.post(text="가" * 41)
        self.assertNotContains(r, "남의 글 표식", status_code=400)
        self.assertNotContains(r, "남의초등학교", status_code=400)


class LifecycleTests(Base):
    """실제 나가기·내보내기 화면을 거친다(ORM으로 지우지 않고)."""

    def test_leave_via_view_keeps_review_and_locks_school(self):
        mate, mc2 = teacher(self.space, "mate", "윤동료님")
        self.post(text="남는 글")
        r = self.mc.post(f"/s/{self.space.token}/settings/leave", {"confirm": self.space.name})
        self.assertEqual(r.status_code, 302)
        rv = Review.objects.get()
        self.assertIsNone(rv.member)
        self.assertContains(self.anon.get(self.detail), "남는 글")
        self.assertEqual(self.mc.get(self.url).status_code, 403)  # 공간 없는 회원
        self.assertEqual(self.mc.post(self.url + "/delete").status_code, 403)
        self.assertEqual(self.post(mc2).status_code, 409)
        self.assertNotContains(mc2.get(self.detail), "내 후기 고치기")

    def test_last_member_leaves_space_gone_school_stays(self):
        self.post(text="남는 글")
        r = self.mc.post(f"/s/{self.space.token}/settings/leave", {"confirm": self.space.name})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Space.objects.filter(pk=self.space.pk).exists())
        self.assertEqual(Review.objects.get().school, self.school)
        self.assertContains(self.anon.get(self.detail), "남는 글")

    def test_rejoin_is_a_new_seat_cannot_edit(self):
        mate, _ = teacher(self.space, "mate", "윤동료님")
        self.post(text="남는 글")
        self.mc.post(f"/s/{self.space.token}/settings/leave", {"confirm": self.space.name})
        Member.objects.create(space=self.space, user=self.me.user, name="홍길표식", cls="4-1")
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(Review.objects.get().text, "남는 글")

    def test_merge_dropping_writer_seat_moves_review_to_kept_seat(self):
        """한 사용자가 한 학교에 자리 둘(손님을 이으면 생긴다). 합치면 자리 하나만 남고 후기도 따라간다."""
        other_space = Space.objects.create(school=self.school, grade=5, name="새솔초 5학년")
        other_seat = Member.objects.create(space=other_space, user=self.me.user, name="홍길표식", cls="5-1")
        self.post(text="내 글")
        self.assertEqual(Review.objects.get().member_id, other_seat.pk)  # 가장 최근 자리로 썼다
        open_school(self.school, None, manager=self.me)
        self.assertEqual(Member.objects.filter(user=self.me.user).count(), 1)
        rv = Review.objects.get()
        self.assertEqual(rv.member.user_id, self.me.user_id)
        self.assertEqual(self.post(badges=("7",), text="합친 뒤").status_code, 303)
        self.assertEqual(Review.objects.get().text, "합친 뒤")

    def test_merge_keeps_mate_lock_and_public_view(self):
        s2 = Space.objects.create(school=self.school, grade=5, name="새솔초 5학년")
        _, c5 = teacher(s2, "g5", "오학년샘")
        self.post(text="4학년이 쓴 글")
        self.assertEqual(self.post(c5).status_code, 409)  # 학년이 달라도 학교는 하나
        open_school(self.school, None, manager=self.me)
        self.assertEqual(self.post(c5).status_code, 409)
        self.assertContains(self.anon.get(self.detail), "4학년이 쓴 글")

    def test_guest_linked_to_google_can_write(self):
        guest = User.objects.create(username="guest:zz", first_name="손")
        Member.objects.create(space=self.space, user=guest, name="박이을샘", cls="4-2")
        g = User.objects.create(username="google:linked", first_name="박")
        absorb_guest(guest, g)
        c = Client()
        c.force_login(g)
        self.assertEqual(self.post(c).status_code, 303)
        self.assertEqual(Review.objects.get().member.user, g)

    def test_guest_review_url_keeps_next_for_google(self):
        guest = User.objects.create(username="guest:yy", first_name="손")
        Member.objects.create(space=self.space, user=guest, name="박손님샘", cls="4-2")
        c = Client()
        c.force_login(guest)
        r = c.get(self.url)
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "/auth/google/login?next=", status_code=403)
        self.assertNotContains(r, 'name="badges"', status_code=403)

    def test_partner_hidden_then_back(self):
        self.post(text="다시 나올 글")
        call_command("partners", "hide", self.p.slug, stdout=StringIO())
        self.assertEqual(self.mc.get(self.url).status_code, 404)
        self.assertEqual(self.anon.get(self.detail).status_code, 404)
        self.assertNotContains(self.anon.get("/sitemap.xml"), self.p.slug)
        call_command("partners", "publish", self.p.slug, stdout=StringIO())
        self.assertContains(self.anon.get(self.detail), "다시 나올 글")

    def test_hidden_notice_never_shows_reason_or_text_to_writer(self):
        self.post(text="내린 글 표식")
        rv = Review.objects.get()
        call_command("partners", "review-hide", rv.report_no, "--reason", "신고까닭표식", stdout=StringIO())
        for r in (self.mc.get(self.url), self.post(badges=("7",)), self.mc.get(self.detail)):
            self.assertNotContains(r, "신고까닭표식", status_code=r.status_code)
            self.assertNotContains(r, "내린 글 표식", status_code=r.status_code)

    def test_hide_show_is_idempotent_and_counts_return(self):
        self.post(badges=("2", "3"))
        rv = Review.objects.get()
        call_command("partners", "review-hide", rv.report_no, "--reason", "한번", stdout=StringIO())
        call_command("partners", "review-hide", rv.report_no, "--reason", "두번", stdout=StringIO())
        self.assertEqual(self.anon.get(self.detail).context["review_count"], 0)
        self.assertEqual(self.anon.get("/partners").context["page_obj"].object_list[0].n_badges, 0)
        call_command("partners", "review-show", rv.report_no, stdout=StringIO())
        call_command("partners", "review-show", rv.report_no, stdout=StringIO())
        self.assertEqual(self.anon.get(self.detail).context["review_count"], 1)
        self.assertEqual(self.anon.get("/partners").context["page_obj"].object_list[0].n_badges, 2)
        self.assertEqual(self.post(badges=("7",)).status_code, 303)  # 되돌리면 다시 고칠 수 있다


@override_settings(CACHES=RL_CACHES, **REVIEW_OPEN)
@skipUnlessDBFeature("has_select_for_update")
class ConcurrentEdgeTests(TransactionTestCase):
    def race(self, jobs):
        codes, gate = [], threading.Barrier(len(jobs))

        def go(job):
            close_old_connections()
            gate.wait()
            codes.append(job().status_code)
            connection.close()

        threads = [threading.Thread(target=go, args=(j,)) for j in jobs]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return sorted(codes)

    def test_different_schools_all_succeed(self):
        caches["ratelimit"].clear()
        p = vendor()
        url = f"/partners/{quote(p.slug)}/review"
        clients = [teacher(school_space(i, f"학교{i}초등학교")[1], f"c{i}", f"동시샘{i}")[1] for i in range(6)]
        codes = self.race([lambda c=c: c.post(url, {"badges": ["0"]}) for c in clients])
        self.assertEqual(codes, [303] * 6)
        self.assertEqual(Review.objects.filter(partner=p).count(), 6)

    def test_same_writer_double_click_one_review(self):
        caches["ratelimit"].clear()
        p = vendor()
        url = f"/partners/{quote(p.slug)}/review"
        _, space = school_space(1)
        m, c = teacher(space, "dbl", "두번샘님")
        c2 = Client()
        c2.force_login(m.user)
        codes = self.race([lambda: c.post(url, {"badges": ["0"]}), lambda: c2.post(url, {"badges": ["1"]})])
        self.assertEqual(Review.objects.filter(partner=p).count(), 1)
        self.assertTrue(set(codes) <= {303, 409}, codes)

    def test_hide_vs_edit_stays_hidden(self):
        caches["ratelimit"].clear()
        p = vendor()
        url = f"/partners/{quote(p.slug)}/review"
        _, space = school_space(1)
        m, c = teacher(space, "hv", "내림경쟁")
        c.post(url, {"badges": ["0"]})
        key = Review.objects.get().report_no

        class Done:
            status_code = 0

        def hide():
            call_command("partners", "review-hide", key, "--reason", "경쟁", stdout=StringIO())
            return Done

        self.race([lambda: c.post(url, {"badges": ["1"]}), hide])
        self.assertTrue(Review.objects.get().is_hidden)


class KnownDefectTests(Base):
    """N2 검증에서 찾은 결함을 고친 뒤의 회귀 테스트."""

    def test_report_number_never_starts_with_dash_and_works_in_command(self):
        """예전 토큰 키는 '-'로 시작할 수 있어 argparse가 옵션으로 읽었다. 이제 명령은 공개 신고 번호로 찾는다."""
        self.post()
        rv = Review.objects.get()
        self.assertRegex(rv.report_no, r"^R-[A-Z2-9]{8}$")
        call_command("partners", "review-hide", rv.report_no, "--reason", "신고", stdout=StringIO())
        self.assertTrue(Review.objects.get().is_hidden)

    def test_phone_with_other_separators_refused(self):
        for text in ("010–1234–5678", "010—1234—5678", "010/1234/5678", "010~1234~5678", "010_1234_5678", "010·1234·5678"):
            self.assertEqual(self.post(text=text).status_code, 400, text)

    def test_money_is_not_a_phone_number(self):
        self.assertEqual(self.post(text="1000000원에 해 줬어요").status_code, 303)

    def test_date_is_not_a_phone_number(self):
        self.assertEqual(self.post(text="2026.10.29 답사가 좋았어요").status_code, 303)

    def test_own_school_name_refused_in_text(self):
        """화면은 '학교는 가린다'고 약속하는데 한 줄에 쓴 자기 학교 이름은 그대로 공개된다."""
        for text in ("새솔초등학교 4학년 잘 해 줬어요", "새솔초 답사 좋았어요", "새 솔 초 등 학 교"):
            r = self.post(text=text)
            self.assertEqual(r.status_code, 400, text)
            self.assertContains(r, "사람 이름이나 학교 이름", status_code=400)

    def test_label_particle_after_vowel_or_rieul(self):
        """경고 문구에 조사 오류(서울으로만)가 없다."""
        _, space = school_space(5, "서울맹학교", "B10")
        _, c = teacher(space, "sp", "맹학교샘")
        self.assertNotContains(c.get(self.url), "</b>으로만")
