"""D6 모으기 목록(내가 걷는 것·내가 낼 것·지난 것)과 다시 걷기(복제). 모습은 design/collect.html.

불변식
- 목록은 request.space 안의 모으기만. 다른 공간 주소는 404. 캐시 금지(공용 PC).
- 내가 걷는 것 = 내가 만든 것 중 잠기지 않고 마감 전. 지난 것 = 내가 만든 것 중 잠금·마감 지남.
- 내가 낼 것 = 남이 만든, 잠기지 않은 모으기에서 내 줄(Row.member가 나, 또는 명부 밖 이름이 나)이 아직 안 낸 것.
- 다시 걷기: 만든 사람만(다른 회원 403, 다른 공간 404, GET 405, CSRF). 칸·설정을 옮기고 제출 값·마감·잠금은 비운다.
  줄은 지금 동학년 명부로 다시 만든다(명부 밖 줄은 옮기지 않음). 새 key·link_token. Cell을 미리 만들지 않는다.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from spaces.models import Member, School, Space

from .models import Cell, Collection, Field, Row
from .presets import PRESETS
from .submit import save_cell

HR = ["학년반", "담임"]


@override_settings(SITE_URL="https://t.goteacher.xyz")
class ListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.me = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2", user=cls.u2)
        cls.mb = Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        # 내가 걷는 것
        cls.mine = cls.make(cls.a, cls.me, "학예회 참가 인원")
        # 남이 보낸 것(내 줄 있음)
        cls.theirs = cls.make(cls.a, cls.m2, "방과후 수요조사")
        # 옆 학년
        cls.other = cls.make(cls.b, cls.mb, "옆 학년 비밀 조사")

    @classmethod
    def make(cls, space, by, title, **kw):
        c = Collection.objects.create(space=space, created_by=by, title=title, source=Collection.Source.SHEET,
                                      unit_head=HR, **kw)
        Field.objects.create(collection=c, pos=0, name="참가(명)", kind="number")
        for i, m in enumerate(Member.objects.in_space(space).order_by("pk")):
            Row.objects.create(collection=c, pos=i, unit=m.cls, who=m.name, member=m)
        return c

    def setUp(self):
        self.client.force_login(self.ua)

    def url(self, space=None, tail=""):
        return f"/s/{(space or self.a).token}/c/{tail}"

    def page(self, tab="", **params):
        if tab:
            params["tab"] = tab
        r = self.client.get(self.url(), params)
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        return html[html.index('id="list"'):html.index("</table>")]  # 목록 표만(제목 줄·곁 카드 빼고)

    # ---------- 목록 ----------
    def test_sent_tab_lists_only_mine_in_space(self):
        html = self.page()
        self.assertIn("학예회 참가 인원", html)
        self.assertNotIn("방과후 수요조사", html)  # 남이 보낸 것은 '내가 낼 것'에
        self.assertNotIn("옆 학년 비밀 조사", html)
        self.assertIn(f"/s/{self.a.token}/c/{self.mine.key}/", html)
        self.assertIn("0/2", html)

    def test_other_space_is_404(self):
        self.assertEqual(self.client.get(self.url(self.b)).status_code, 404)

    def test_anonymous_goes_to_login(self):
        r = Client().get(self.url())
        self.assertEqual(r.status_code, 302)

    def test_no_cache(self):
        r = self.client.get(self.url())
        self.assertIn("no-store", r["Cache-Control"])

    def test_todo_my_row_not_done(self):
        html = self.page("todo")
        self.assertIn("방과후 수요조사", html)
        self.assertNotIn("학예회 참가 인원", html)  # 내가 만든 것은 낼 것에 넣지 않음(prototype !c.mine)
        row = self.theirs.rows.get(member=self.me)
        self.assertIn(f"/c/{self.theirs.link_token}/u/{row.key}", html)
        self.assertNotIn("옆 학년 비밀 조사", html)

    def test_todo_gone_after_submit(self):
        row = self.theirs.rows.get(member=self.me)
        save_cell(row, self.theirs.fields.get(), "3", "김가람")
        self.assertNotIn("방과후 수요조사", self.page("todo"))

    def test_todo_others_row_only_not_listed(self):
        c = self.make(self.a, self.m2, "다른 반만")
        c.rows.filter(member=self.me).delete()
        self.assertNotIn("다른 반만", self.page("todo"))

    def test_todo_outside_row_with_my_name(self):
        c = self.make(self.a, self.m2, "명부 밖 줄", allow_outside=True)
        c.rows.filter(member=self.me).delete()
        Row.objects.create(collection=c, pos=9, unit="명부 밖", who="김가람")
        self.assertIn("명부 밖 줄", self.page("todo"))

    def test_todo_locked_not_listed(self):
        Collection.objects.filter(pk=self.theirs.pk).update(closed_at=timezone.now())
        self.assertNotIn("방과후 수요조사", self.page("todo"))

    def test_past_locked_and_overdue(self):
        Collection.objects.filter(pk=self.mine.pk).update(closed_at=timezone.now())
        late = self.make(self.a, self.me, "마감 지난 조사", due=timezone.now() - timedelta(hours=1))
        sent, past = self.page(), self.page("past")
        self.assertNotIn("학예회 참가 인원", sent)
        self.assertNotIn("마감 지난 조사", sent)
        self.assertIn("학예회 참가 인원", past)
        self.assertIn("마감 지난 조사", past)
        self.assertIn(f"/c/{late.key}/", past)

    def test_search(self):
        self.make(self.a, self.me, "현장체험학습 버스")
        html = self.page(q="버스")
        self.assertIn("현장체험학습 버스", html)
        self.assertNotIn("학예회 참가 인원", html)

    def test_shell_menu_points_to_list(self):
        r = self.client.get(f"/s/{self.a.token}/")
        self.assertContains(r, f'href="/s/{self.a.token}/c/"')

    # ---------- 다시 걷기 ----------
    def again(self, c=None, client=None, space=None):
        c = c or self.mine
        return (client or self.client).post(self.url(space, f"{c.key}/again"))

    def test_again_copies_fields_and_settings_only(self):
        f = self.mine.fields.get()
        Field.objects.create(collection=self.mine, pos=1, name="참가 여부", kind="select", options=["예", "아니오"])
        Collection.objects.filter(pk=self.mine.pk).update(
            due=timezone.now(), closed_at=timezone.now(), allow_outside=True, footer_on=False, multi=True)
        save_cell(self.mine.rows.get(member=self.me), f, "24", "김가람")
        Row.objects.create(collection=self.mine, pos=9, unit="명부 밖", who="강사")
        new_m = Member.objects.create(space=self.a, name="정하은", cls="4-3")  # 원본 뒤에 들어온 선생님

        r = self.again()
        new = Collection.objects.in_space(self.a).exclude(pk__in=[self.mine.pk, self.theirs.pk]).get()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r["Location"], f"/s/{self.a.token}/c/{new.key}/")
        self.assertEqual(new.title, "학예회 참가 인원")
        self.assertNotEqual(new.key, self.mine.key)
        self.assertNotEqual(new.link_token, self.mine.link_token)
        self.assertGreaterEqual(len(new.link_token), 32)
        self.assertIsNone(new.due)
        self.assertIsNone(new.closed_at)
        self.assertEqual((new.source, new.unit_head, new.multi, new.allow_outside, new.footer_on),
                         (Collection.Source.SHEET, HR, True, True, False))
        self.assertEqual(new.created_by, self.me)
        self.assertEqual([(x.name, x.kind, x.options) for x in new.fields.all()],
                         [("참가(명)", "number", []), ("참가 여부", "select", ["예", "아니오"])])
        self.assertEqual([(x.unit, x.who, x.member) for x in new.rows.all()],
                         [("4-1", "김가람", self.me), ("4-2", "박서준", self.m2), ("4-3", "정하은", new_m)])
        self.assertFalse(Cell.objects.filter(row__collection=new).exists())
        self.assertTrue(Cell.objects.filter(row__collection=self.mine).exists())  # 원본은 그대로

    def test_again_training_uses_people_roster(self):
        spec = PRESETS["training"]
        c = Collection.objects.create(space=self.a, created_by=self.me, title=spec["title"],
                                      source=Collection.Source.PRESET, unit_head=spec["unit_head"])
        Field.objects.create(collection=c, pos=0, name="청렴교육", kind="text")
        Member.objects.create(space=self.a, name="전담쌤", cls="4-전담")
        self.assertEqual(self.again(c).status_code, 303)
        new = Collection.objects.in_space(self.a).filter(title=spec["title"]).exclude(pk=c.pk).get()
        self.assertEqual([x.who for x in new.rows.all()], ["김가람", "박서준", "전담쌤"])

    def test_again_owner_only(self):
        other = Client()
        other.force_login(self.u2)
        self.assertEqual(self.again(client=other).status_code, 403)
        self.assertEqual(Collection.objects.in_space(self.a).count(), 2)

    def test_again_other_space_404(self):
        self.assertEqual(self.again(self.other).status_code, 404)  # 내 공간 주소 + 옆 공간 key
        self.assertEqual(self.again(self.other, space=self.b).status_code, 404)
        self.assertEqual(Collection.objects.in_space(self.b).count(), 1)

    def test_again_get_405_and_csrf(self):
        self.assertEqual(self.client.get(self.url(tail=f"{self.mine.key}/again")).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.ua)
        self.assertEqual(self.again(client=strict).status_code, 403)

    def test_again_no_roster(self):
        Member.objects.filter(pk__in=[self.m2.pk]).delete()
        Member.objects.filter(pk=self.me.pk).update(cls="4-전담")
        r = self.again()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Collection.objects.in_space(self.a).filter(created_by=self.me).count(), 1)

    def test_again_button_on_sent_and_past(self):
        self.assertIn(f"/c/{self.mine.key}/again", self.page())
        Collection.objects.filter(pk=self.mine.pk).update(closed_at=timezone.now())
        self.assertIn(f"/c/{self.mine.key}/again", self.page("past"))

    # ---------- QA 추가: 경계 사례(마감 정각·다시 열기·동명이인·손님·연수 이수 부분 저장·htmx·메뉴) ----------

    def test_due_exact_boundary(self):
        from unittest import mock
        t = timezone.now().replace(microsecond=0) + timedelta(days=1)
        self.make(self.a, self.me, "정각 마감", due=t)
        self.make(self.a, self.me, "1초 뒤 마감", due=t + timedelta(seconds=1))
        with mock.patch("collect.lists.timezone.now", return_value=t):
            sent, past = self.page(), self.page("past")
        self.assertIn("1초 뒤 마감", sent)
        self.assertNotIn("정각 마감", sent)
        self.assertIn("정각 마감", past)

    def test_reopen_returns_to_sent(self):
        Collection.objects.filter(pk=self.mine.pk).update(closed_at=timezone.now())
        self.assertIn("학예회 참가 인원", self.page("past"))
        r = self.client.post(self.url(tail=f"{self.mine.key}/lock"), {"open": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("학예회 참가 인원", self.page())
        self.assertNotIn("학예회 참가 인원", self.page("past"))

    def test_reopen_theirs_returns_to_todo(self):
        Collection.objects.filter(pk=self.theirs.pk).update(closed_at=timezone.now())
        Collection.objects.filter(pk=self.theirs.pk).update(closed_at=None)
        self.assertIn("방과후 수요조사", self.page("todo"))

    def test_namesake_roster_row_is_not_mine(self):
        twin = Member.objects.create(space=self.a, name="김가람", cls="4-3")
        c = self.make(self.a, self.m2, "동명이인 조사")
        c.rows.filter(member=self.me).delete()
        self.assertTrue(c.rows.filter(member=twin).exists())
        self.assertNotIn("동명이인 조사", self.page("todo"))

    def test_outside_row_my_name_done_not_listed(self):
        c = self.make(self.a, self.m2, "명부 밖 냄", allow_outside=True)
        c.rows.filter(member=self.me).delete()
        row = Row.objects.create(collection=c, pos=9, unit="명부 밖", who="김가람")
        save_cell(row, c.fields.get(), "1", "김가람")
        self.assertNotIn("명부 밖 냄", self.page("todo"))

    def test_guest_member_sees_todo(self):
        User = get_user_model()
        gu = User.objects.create(username="guest:qa-guest-token-0000000000")
        g = Member.objects.create(space=self.a, name="손님쌤", cls="4-4", user=gu)
        c = self.make(self.a, self.m2, "손님도 낼 것")
        cl = Client()
        cl.force_login(gu)
        r = cl.get(self.url(), {"tab": "todo"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "손님도 낼 것")
        self.assertContains(r, f"/c/{c.link_token}/u/{c.rows.get(member=g).key}")
        self.assertEqual(cl.get(self.url(self.b)).status_code, 404)

    def test_training_partial_save_stays_in_todo(self):
        spec = PRESETS["training"]
        c = Collection.objects.create(space=self.a, created_by=self.m2, title="연수 이수 QA",
                                      source=Collection.Source.PRESET, unit_head=spec["unit_head"])
        f1 = Field.objects.create(collection=c, pos=0, name="청렴교육", kind="text")
        f2 = Field.objects.create(collection=c, pos=1, name="성희롱 예방", kind="text")
        row = Row.objects.create(collection=c, pos=0, unit="4학년 1반", who="김가람", member=self.me)
        save_cell(row, f1, "이수", "김가람")
        self.assertIn("연수 이수 QA", self.page("todo"))
        save_cell(row, f2, "이수", "김가람")
        self.assertNotIn("연수 이수 QA", self.page("todo"))

    def test_again_from_past_overdue_keeps_original(self):
        late = self.make(self.a, self.me, "지난 조사", due=timezone.now() - timedelta(days=3))
        save_cell(late.rows.get(member=self.me), late.fields.get(), "5", "김가람")
        before = (late.due, late.link_token, late.rows.count(), Cell.objects.filter(row__collection=late).count())
        self.assertEqual(self.again(late).status_code, 303)
        late.refresh_from_db()
        self.assertEqual(before, (late.due, late.link_token, late.rows.count(),
                                  Cell.objects.filter(row__collection=late).count()))
        new = Collection.objects.in_space(self.a).filter(title="지난 조사").exclude(pk=late.pk).get()
        self.assertIn(f"/c/{new.key}/", self.page())  # 새것은 '내가 걷는 것'에
        self.assertEqual(self.client.post(self.url(tail="nope-key/again")).status_code, 404)

    def test_again_other_space_by_link_token_404(self):
        self.assertEqual(self.client.post(self.url(tail=f"{self.mine.link_token}/again")).status_code, 404)

    def test_htmx_search_has_swap_targets_and_escapes(self):
        r = self.client.get(self.url(), {"tab": "past", "q": "<b>x"}, HTTP_HX_REQUEST="true")
        html = r.content.decode()
        for t in ('id="list"', 'id="tabs"', 'id="count"'):
            self.assertIn(t, html)
        self.assertNotIn("<b>x", html)
        self.assertIn("&lt;b&gt;x", html)
        self.assertIn("no-store", r["Cache-Control"])

    def test_search_counts_in_tabs(self):
        html = self.client.get(self.url(), {"q": "방과후"}).content.decode()
        self.assertIn('내가 걷는 것 <span class="c num">0</span>', html)
        self.assertIn('내가 낼 것 <span class="c num">1</span>', html)

    def test_menu_on_every_space_page(self):
        link = f'href="/s/{self.a.token}/c/"'
        for u in (f"/s/{self.a.token}/", self.url(tail="new"), self.url(tail=f"{self.mine.key}/"), self.url()):
            r = self.client.get(u)
            self.assertEqual(r.status_code, 200, u)
            self.assertContains(r, link, msg_prefix=u)
        for u in (self.url(tail="new"), self.url(tail=f"{self.mine.key}/"), self.url()):
            html = self.client.get(u).content.decode()
            i = html.index(link)
            self.assertIn('aria-current="page"', html[i:i + 80], u)
        self.assertNotIn("티로드", self.client.get(self.url()).content.decode())
