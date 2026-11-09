"""N1 협력업체: 가상 데이터 없이 빈 상태 정상, 업체가 스스로 등록 못 함.

- 등록·공개는 관리 명령 partners로만(리드 결정: /admin 경로는 열지 않는다).
"""
import re
from datetime import timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import unquote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.exceptions import ValidationError
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
VENDORS_JS = Path(settings.REPO_DIR) / "prototype" / "assets" / "vendors.js"
EMPTY = "아직 등록된 업체가 없어요. 좋은 업체를 추천해 주세요"
SUGGEST = 'href="/partners/suggest"'
GOOD = {"name": "참된인쇄", "cat": "인쇄·현수막", "contact": "02-123-4567", "note": "납기를 잘 지켜요"}


def seed_names():
    return re.findall(r"\bname: '([^']+)'", VENDORS_JS.read_text(encoding="utf-8"))


def partner(name="참된인쇄", **kw):
    kw.setdefault("cat", CATS[0])
    kw.setdefault("area", "전국")
    kw.setdefault("desc", "소개 글")
    kw.setdefault("is_public", True)
    return Partner.objects.create(name=name, **kw)


@override_settings(CACHES=RL_CACHES)
class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        self.grade = Space.objects.create(school=self.school, grade=4, name="새솔초 4학년")
        self.user = User.objects.create(username="google:1", first_name="송민재")
        self.member = Member.objects.create(space=self.grade, user=self.user, name="송민재", cls="4-1")
        self.guest = User.objects.create(username="guest:abc", first_name="손님")
        Member.objects.create(space=self.grade, user=self.guest, name="손님", cls="4-2")
        self.mc = Client()
        self.mc.force_login(self.user)
        self.gc = Client()
        self.gc.force_login(self.guest)


class EmptyAndSeedTests(Base):
    def test_empty_list_anonymous(self):
        r = self.client.get("/partners")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, EMPTY)
        self.assertNotContains(r, SUGGEST)

    def test_empty_list_member_and_guest_button(self):
        r = self.mc.get("/partners")
        self.assertContains(r, EMPTY)
        self.assertContains(r, SUGGEST)
        self.assertNotContains(self.gc.get("/partners"), SUGGEST)

    def test_filtered_empty_is_not_the_first_run_message(self):
        partner(cat=CATS[0])
        r = self.client.get("/partners", {"cat": CATS[1]})
        self.assertNotContains(r, EMPTY)
        self.assertContains(r, "조건에 맞는 업체가 없어요")

    def test_prototype_categories_only(self):
        src = VENDORS_JS.read_text(encoding="utf-8")
        self.assertEqual(CATS, re.findall(r"'([^']+)'", re.search(r"TR\.vendorCats = \[(.*?)\]", src).group(1)))

    def test_no_seed_vendor_in_db_or_response(self):
        names = seed_names()
        self.assertGreater(len(names), 5)  # 정규식이 실제로 이름을 찾았는지
        self.assertEqual(Partner.objects.count(), 0)
        self.assertEqual(Suggestion.objects.count(), 0)
        body = self.client.get("/partners").content.decode() + self.client.get("/sitemap.xml").content.decode()
        for n in names:
            self.assertNotIn(n, body)

    def test_no_seed_vendor_in_source(self):
        names = seed_names()
        roots = [Path(settings.BASE_DIR), Path(settings.REPO_DIR) / "web" / "static"]
        files = [p for root in roots for p in root.rglob("*")
                 if p.suffix in {".py", ".html", ".xml", ".json", ".yaml", ".yml", ".txt", ".css"} and p.is_file()]
        self.assertGreater(len(files), 50)
        for p in files:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for n in names:
                self.assertNotIn(n, text, f"{p}에 prototype 업체 이름")


