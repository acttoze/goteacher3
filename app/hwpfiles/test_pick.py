"""E5 표 고르기: 만들기 화면 '한글 양식' 탭에서 올리기 → 고르기 부분 화면 → 표 확정(POST).

불변식
- 확정은 회원·같은 공간·완료된 파일·report["tables"] 안의 (sec, index)일 때만 저장한다. CSRF 필요.
- 후보가 아닌 표(결재란 등)도 고를 수 있다(1순위 적중 약 70% → 사람이 확정).
- 확정한 위치로 hwp.schema 를 그대로 부를 수 있다(E6 칸 확인의 입력).
- 화면·JSON 응답에 문서 글자가 없다(표 번호·줄·칸 수만).
"""
import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

import hwp
from hwp.tests.test_hwp import roster
from spaces.models import Member, School, Space

from .models import SourceFile

S = SourceFile.Status


def up(name="연수.hwpx", data=None):
    return SimpleUploadedFile(name, data or roster(), content_type="application/octet-stream")


@override_settings(ROOT_URLCONF="config.urls", REDIS_URL="")
class PickTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999998", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.ub = User.objects.create(username="google:pa"), User.objects.create(username="google:pb")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, True)
        o = override_settings(MEDIA_ROOT=media)
        o.enable()
        self.addCleanup(o.disable)
        self.client.force_login(self.ua)

    def done(self, name="연수.hwpx", data=None):
        r = self.client.post(f"/s/{self.a.token}/hwp/upload?pick=1", {"file": up(name, data)})
        self.assertEqual(r.status_code, 200)
        return r, SourceFile.objects.get(name=name)

    def url(self, sf, space=None):
        return f"/s/{(space or self.a).token}/hwp/{sf.key}/pick"

    def pick(self, sf, client=None, space=None, **data):
        return (client or self.client).post(self.url(sf, space), data)

    # ---- 고르기 부분 화면
    def test_upload_pick_mode_returns_pick_part(self):
        r, sf = self.done()
        self.assertEqual(sf.status, S.DONE)
        body = r.content.decode()
        self.assertIn('id="hwpPick"', body)
        self.assertIn(f'data-pick="/s/{self.a.token}/hwp/{sf.key}/pick"', body)
        self.assertIn("다른 파일", body)
        self.assertIn("추천 · 표 2", body)  # 1순위 후보 = 문서의 두 번째 표(첫 표는 결재란)
        self.assertNotIn("hx-trigger", body)  # 끝났으면 폴링 멈춤
        tables = json.loads(body.split('id="hwpTables" type="application/json">')[1].split("</script>")[0])
        self.assertEqual([(t["no"], t["role"], t["rank"]) for t in tables], [(1, "approval", None), (2, "data", 0)])
        for word in ("담당", "성명", "명단"):  # 문서 글자 없음
            self.assertNotIn(word, body)

    def test_plain_upload_still_returns_status(self):
        r = self.client.post(f"/s/{self.a.token}/hwp/upload", {"file": up()})
        self.assertNotContains(r, 'id="hwpPick"')
        self.assertContains(r, "읽기 끝났어요")

    def test_busy_file_polls_whole_pick_part(self):
        _, sf = self.done()
        SourceFile.objects.filter(pk=sf.pk).update(status=S.CONVERTING)
        r = self.client.get(self.url(sf))
        self.assertContains(r, f'hx-get="/s/{self.a.token}/hwp/{sf.key}/pick" hx-trigger="every 2s"')
        self.assertNotContains(r, "hwpPreview")
        self.assertEqual(r.content.decode().count("hx-trigger"), 1)  # 안쪽 상태 줄은 따로 폴링하지 않는다

    def test_no_table_file_says_so(self):
        from hwpx.document import HwpxDocument

        d = HwpxDocument.new()
        d.add_paragraph("표 없는 공문")
        r, sf = self.done("공문.hwpx", d.to_bytes())
        self.assertContains(r, "이 파일에서 표를 찾지 못했어요")
        self.assertNotContains(r, "hwpPreview")

    # ---- 확정
    def test_pick_saves_location_and_schema_follows(self):
        _, sf = self.done()
        r = self.pick(sf, sec=0, index=1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"sec": 0, "index": 1, "no": 2, "rank": 0})
        sf.refresh_from_db()
        self.assertEqual((sf.table_sec, sf.table_index), (0, 1))
        s = sf.picked_schema()
        with sf.hwpx_file().open("rb") as fh:
            hx = fh.read()
        self.assertEqual(s, hwp.schema(hx, "Contents/section0.xml", 1))
        self.assertEqual((s["rows"], s["cols"], s["total_rows"]), (5, 3, [4]))
        # 다시 열면 고른 표가 보인다
        r = self.client.get(self.url(sf))
        self.assertContains(r, 'data-picked="0/1"')
        self.assertContains(r, "이 표로 받아요")

    def test_non_candidate_table_can_be_picked(self):
        _, sf = self.done()
        r = self.pick(sf, sec=0, index=0)  # 결재란(후보 아님)도 사람이 고르면 그대로
        self.assertEqual(r.json()["rank"], None)
        sf.refresh_from_db()
        self.assertEqual((sf.table_sec, sf.table_index), (0, 0))

    def test_pick_rejects_table_not_in_report(self):
        _, sf = self.done()
        for data in ({"sec": 0, "index": 2}, {"sec": 1, "index": 0}, {"sec": -1, "index": 1}, {"sec": "x", "index": 1},
                     {"index": 1}, {"sec": 0}, {}, {"sec": "0/1", "index": ""}):
            r = self.pick(sf, **data)
            self.assertEqual(r.status_code, 400, data)
            self.assertIn("error", r.json())
        sf.refresh_from_db()
        self.assertIsNone(sf.table_index)
        self.assertIsNone(sf.picked_schema())

    def test_pick_needs_done(self):
        _, sf = self.done()
        for st in (S.PENDING, S.CONVERTING, S.FAILED):
            SourceFile.objects.filter(pk=sf.pk).update(status=st)
            self.assertEqual(self.pick(sf, sec=0, index=1).status_code, 409, st)
        sf.refresh_from_db()
        self.assertIsNone(sf.table_index)

    def test_pick_isolation(self):
        _, sf = self.done()
        other = Client()
        other.force_login(self.ub)
        self.assertEqual(self.pick(sf, client=other, space=self.b, sec=0, index=1).status_code, 404)  # 내 공간에 남의 키
        self.assertEqual(self.pick(sf, client=other, sec=0, index=1).status_code, 404)  # 남의 공간 주소
        self.assertEqual(other.get(self.url(sf)).status_code, 404)
        self.assertEqual(self.pick(sf, space=self.b, sec=0, index=1).status_code, 404)  # 회원 아닌 공간
        self.assertEqual(self.pick(sf, client=Client(), sec=0, index=1).status_code, 302)  # 로그인 안 함
        sf.refresh_from_db()
        self.assertIsNone(sf.table_index)

    def test_pick_needs_csrf(self):
        _, sf = self.done()
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        self.assertEqual(self.pick(sf, client=c, sec=0, index=1).status_code, 403)
        self.assertEqual(self.client.put(self.url(sf)).status_code, 405)
        sf.refresh_from_db()
        self.assertIsNone(sf.table_index)

    # ---- 만들기 화면
    def new(self, **data):
        return self.client.post(f"/s/{self.a.token}/c/new", {"src": "hwp", "title": "연수 신청", **data})

    def test_new_page_has_hwp_tab_and_csp(self):
        r = self.client.get(f"/s/{self.a.token}/c/new")
        self.assertContains(r, 'data-src="hwp" aria-pressed="false">한글 양식 올리기</button>')
        self.assertContains(r, f'data-upload="/s/{self.a.token}/hwp/upload?pick=1"')
        self.assertIn("'wasm-unsafe-eval'", r["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", r["Content-Security-Policy"])

    def test_new_hwp_needs_file_and_pick(self):
        r = self.new(hwp="")
        self.assertContains(r, "한글 파일을 올려 주세요", status_code=400)
        _, sf = self.done()
        r = self.new(hwp=sf.key)
        self.assertContains(r, "취합할 표를 미리보기에서 한 번 눌러 골라 주세요", status_code=400)
        self.assertContains(r, 'id="hwpPick"', status_code=400)  # 올린 파일은 그대로 다시 보인다
        self.pick(sf, sec=0, index=1)
        r = self.new(hwp=sf.key)
        self.assertContains(r, "받을 칸 확인", status_code=400)  # E6 칸 확인이 이어서 보인다(칸 값 없이 보내면 다시 확인)
        self.assertContains(r, 'data-picked="0/1"', status_code=400)

    def test_new_hwp_other_space_key_ignored(self):
        _, sf = self.done()
        other = Client()
        other.force_login(self.ub)
        r = other.post(f"/s/{self.b.token}/c/new", {"src": "hwp", "title": "x", "hwp": sf.key})
        self.assertContains(r, "한글 파일을 올려 주세요", status_code=400)
        self.assertNotContains(r, sf.key, status_code=400)


@override_settings(ROOT_URLCONF="config.urls", REDIS_URL="")
class PickQaTests(TestCase):
    """E5 독립 검증(QA)에서 더한 경계 사례. PickTests 의 준비·도우미만 빌려 쓴다(그 테스트는 다시 돌리지 않음)."""
    setUpTestData = PickTests.__dict__["setUpTestData"]
    setUp, done, url, pick, new = PickTests.setUp, PickTests.done, PickTests.url, PickTests.pick, PickTests.new

    def test_other_school_member_gets_404(self):
        _, sf = self.done()
        far = School.objects.create(neis_atpt="B10", neis_code="7777777", name="먼학교초")
        c = Space.objects.create(school=far, grade=4, name="4학년")
        u = get_user_model().objects.create(username="google:pc")
        Member.objects.create(space=c, name="최라온", cls="4-1", user=u)
        other = Client()
        other.force_login(u)
        self.assertEqual(other.get(self.url(sf, c)).status_code, 404)
        self.assertEqual(self.pick(sf, client=other, space=c, sec=0, index=1).status_code, 404)
        self.assertEqual(self.pick(sf, client=other, sec=0, index=1).status_code, 404)
        r = other.post(f"/s/{c.token}/c/new", {"src": "hwp", "title": "x", "hwp": sf.key})
        self.assertNotContains(r, sf.key, status_code=400)
        sf.refresh_from_db()
        self.assertIsNone(sf.table_index)

    def test_huge_numbers_are_400_not_500(self):
        _, sf = self.done()
        for data in ({"sec": 10**20, "index": 1}, {"sec": 0, "index": 10**20}, {"sec": " 0", "index": "1.0"}):
            self.assertEqual(self.pick(sf, **data).status_code, 400, data)

    def test_repick_replaces(self):
        _, sf = self.done()
        self.assertEqual(self.pick(sf, sec=0, index=1).status_code, 200)
        self.assertEqual(self.pick(sf, sec=0, index=0).status_code, 200)
        sf.refresh_from_db()
        self.assertEqual((sf.table_sec, sf.table_index), (0, 0))

    def test_new_hwp_failed_file_asks_upload(self):
        _, sf = self.done()
        SourceFile.objects.filter(pk=sf.pk).update(status=S.FAILED)
        r = self.new(hwp=sf.key)
        self.assertContains(r, "한글 파일을 올려 주세요", status_code=400)

    def test_rerender_preview_is_not_a_source_tab(self):
        """재표시(400) 화면에서 미리보기 표를 눌러도 '가져오는 방식'이 바뀌면 안 된다.
        collect_new.js 는 [data-src] 전부를 탭 단추로 보고 클릭을 건다. 미리보기 상자(#hwpPreview)가 data-src(hwpx 주소)를
        가지면, 표를 누를 때 src 가 그 주소로 바뀌고 한글 양식 칸이 통째로 숨는다(브라우저 재현: E5 QA)."""
        import re

        from django.conf import settings
        _, sf = self.done()
        self.pick(sf, sec=0, index=1)
        r = self.new(hwp=sf.key)
        stray = set(re.findall(r'data-src="([^"]*)"', r.content.decode())) - {"hwp", "paste", "sheet", "preset"}
        js = (settings.STATICFILES_DIRS[0] / "js" / "collect_new.js").read_text()
        bare = "querySelectorAll('[data-src]')" in js
        self.assertFalse(stray and bare, f"탭 아닌 data-src {len(stray)}개가 탭 클릭을 받는다")
