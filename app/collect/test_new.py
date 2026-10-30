"""C2 새로 모으기(표 붙여넣기) 화면: 공간 격리, 만들기 결과(Collection·Field·Row, Cell 없음), htmx 미리보기."""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from spaces.models import Member, School, Space

from .models import Cell, Collection, Field, Row

FIX = Path(__file__).parent / "fixtures" / "paste"
TSV = "반\t담임\t참가(명)\t희망 여부\t비고\n4-1\t김가람\t\t\t\n4-2\t\t\t\t\n\n합계\t\t\t\t\n"


@override_settings(SITE_URL="https://t.goteacher.xyz")
class NewCollectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.ub = User.objects.create(username="google:a"), User.objects.create(username="google:b")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        self.client.force_login(self.ua)

    def url(self, space=None, tail="c/new"):
        return f"/s/{(space or self.a).token}/{tail}"

    def create(self, space=None, **data):
        return self.client.post(self.url(space), {"title": "학예회 참가", "paste": TSV, **data})

    # ---- 공간 격리
    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.assertEqual(self.client.post(self.url(), {"title": "x", "paste": TSV}).status_code, 302)
        self.assertFalse(Collection.objects.exists())

    def test_other_space_new_404(self):
        self.assertEqual(self.client.get(self.url(self.b)).status_code, 404)
        self.assertEqual(self.create(self.b).status_code, 404)
        self.assertEqual(self.client.post(self.url(self.b, "c/new/preview"), {"paste": TSV}).status_code, 404)
        self.assertFalse(Collection.objects.exists())

    def test_other_space_made_404(self):
        self.client.force_login(self.ub)
        self.create(self.b)
        cb = Collection.objects.get()
        self.client.force_login(self.ua)
        self.assertEqual(self.client.get(self.url(self.a, f"c/{cb.key}/")).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.b, f"c/{cb.key}/")).status_code, 404)

    # ---- 화면
    def test_get_form(self):
        r = self.client.get(self.url())
        self.assertContains(r, "새로 모으기")
        self.assertContains(r, "[고티처]")
        self.assertContains(r, 'name="paste"')
        self.assertNotContains(r, "티로드")

    # ---- 만들기
    def test_create_from_tsv(self):
        r = self.create()
        c = Collection.objects.get()
        self.assertRedirects(r, self.url(tail=f"c/{c.key}/"), fetch_redirect_response=False)
        self.assertEqual((c.space, c.created_by, c.source, c.title), (self.a, self.ma, "paste", "학예회 참가"))
        self.assertEqual(c.unit_head, ["반", "담임"])
        self.assertEqual([(f.name, f.kind, f.options) for f in c.fields.all()],
                         [("참가(명)", "number", []), ("희망 여부", "select", ["예", "아니오"]), ("비고", "text", [])])
        self.assertEqual([(r.unit, r.who) for r in c.rows.all()], [("4-1", "김가람"), ("4-2", "")])
        self.assertEqual(len({r.key for r in c.rows.all()}), 2)
        self.assertFalse(Cell.objects.exists())  # 빈 칸을 미리 만들지 않는다

    def test_create_from_hwp_fixture_tsv(self):
        from .paste import html_to_tsv
        tsv = html_to_tsv((FIX / "14_two_row_header.html").read_text(encoding="utf-8"))
        self.create(paste=tsv)
        c = Collection.objects.get()
        self.assertEqual(c.fields.count(), 4)
        self.assertEqual(list(c.rows.values_list("unit", flat=True)), ["4학년 1", "4학년 2", "4학년 3"])

    def test_kind_override_and_invalid_kind(self):
        self.create(kind_0="text", kind_1="calc", kind_2="select")
        c = Collection.objects.get()
        self.assertEqual([(f.kind, f.options) for f in c.fields.all()],
                         [("text", []), ("select", ["예", "아니오"]), ("select", ["예", "아니오"])])

    def test_title_required(self):
        r = self.create(title="  ")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "제목을 적어 주세요", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_bad_paste_shows_error(self):
        r = self.create(paste="반\n담임\n4-1")
        self.assertContains(r, "칸 구분이 없어요", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_rows_and_fields_belong_to_new_collection_only(self):
        self.create()
        self.create(title="둘째")
        c1, c2 = Collection.objects.order_by("pk")
        self.assertEqual(set(Row.objects.filter(collection=c2).values_list("pos", flat=True)), {0, 1})
        self.assertEqual(Field.objects.filter(collection=c1).count(), 3)

    def test_made_page(self):
        self.create()
        c = Collection.objects.get()
        r = self.client.get(self.url(tail=f"c/{c.key}/"))
        self.assertContains(r, "학예회 참가")
        self.assertContains(r, f"https://t.goteacher.xyz/c/{c.link_token}")
        self.assertContains(r, "받는 사람 2줄")
        self.assertNotRegex(r.content.decode(), rf"/c/{c.pk}(?![\w-])")  # 순번 ID를 주소에 쓰지 않는다

    # ---- 미리보기(htmx)
    def test_preview_from_hwp_html_returns_tsv(self):
        html = (FIX / "13_design_sample.html").read_text(encoding="utf-8")
        r = self.client.post(self.url(tail="c/new/preview"), {"html": html, "paste": "예전 글자"})
        self.assertContains(r, 'id="tsvOut"')
        self.assertContains(r, "반\t담임\t참가(명)")
        self.assertContains(r, 'name="kind_0"')
        self.assertContains(r, '<div id="aud" hx-swap-oob="true">')  # 받는 사람 카드·쪽지 칸도 함께 갱신
        self.assertContains(r, "4-5")
        self.assertNotContains(r, "<TABLE")
        self.assertNotContains(r, "HStyle0")
        self.assertNotContains(r, "결재")

    def test_preview_escapes_pasted_text(self):
        r = self.client.post(self.url(tail="c/new/preview"), {"paste": "반\t<script>alert(1)</script>\n4-1\t"})
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertContains(r, "&lt;script&gt;")

    def test_preview_rowspan_bomb_cut_without_tsv(self):
        # 작은 HTML이라도 세로 병합 복사로 결과가 커지면 끊고, 바꾼 글자는 싣지 않는다
        big = "가" * 900
        html = "<table><tr>" + "".join(f'<td rowspan="1000">{big}</td>' for _ in range(60)) + "</tr>" + "<tr></tr>" * 0
        html += "".join("<tr><td>x</td></tr>" for _ in range(999)) + "</table>"
        r = self.client.post(self.url(tail="c/new/preview"), {"html": html})
        self.assertContains(r, "표가 너무 커요")
        self.assertNotContains(r, "tsvOut")
        self.assertLess(len(r.content), 20_000)

    def test_preview_html_too_long(self):
        r = self.client.post(self.url(tail="c/new/preview"), {"html": "<table>" + " " * 1_000_001})
        self.assertContains(r, "표가 너무 커요")

    def test_preview_html_without_table(self):
        r = self.client.post(self.url(tail="c/new/preview"), {"html": "<p>글</p>", "paste": ""})
        self.assertContains(r, "표를 찾지 못했어요")