class ListTests(Base):
    def test_guide_line_always(self):
        guide = "좋은 업체를 아시면 로그인한 뒤 추천해 주세요"
        for has_partner in (False, True):
            if has_partner:
                partner()
            self.assertContains(self.client.get("/partners"), f'<a href="/start?next=/partners/suggest">{guide}</a>')
            self.assertContains(self.gc.get("/partners"), f'<a href="/start?next=/partners/suggest">{guide}</a>')
            r = self.mc.get("/partners")
            self.assertNotContains(r, guide)
            self.assertContains(r, SUGGEST)

    def test_history_script_only_on_list(self):
        p = partner()
        self.assertContains(self.client.get("/partners"), "js/partners.js")
        self.assertNotContains(self.client.get(f"/partners/{p.slug}"), "js/partners.js")

    def test_card_shows_fields_and_only_public(self):
        partner(name="공개업체", cat="전세버스", area="경기", desc="버스 대절", tags="학교 전문, 보험")
        partner(name="숨긴업체", is_public=False)
        r = self.client.get("/partners")
        for s in ("공개업체", "전세버스", "경기", "버스 대절", "학교 전문", "보험"):
            self.assertContains(r, s)
        self.assertNotContains(r, "숨긴업체")

    def test_sorted_by_name(self):
        for n in ("다람", "가람", "나람"):
            partner(name=n)
        h = self.client.get("/partners").content.decode()
        self.assertLess(h.index("가람"), h.index("나람"))
        self.assertLess(h.index("나람"), h.index("다람"))

    def test_category_filter(self):
        partner(name="버스회사", cat="전세버스")
        partner(name="인쇄회사", cat="인쇄·현수막")
        r = self.client.get("/partners", {"cat": "전세버스"})
        self.assertContains(r, "버스회사")
        self.assertNotContains(r, "인쇄회사")

    def test_area_filter_follows_prototype(self):
        for name, area in [("전국사", "전국"), ("수도권사", "수도권"), ("서울사", "서울"), ("대전사", "대전"), ("부산사", "부산"),
                           ("경기인천사", "경기 · 인천")]:
            partner(name=name, area=area)

        def seen(a):
            h = self.client.get("/partners", {"area": a}).content.decode()
            return {n for n in ("전국사", "수도권사", "서울사", "대전사", "부산사", "경기인천사") if n in h}

        self.assertEqual(seen("서울"), {"전국사", "수도권사", "서울사"})
        self.assertEqual(seen("인천"), {"전국사", "수도권사", "경기인천사"})
        self.assertEqual(seen("충청"), {"전국사", "대전사"})
        self.assertEqual(seen("경상"), {"전국사", "부산사"})
        self.assertEqual(seen("제주"), {"전국사"})

    def test_search_name_only_all_words(self):
        partner(name="참된 인쇄 공방", desc="소개")
        partner(name="다른회사", desc="참된 인쇄")
        r = self.client.get("/partners", {"q": "인쇄 참된"})
        self.assertContains(r, "참된 인쇄 공방")
        self.assertNotContains(r, "다른회사")

    def test_pagination_keeps_filters(self):
        for i in range(25):
            partner(name=f"업체{i:02d}", cat="전세버스")
        r = self.client.get("/partners", {"cat": "전세버스"})
        self.assertEqual(len(r.context["page_obj"].object_list), 18)
        self.assertContains(r, "page=2")
        self.assertContains(r, "cat=%EC%A0%84%EC%84%B8%EB%B2%84%EC%8A%A4")
        r2 = self.client.get("/partners", {"cat": "전세버스", "page": 2})
        self.assertContains(r2, "업체24")
        self.assertNotContains(r2, "업체00")

    def test_odd_params_never_500(self):
        partner()
        for params in ({"page": "abc"}, {"page": "-1"}, {"page": "0"}, {"page": "99999"}, {"page": "1e9"}, {"page": "9" * 5000},
                       {"cat": "<script>"}, {"cat": "\x00"}, {"area": "없는곳"}, {"area": "\x00"}, {"q": "\x00"}, {"q": "가\x00나"},
                       {"q": "%_'\""}, {"q": "가" * 5000}, {"cat": ["a", "b"]}):
            r = self.client.get("/partners", params)
            self.assertEqual(r.status_code, 200, params)

    def test_nul_never_reaches_query(self):
        self.assertEqual(self.client.get("/partners", {"q": "가\x00나"}).context["q"], "가나")

    def test_query_count_flat(self):
        def count(c):
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(c.get("/partners").status_code, 200)
            return len(q)

        for i in range(10):
            partner(name=f"앞{i:03d}", tags="가, 나")
        few_a, few_m = count(self.client), count(self.mc)
        for i in range(90):
            partner(name=f"뒤{i:03d}", tags="가, 나")
        self.assertEqual(count(self.client), few_a)
        self.assertEqual(count(self.mc), few_m)

    def test_logged_in_page_not_cached_public_page_cacheable(self):
        self.assertIn("no-cache", self.mc.get("/partners").headers.get("Cache-Control", ""))
        self.assertNotIn("no-cache", self.client.get("/partners").headers.get("Cache-Control", ""))


