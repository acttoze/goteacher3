"""N1 독립 검증(gt-qa). tests.py에 없는 경계만: prototype vInArea 대조, NUL 경로, slug 길이, 비공개 누출, 하루 경계, 회원 삭제 뒤."""
import re
import time
import xml.etree.ElementTree as ET
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from config.test_ratelimit import RL_CACHES
from hwpfiles.retention import purge
from spaces.models import Member, School, Space

from .models import CATS, Partner, Suggestion

User = get_user_model()
GOOD = {"name": "참된인쇄", "cat": CATS[8], "contact": "02-123-4567", "note": "납기"}
V_AREAS = ["서울", "경기", "인천", "강원", "충청", "전라", "경상", "제주"]  # app.js V_AREAS('전체' 제외)
AREAS = ["전국", "경기 · 인천", "서울", "강원", "경기", "경기 · 서울", "수도권",  # vendors.js에 실제로 있는 값
         "대전", "세종", "충남", "충북", "전남", "전북", "광주", "경남", "경북", "부산", "대구", "울산", "제주", "충청", "전라", "경상",
         "서울 · 부산", "수도권 · 강원"]


def v_in_area(area, sel):
    """prototype/assets/app.js vInArea를 그대로 옮긴 것(전체 제외)."""
    if area == "전국":
        return True
    if "수도권" in area and re.search("서울|경기|인천", sel):
        return True
    return (sel in area or (sel == "충청" and bool(re.search("충남|충북|대전|세종", area)))
            or (sel == "전라" and bool(re.search("전남|전북|광주", area)))
            or (sel == "경상" and bool(re.search("경남|경북|부산|대구|울산", area))))


def partner(name="참된인쇄", **kw):
    kw.setdefault("cat", CATS[0])
    kw.setdefault("area", "전국")
    kw.setdefault("desc", "소개 글")
    kw.setdefault("is_public", True)
    return Partner.objects.create(name=name, **kw)


def cmd(*args):
    out = StringIO()
    call_command("partners", *args, stdout=out)
    return out.getvalue()


@override_settings(CACHES=RL_CACHES)
class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        self.grade = Space.objects.create(school=self.school, grade=4, name="새솔초 4학년")
        self.user = User.objects.create(username="google:1", first_name="송민재")
        self.member = Member.objects.create(space=self.grade, user=self.user, name="송민재", cls="4-1")
        self.mc = Client()
        self.mc.force_login(self.user)


class PrototypeAreaTests(Base):
    def test_area_filter_same_as_v_in_area_for_every_pair(self):
        for i, a in enumerate(AREAS):
            partner(name=f"지역{i:02d}", area=a)
        for sel in V_AREAS:
            r = self.client.get("/partners", {"area": sel})
            got = set()
            for pg in range(1, r.context["page_obj"].paginator.num_pages + 1):
                got |= {p.area for p in self.client.get("/partners", {"area": sel, "page": pg}).context["page_obj"].object_list}
            self.assertEqual(got, {a for a in AREAS if v_in_area(a, sel)}, sel)

    def test_cat_and_area_and_search_combine(self):
        partner(name="버스 서울", cat="전세버스", area="서울")
        partner(name="버스 부산", cat="전세버스", area="부산")
        partner(name="인쇄 서울", cat="인쇄·현수막", area="서울")
        r = self.client.get("/partners", {"cat": "전세버스", "area": "서울", "q": "버스"})
        self.assertEqual([p.name for p in r.context["page_obj"].object_list], ["버스 서울"])

    def test_last_page_keeps_q_and_area_in_prev_link(self):
        for i in range(20):
            partner(name=f"가 {i:02d}", area="서울")
        r = self.client.get("/partners", {"q": "가", "area": "서울", "page": "99999"})
        self.assertEqual(r.context["page_obj"].number, 2)
        self.assertIn("q=", r.context["prev_href"])
        self.assertIn("area=", r.context["prev_href"])
        self.assertEqual(r.context["next_href"], "")


