"""모으기 지우기(개인정보처리방침 3항 "요청자가 지우거나 … 파기"). 합본 화면 아래 '이 모으기 지우기'.

불변식
- 만든 사람만(마감 바꾸기·잠그기와 같은 규칙). 다른 회원 403, 다른 공간 404, GET 405, CSRF 필요.
- 지우면 칸·줄·제출 값·알림·올린 한글 파일·결과 파일이 모두 사라진다. 받는 링크는 404, 목록에서도 사라진다.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from hwpfiles.models import ResultFile, SourceFile
from spaces.models import Member, School, Space

from .models import Cell, Collection, Field, Notification, Row


@override_settings(SITE_URL="https://t.goteacher.xyz")
class DeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2", user=cls.u2)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        cls.other = Collection.objects.create(space=cls.b, title="옆 학년", source=Collection.Source.SHEET,
                                              unit_head=["학년반", "담임"])

    def setUp(self):
        self.client.force_login(self.ua)
        c = self.c = Collection.objects.create(space=self.a, created_by=self.owner, title="학예회 참가 인원",
                                               source=Collection.Source.HWP, unit_head=["학년반", "담임"])
        f = Field.objects.create(collection=c, pos=0, name="참가(명)", kind="number")
        r = Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=self.owner)
        Cell.objects.create(row=r, field=f, value="21")
        Notification.objects.create(space=self.a, member=self.owner, row=r, kind="d1", day=date(2026, 11, 2),
                                    text="학예회", link=f"/c/{c.link_token}/u/{r.key}")
        sf = SourceFile.objects.create(space=self.a, created_by=self.owner, name="양식.hwpx", kind="hwpx",
                                       size=10, collection=c)
        ResultFile.objects.create(space=self.a, source=sf, collection=c, fmt="hwpx")

    def url(self, c=None, space=None, tail="delete"):
        c = c or self.c
        return f"/s/{(space or c.space).token}/c/{c.key}/{tail}"

    def gone(self):
        return not Collection.objects.filter(pk=self.c.pk).exists()

    def test_owner_deletes_everything(self):
        link = f"/c/{self.c.link_token}"
        self.assertEqual(Client().get(link).status_code, 200)
        r = self.client.post(self.url())
        self.assertRedirects(r, f"/s/{self.a.token}/c/", fetch_redirect_response=False)
        self.assertTrue(self.gone())
        for model in (Field, Row, Cell, Notification, SourceFile, ResultFile):
            self.assertEqual(model.objects.count(), 0, model.__name__)  # 옆 학년 모으기에는 칸·줄이 없다
        self.assertEqual(Client().get(link).status_code, 404)
        self.assertEqual(self.client.get(self.url(tail="")).status_code, 404)
        self.assertNotContains(self.client.get(f"/s/{self.a.token}/c/"), "학예회 참가 인원")
        self.assertTrue(Collection.objects.filter(pk=self.other.pk).exists())  # 다른 공간 것은 그대로

    def test_owner_sees_button_and_dialog(self):
        body = self.client.get(self.url(tail="")).content.decode()
        self.assertIn("이 모으기 지우기", body)
        self.assertIn("받은 값·올린 한글 파일·결과 파일이 모두 지워지고 되돌릴 수 없어요. 백업에는 7일 동안 남아요.", body)
        self.assertIn(f'action="{self.url()}"', body)

    def test_other_member_forbidden(self):
        c2 = Client()
        c2.force_login(self.u2)
        self.assertEqual(c2.post(self.url()).status_code, 403)
        self.assertFalse(self.gone())
        self.assertNotIn("이 모으기 지우기", c2.get(self.url(tail="")).content.decode())

    def test_other_space_404(self):
        c2 = Client()
        c2.force_login(self.ub)
        self.assertEqual(c2.post(self.url()).status_code, 404)
        self.assertEqual(self.client.post(self.url(c=self.other, space=self.a)).status_code, 404)
        self.assertFalse(self.gone())
        self.assertTrue(Collection.objects.filter(pk=self.other.pk).exists())

    def test_get_405(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertFalse(self.gone())

    def test_csrf_required(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        self.assertEqual(c.post(self.url()).status_code, 403)
        self.assertFalse(self.gone())

    def test_anonymous_not_deleted(self):
        self.assertIn(Client().post(self.url()).status_code, (302, 403, 404))
        self.assertFalse(self.gone())