class DetailTests(Base):
    def test_detail_anonymous(self):
        p = partner(name="버스회사", cat="전세버스", desc="버스 대절", phone="02-111-2222", url="https://bus.example.com/a")
        r = self.client.get(f"/partners/{p.slug}")
        self.assertEqual(r.status_code, 200)
        for s in ("버스회사", "버스 대절", "02-111-2222"):
            self.assertContains(r, s)
        self.assertContains(r, 'href="https://bus.example.com/a"')
        self.assertContains(r, 'rel="nofollow noopener"')

    def test_private_and_missing_404_for_everyone(self):
        p = partner(is_public=False)
        for c in (self.client, self.mc, self.gc):
            self.assertEqual(c.get(f"/partners/{p.slug}").status_code, 404)
            self.assertEqual(c.get("/partners/없는업체").status_code, 404)

    def test_sequential_id_is_not_an_address(self):
        p = partner()
        self.assertEqual(self.client.get(f"/partners/{p.pk}").status_code, 404)

    def test_slug_unique_and_auto(self):
        a, b = partner(name="같은 이름"), partner(name="같은 이름")
        self.assertEqual(a.slug, "같은-이름")
        self.assertEqual(b.slug, "같은-이름-2")
        c = partner(name="Suggest")  # /partners/suggest 와 겹치지 않게
        self.assertNotEqual(c.slug, "suggest")
        self.assertEqual(self.client.get(f"/partners/{c.slug}").status_code, 200)

    def test_only_http_urls_validate(self):
        for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "ftp://a.example.com/", "vbscript:x"):
            with self.assertRaises(ValidationError, msg=bad):
                Partner(name="가", cat=CATS[0], area="전국", desc="가", url=bad).full_clean(exclude=["slug"])
        for ok in ("http://a.example.com/", "https://a.example.com/x?y=1"):
            Partner(name="가", cat=CATS[0], area="전국", desc="가", url=ok).full_clean(exclude=["slug"])

    def test_bad_url_in_db_never_becomes_a_link(self):
        for bad in ("javascript:alert(1)", " javascript:alert(1)", "java\nscript:alert(1)", "data:text/html,x"):
            p = partner(name=f"나쁜{bad[:3]}", url=bad)
            r = self.client.get(f"/partners/{p.slug}")
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, 'rel="nofollow noopener"')

    def test_xss_escaped(self):
        x = "<script>alert(1)</script>"
        p = partner(name=x, desc=x, area=x, tags=x, phone=x)
        for path_, params in (("/partners", {}), ("/partners", {"q": x}), (f"/partners/{p.slug}", {})):
            r = self.client.get(path_, params)
            self.assertNotContains(r, "<script>alert")
        self.assertContains(self.client.get("/partners"), "&lt;script&gt;")