class OddInputTests(Base):
    def test_nul_and_broken_paths_are_404_not_500(self):
        c = Client(raise_request_exception=False)
        for path in ("/partners/%00", "/partners/a%00b", "/partners/%FF", "/partners/%ED%A0%80", "/partners/..%2f..%2fetc",
                     "/partners/" + "가" * 500):
            self.assertEqual(c.get(path).status_code, 404, path)

    def test_odd_query_values_never_500(self):
        partner()
        c = Client(raise_request_exception=False)
        for qs in ("q=%FF%FE", "cat=%00", "area=%00&cat=%00&q=%00&page=%00", "page=-0", "page=1.5", "page=%EF%BC%91", "q=" + "%25" * 300,
                   "q=%F0%9F%9A%8C", "cat=전세버스&cat=없음", "area[]=서울"):
            self.assertEqual(c.get(f"/partners?{qs}").status_code, 200, qs)

    def test_suggest_get_with_odd_sent_and_head_options(self):
        self.assertEqual(self.mc.get("/partners/suggest?sent=%00").status_code, 200)
        self.assertEqual(self.mc.head("/partners/suggest").status_code, 200)
        self.assertEqual(self.client.head("/partners").status_code, 200)
        for m in (self.client.put, self.client.delete, self.client.patch):
            self.assertEqual(m("/partners").status_code, 405)
            self.assertEqual(m("/partners/suggest").status_code, 405)


class SlugTests(Base):
    def test_long_name_duplicates_fit_the_column(self):
        name = "가" * 100
        slugs = {partner(name=name).slug for _ in range(12)}
        self.assertEqual(len(slugs), 12)
        self.assertTrue(all(len(s) <= 100 for s in slugs))

    def test_symbol_only_and_reserved_names_have_working_pages(self):
        for name in ("🚌", "!!!", "suggest", "SUGGEST", "<b>", "a/b", "../x", "가?나#다", "form"):
            p = partner(name=name)
            self.assertTrue(p.slug)
            self.assertNotEqual(p.slug, "suggest")
            self.assertEqual(self.client.get(f"/partners/{p.slug}").status_code, 200, name)
            self.assertContains(self.client.get("/partners", {"q": name}), f"/partners/{p.slug}")
        root = ET.fromstring(self.client.get("/sitemap.xml").content)  # 이상한 이름에도 XML이 깨지지 않는다
        self.assertGreater(len(root), 9)


class PrivateLeakTests(Base):
    def test_only_private_partners_means_empty_state_and_no_count(self):
        p = partner(name="숨긴곳", is_public=False, desc="숨은소개", phone="02-999", url="https://secret.example.com/")
        r = self.client.get("/partners", {"q": "숨긴"})
        self.assertContains(r, "아직 등록된 업체가 없어요")
        for s in ("숨은소개", "02-999", "secret.example.com", p.slug):
            self.assertNotContains(r, s)
        self.assertNotIn(p.slug, self.client.get("/sitemap.xml").content.decode())

    def test_hide_removes_everywhere_and_publish_restores(self):
        cmd("add", "--name=오감", f"--cat={CATS[0]}", "--area=전국", "--desc=소개", "--public")
        p = Partner.objects.get()
        before = self.client.get("/sitemap.xml").content.decode()
        self.assertEqual(before.count("<url>"), 4)
        cmd("hide", p.slug)
        self.assertEqual(self.client.get("/sitemap.xml").content.decode().count("<url>"), 3)
        self.assertNotContains(self.client.get("/partners"), "오감")
        self.assertEqual(self.mc.get(f"/partners/{p.slug}").status_code, 404)
        cmd("publish", p.slug)
        self.assertEqual(self.client.get("/sitemap.xml").content.decode().count("<url>"), 4)


class LinkTests(Base):
    def test_every_scheme_trick_is_refused_by_command(self):
        for bad in ("javascript:alert(1)", " javascript:alert(1)", "javascript://x.example.com/%0aalert(1)", "JAVASCRIPT:alert(1)", "data:text/html;base64,AAAA",
                    "file:///etc/passwd", "//evil.example.com/", "http://", "https://exa mple.com", "x" * 400):
            with self.assertRaises(CommandError, msg=bad):
                cmd("add", "--name=가", f"--cat={CATS[0]}", "--area=전국", "--desc=소개", f"--url={bad}")
        self.assertEqual(Partner.objects.count(), 0)

    def test_quote_in_url_is_escaped_in_href(self):
        cmd("add", "--name=따옴표", f"--cat={CATS[0]}", "--area=전국", "--desc=소개", "--public", '--url=https://a.example.com/"onmouseover="x')
        h = self.client.get(f"/partners/{Partner.objects.get().slug}").content.decode()
        self.assertNotIn('"onmouseover="', h)
        self.assertIn("&quot;onmouseover=&quot;x", h)

    def test_broken_url_in_db_does_not_500(self):
        p = partner(url="http://[bad")
        c = Client(raise_request_exception=False)
        self.assertEqual(c.get(f"/partners/{p.slug}").status_code, 200)

    def test_contact_empty_says_so_and_no_link(self):
        p = partner()
        r = self.client.get(f"/partners/{p.slug}")
        self.assertContains(r, "등록된 연락처가 없어요")
        self.assertNotContains(r, 'target="_blank"')

    def test_all_blank_target_links_have_rel(self):
        p = partner(url="https://a.example.com/", phone="02-1")
        h = self.client.get(f"/partners/{p.slug}").content.decode()
        for a in re.findall(r"<a [^>]*target=\"_blank\"[^>]*>", h):
            self.assertIn("noopener", a)


class SuggestEdgeTests(Base):
    def test_limit_applies_across_sessions_of_one_member_and_resets_next_day(self):
        other = Client()
        other.force_login(self.user)
        for i in range(5):
            self.assertEqual((self.mc if i % 2 else other).post("/partners/suggest", GOOD).status_code, 303)
        self.assertEqual(other.post("/partners/suggest", GOOD).status_code, 429)
        self.assertEqual(self.mc.post("/partners/suggest", GOOD).status_code, 429)
        self.assertEqual(Suggestion.objects.count(), 5)
        with mock.patch("config.ratelimit.time.time", return_value=time.time() + 86401):
            self.assertEqual(self.mc.post("/partners/suggest", GOOD).status_code, 303)

    def test_limit_holds_when_requests_overlap(self):
        """요청이 겹쳐도 하루 5건. 첫 요청이 저장하는 사이에 같은 회원의 요청 5건이 끼어드는 상황을 그대로 만든다."""
        real_save, nested = Suggestion.save, []

        def save(obj, *a, **kw):
            if not nested:
                nested.append(1)
                for _ in range(5):
                    self.mc.post("/partners/suggest", GOOD)
            real_save(obj, *a, **kw)

        with mock.patch.object(Suggestion, "save", save):
            self.mc.post("/partners/suggest", GOOD)
        self.assertLessEqual(Suggestion.objects.count(), 5)

    def test_user_without_space_sees_list_and_form(self):
        u = User.objects.create(username="google:9", first_name="혼자샘")
        c = Client()
        c.force_login(u)
        for path in ("/partners", "/partners/suggest"):
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertContains(r, "혼자샘 선생님")

    def test_forged_by_member_does_not_change_author(self):
        u = User.objects.create(username="google:2", first_name="다른샘")
        other = Member.objects.create(space=self.grade, user=u, name="다른샘", cls="4-2")
        self.mc.post("/partners/suggest", {**GOOD, "by_member": other.pk, "created_at": "2000-01-01", "slug": "x"})
        s = Suggestion.objects.get()
        self.assertEqual(s.by_member, self.member)
        self.assertEqual(s.created_at.year, timezone.now().year)

    def test_exact_max_length_accepted_and_one_more_refused(self):
        ok = {**GOOD, "name": "가" * 100, "contact": "나" * 100, "note": "다" * 200}
        self.assertEqual(self.mc.post("/partners/suggest", ok).status_code, 303)
        self.assertEqual(self.mc.post("/partners/suggest", {**ok, "contact": "나" * 101}).status_code, 400)
        self.assertEqual(Suggestion.objects.count(), 1)

    def test_huge_body_does_not_500(self):
        c = Client(raise_request_exception=False)
        c.force_login(self.user)
        self.assertIn(c.post("/partners/suggest", {**GOOD, "note": "가" * 2_000_000}).status_code, (400, 413))
        self.assertEqual(Suggestion.objects.count(), 0)

    def test_terminal_tricks_all_removed_from_operator_list(self):
        nasty = "앞\x1b]0;x\x07\x9b2J‮  ‏\r\n\t뒤"
        Suggestion.objects.create(name=nasty, cat=CATS[0], contact=nasty, note=nasty, by_name=nasty, school=nasty)
        out = cmd("suggestions")
        self.assertEqual(out.count("\n"), 1)
        for ch in "\x1b\x07\x9b‮  ‏\r\t":
            self.assertNotIn(ch, out)
        self.assertIn("앞", out)

    def test_suggestion_with_xss_shows_nowhere_and_escapes_on_retry(self):
        x = '"><script>alert(1)</script>'
        r = self.mc.post("/partners/suggest", {**GOOD, "cat": "", "name": x, "contact": x, "note": x})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("<script>alert", r.content.decode())

    def test_deleted_member_keeps_row_until_purge(self):
        self.mc.post("/partners/suggest", GOOD)
        self.member.delete()
        s = Suggestion.objects.get()
        self.assertIsNone(s.by_member)
        self.assertEqual(self.mc.post("/partners/suggest", GOOD).status_code, 303)  # 공간이 없어도 추천은 된다

    def test_partner_login_required_redirect_comes_back(self):
        r = self.client.get("/partners/suggest")
        self.assertEqual(r["Location"], "/start?next=/partners/suggest")
        self.assertContains(self.client.get(r["Location"]), 'name="next" value="/partners/suggest"')