class SuggestTests(Base):
    def test_anonymous_redirected_and_not_saved(self):
        r = self.client.post("/partners/suggest", GOOD)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/start?"))
        self.assertEqual(self.client.get("/partners/suggest").status_code, 302)
        self.assertEqual(Suggestion.objects.count(), 0)

    def test_guest_refused(self):
        self.assertEqual(self.gc.get("/partners/suggest").status_code, 403)
        self.assertEqual(self.gc.post("/partners/suggest", GOOD).status_code, 403)
        self.assertEqual(Suggestion.objects.count(), 0)

    def test_form_says_operator_checks(self):
        r = self.mc.get("/partners/suggest")
        self.assertContains(r, "운영자가 확인한 뒤 올립니다. 업체가 스스로 등록·광고할 수는 없어요.")

    def test_member_suggestion_waits(self):
        r = self.mc.post("/partners/suggest", GOOD)
        self.assertEqual(r.status_code, 303)
        s = Suggestion.objects.get()
        self.assertEqual((s.name, s.cat, s.contact, s.note), (GOOD["name"], GOOD["cat"], GOOD["contact"], GOOD["note"]))
        self.assertEqual((s.by_member, s.by_name, s.school), (self.member, "송민재", "새솔초등학교"))
        self.assertEqual(s.status, Suggestion.Status.PENDING)
        self.assertContains(self.mc.get(r["Location"]), "운영자가 확인한 뒤")

    def test_suggestion_is_nowhere_public(self):
        self.mc.post("/partners/suggest", GOOD)
        self.assertEqual(Partner.objects.count(), 0)
        for c in (self.client, self.mc):
            for u in ("/partners", "/sitemap.xml", "/partners?q=참된인쇄"):
                h = c.get(u).content.decode()
                self.assertNotIn(GOOD["name"], h.replace('value="참된인쇄"', ""))
                self.assertNotIn(GOOD["contact"], h)

    def test_cannot_publish_by_forging_fields(self):
        self.mc.post("/partners/suggest", {**GOOD, "is_public": "1", "status": "accepted", "by_name": "거짓", "school": "거짓"})
        s = Suggestion.objects.get()
        self.assertEqual((s.status, s.by_name, s.school), (Suggestion.Status.PENDING, "송민재", "새솔초등학교"))
        self.assertEqual(Partner.objects.count(), 0)

    def test_school_space_member_and_no_space_member(self):
        sch = Space.objects.create(school=self.school, kind="school", grade=0, name="새솔초")
        u = User.objects.create(username="google:2", first_name="학교샘")
        Member.objects.create(space=sch, user=u, name="학교샘", cls="4-3")
        c = Client()
        c.force_login(u)
        self.assertEqual(c.post("/partners/suggest", GOOD).status_code, 303)
        u2 = User.objects.create(username="google:3", first_name="혼자샘")
        c2 = Client()
        c2.force_login(u2)
        self.assertEqual(c2.post("/partners/suggest", GOOD).status_code, 303)
        s = Suggestion.objects.get(by_name="혼자샘")
        self.assertIsNone(s.by_member)
        self.assertEqual(s.school, "")

    def test_required_and_choices(self):
        for bad in ({**GOOD, "name": " "}, {**GOOD, "cat": "없는 분류"}, {**GOOD, "contact": ""}, {**GOOD, "name": "가" * 101}, {**GOOD, "note": "가" * 201},
                    {**GOOD, "name": "가\x00나"}):
            self.assertEqual(self.mc.post("/partners/suggest", bad).status_code, 400, bad)
        self.assertEqual(Suggestion.objects.count(), 0)
        self.assertEqual(self.mc.post("/partners/suggest", {**GOOD, "note": ""}).status_code, 303)

    def test_five_a_day_per_member(self):
        for _ in range(5):
            self.assertEqual(self.mc.post("/partners/suggest", GOOD).status_code, 303)
        self.assertEqual(self.mc.post("/partners/suggest", GOOD).status_code, 429)
        self.assertEqual(Suggestion.objects.count(), 5)
        u = User.objects.create(username="google:9", first_name="다른샘")
        c = Client()
        c.force_login(u)
        self.assertEqual(c.post("/partners/suggest", GOOD).status_code, 303)  # 다른 회원은 따로 센다

    def test_invalid_posts_do_not_use_up_the_limit(self):
        for _ in range(6):
            self.mc.post("/partners/suggest", {**GOOD, "name": ""})
        self.assertEqual(self.mc.post("/partners/suggest", GOOD).status_code, 303)

    def test_csrf_enforced(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.user)
        self.assertEqual(c.post("/partners/suggest", GOOD).status_code, 403)
        self.assertEqual(Suggestion.objects.count(), 0)

    def test_values_escaped_on_error_page(self):
        r = self.mc.post("/partners/suggest", {**GOOD, "cat": "", "name": "<script>alert(1)</script>"})
        self.assertNotContains(r, "<script>alert", status_code=400)