class PurgeEdgeTests(Base):
    def test_purge_never_touches_partners(self):
        old = partner(name="오래된 공개")
        hidden = partner(name="오래된 비공개", is_public=False)
        Partner.objects.update(created_at=timezone.now() - timedelta(days=3000))
        purge(timezone.now())
        self.assertEqual(Partner.objects.filter(pk__in=[old.pk, hidden.pk]).count(), 2)

    def test_handled_today_is_deleted_at_next_purge(self):
        self.mc.post("/partners/suggest", GOOD)
        self.assertEqual(purge(timezone.now())["suggestions"], 0)
        cmd("suggestion-done", str(Suggestion.objects.get().pk))
        self.assertEqual(purge(timezone.now())["suggestions"], 1)
        self.assertEqual(Suggestion.objects.count(), 0)


class SeedAndWordingTests(Base):
    def test_seed_names_nowhere_in_app_or_static_any_text_file(self):
        src = (Path(settings.REPO_DIR) / "prototype" / "assets" / "vendors.js").read_text(encoding="utf-8")
        block = src[src.index("TR.seedVendors"):]
        names = re.findall(r"name: '([^']+)'", block)
        self.assertEqual(len(names), 13)
        me = Path(__file__).resolve()
        for root in (Path(settings.BASE_DIR), Path(settings.REPO_DIR) / "web" / "static"):
            for p in root.rglob("*"):
                if not p.is_file() or p.resolve() == me or p.name == "db.sqlite3" or ".venv" in p.parts or "__pycache__" in p.parts or p.stat().st_size > 2_000_000:
                    continue
                try:
                    text = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for n in names:
                    self.assertNotIn(n, text, p)

    def test_no_old_name_or_fake_stats_on_any_page(self):
        p = partner(phone="02-1", url="https://a.example.com/", tags="가")
        pages = ["/partners", f"/partners/{p.slug}", "/partners/suggest", "/partners/suggest?sent=1"]
        for c in (self.mc, ):
            for u in pages:
                h = c.get(u).content.decode()
                for word in ("티로드", "T-Road", "학교 0곳", "곳 이용", "별점"):
                    self.assertNotIn(word, h, u)
        for u in pages[:2]:
            self.assertNotIn("티로드", self.client.get(u).content.decode(), u)


class QueryCountTests(Base):
    def test_detail_sitemap_and_filtered_list_flat_at_100(self):
        def counts():
            out = []
            first = Partner.objects.order_by("pk").first()
            for c, u in ((self.client, f"/partners/{first.slug}"), (self.mc, f"/partners/{first.slug}"), (self.client, "/sitemap.xml"),
                         (self.client, "/partners?area=서울&q=곳"), (self.mc, "/partners?cat=에듀테크&page=2"), (self.mc, "/partners/suggest")):
                with CaptureQueriesContext(connection) as q:
                    self.assertEqual(c.get(u).status_code, 200, u)
                out.append(len(q))
            return out

        for i in range(40):
            partner(name=f"곳{i:03d}", tags="가, 나", cat="에듀테크", url="https://a.example.com/")
        few = counts()
        for i in range(60):
            partner(name=f"뒷곳{i:03d}", tags="가, 나", cat="에듀테크", url="https://a.example.com/")
        self.assertEqual(counts(), few)