class CommandTests(Base):
    """공개는 운영자 명령으로만. 화면에는 등록·공개 경로가 없다."""

    def run_cmd(self, *args):
        out = StringIO()
        call_command("partners", *args, stdout=out)
        return out.getvalue()

    def add(self, *extra):
        return self.run_cmd("add", "--name=운영자등록", f"--cat={CATS[0]}", "--area=전국", "--desc=소개", *extra)

    def test_no_web_path_to_register_or_publish(self):
        p = partner(is_public=False)
        for c in (self.client, self.mc, self.gc):
            self.assertEqual(c.get("/admin/").status_code, 404)
            self.assertEqual(c.post("/partners", {"name": "가", "is_public": "on"}).status_code, 405)
            self.assertEqual(c.post(f"/partners/{p.slug}", {"is_public": "on"}).status_code, 405)
        p.refresh_from_db()
        self.assertFalse(p.is_public)
        self.assertEqual(Partner.objects.count(), 1)

    def test_add_private_by_default_then_publish_and_hide(self):
        self.add("--phone=02-1", "--url=https://a.example.com/", "--tags=가, 나")
        p = Partner.objects.get()
        self.assertEqual((p.name, p.phone, p.url, p.tag_list, p.is_public), ("운영자등록", "02-1", "https://a.example.com/", ["가", "나"], False))
        self.assertEqual(self.client.get(f"/partners/{p.slug}").status_code, 404)
        self.run_cmd("publish", p.slug)
        self.assertEqual(self.client.get(f"/partners/{p.slug}").status_code, 200)
        self.run_cmd("hide", p.slug)
        self.assertEqual(self.client.get(f"/partners/{p.slug}").status_code, 404)

    def test_add_public(self):
        self.add("--public")
        self.assertTrue(Partner.objects.get().is_public)

    def test_bad_values_error_and_save_nothing(self):
        for bad in ("--url=javascript:alert(1)", "--url=JaVaScRiPt:alert(1)", "--url=ftp://a.example.com/", "--url=data:text/html,x", "--url=가나다"):
            with self.assertRaises(CommandError, msg=bad):
                self.add(bad)
        with self.assertRaises(CommandError):
            self.run_cmd("add", "--name=가", "--cat=없는 분류", "--area=전국", "--desc=소개")
        with self.assertRaises(CommandError):
            self.run_cmd("add", "--name=  ", f"--cat={CATS[0]}", "--area=전국", "--desc=소개")
        with self.assertRaises(CommandError):
            self.run_cmd("add", "--name=가")
        self.assertEqual(Partner.objects.count(), 0)

    def test_missing_targets_error(self):
        for args in (("publish", "없음"), ("hide", "없음"), ("suggestion-done", "999999")):
            with self.assertRaises(CommandError, msg=args):
                self.run_cmd(*args)
        with self.assertRaises(CommandError):
            self.run_cmd("suggestion-done", "abc")
        with self.assertRaises(CommandError):
            self.run_cmd()

    def test_suggestions_lists_pending_only_on_one_line_without_control_chars(self):
        self.mc.post("/partners/suggest", {**GOOD, "note": "좋아요\x1b[2J\n거짓 줄"})
        done = Suggestion.objects.create(name="처리된곳", cat=CATS[0], contact="1", by_name="가", status="accepted")
        out = self.run_cmd("suggestions")
        self.assertIn(GOOD["name"], out)
        self.assertIn(GOOD["contact"], out)
        self.assertNotIn(done.name, out)
        self.assertNotIn("\x1b", out)
        self.assertEqual(out.count("\n"), 1)

    def test_suggestion_done_never_publishes(self):
        self.mc.post("/partners/suggest", GOOD)
        s = Suggestion.objects.get()
        self.run_cmd("suggestion-done", str(s.pk))
        s.refresh_from_db()
        self.assertEqual(s.status, Suggestion.Status.ACCEPTED)
        self.assertEqual(Partner.objects.count(), 0)
        self.run_cmd("suggestion-done", str(s.pk), "--reject")
        s.refresh_from_db()
        self.assertEqual(s.status, Suggestion.Status.REJECTED)


class PurgeTests(Base):
    def make(self, name, status="pending", days=0):
        s = Suggestion.objects.create(name=name, cat=CATS[0], contact="1", by_name="가", status=status)
        Suggestion.objects.filter(pk=s.pk).update(created_at=timezone.now() - timedelta(days=days))

    def setUp(self):
        super().setUp()
        self.make("새 대기")
        self.make("거의 1년", days=364)
        self.make("1년 지남", days=366)
        self.make("올림", "accepted")
        self.make("안 올림", "rejected")

    def test_dry_run_only_counts(self):
        self.assertEqual(purge(timezone.now(), dry_run=True)["suggestions"], 3)
        self.assertEqual(Suggestion.objects.count(), 5)

    def test_handled_and_year_old_deleted(self):
        self.assertEqual(purge(timezone.now())["suggestions"], 3)
        self.assertEqual(set(Suggestion.objects.values_list("name", flat=True)), {"새 대기", "거의 1년"})

    def test_command_prints_count_only(self):
        out = StringIO()
        call_command("purge", stdout=out)
        self.assertIn("업체 추천 3건", out.getvalue())
        self.assertNotIn("올림", out.getvalue())


class SitemapAndMenuTests(Base):
    def test_sitemap_public_only(self):
        pub, hid = partner(name="공개업체"), partner(name="숨긴업체", is_public=False)
        h = self.client.get("/sitemap.xml").content.decode()
        self.assertIn(f"{settings.SITE_URL}/partners<", h)
        self.assertIn(f"/partners/{pub.slug}<", unquote(h))
        self.assertNotIn(hid.slug, unquote(h))
        self.assertIn("/forms<", h)  # 자료실은 그대로

    def test_not_blocked_from_search(self):
        p = partner()
        for u in ("/partners", f"/partners/{p.slug}"):
            self.assertNotContains(self.client.get(u), "noindex")

    def test_menu_in_grade_and_school_space(self):
        self.assertContains(self.mc.get(f"/s/{self.grade.token}/"), 'href="/partners"')
        sch = Space.objects.create(school=self.school, kind="school", grade=0, name="새솔초")
        u = User.objects.create(username="google:2", first_name="학교샘")
        Member.objects.create(space=sch, user=u, name="학교샘", cls="4-3")
        c = Client()
        c.force_login(u)
        h = c.get(f"/s/{sch.token}/today")
        self.assertContains(h, 'href="/partners"')
        self.assertNotContains(c.get("/partners"), "동학년")

    def test_shell_for_member_marks_current(self):
        self.assertContains(self.mc.get("/partners"), '<a href="/partners" aria-current="page">')

    def test_reachable_without_login(self):
        self.assertContains(self.client.get("/"), 'href="/partners"')
        self.assertContains(self.client.get("/forms"), 'href="/partners"')
