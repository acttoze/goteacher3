"""설정 화면 완성(design/settings.html): 내 정보 · 알림 · 동학년 · 이 PC.

불변식
- 영역은 ?a=me|alerts|space|device. 기본은 내 정보(design #me). 다른 공간 주소는 모두 404.
- 내 정보: 이름·반을 바꾸면 바로 저장. 반은 그 학년 반(NEIS 또는 1~4반)+전담만. 같은 반·같은 이름 선생님이 이미 있으면 거부
  (초대 입장은 반·이름으로 같은 사람을 찾으므로 겹치면 남의 자리로 들어가게 된다).
- 알림: 묶음 시각 3개 안내 + 이 PC 브라우저 알림 켜기·끄기(VAPID 키가 있을 때만).
- 동학년: 선생님 목록, 초대 링크·QR(새로 만들기는 만든 사람만), 회원 내보내기는 동학년 화면으로.
- 나가기: 동학년 이름을 써야 나간다. 내가 만든 모으기는 남은 선생님 중 가장 먼저 들어온 분(새 학년 대표)이 넘겨받는다.
  마지막 한 명이 나가면 동학년을 지운다(같은 학년을 새로 만들 수 있게). 손님은 계정도 지우고 로그아웃.
- 이 PC 스위치는 실제 상태를 따른다(기기 쿠키 없으면 꺼짐).
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from collect.models import Collection
from spaces.models import Member, School, Space

from .models import DEVICE_COOKIE, DeviceToken, new_guest

User = get_user_model()
SITE = "https://t.goteacher.xyz"


@override_settings(SITE_URL=SITE, VAPID_PUBLIC_KEY="BPUBKEY")
class SettingsBase(TestCase):
    def setUp(self):
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2", "3"])
        p.start()
        self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        self.space = Space.objects.create(school=school, grade=4, name="새솔초 4학년")
        self.other = Space.objects.create(school=school, grade=5, name="새솔초 5학년")
        self.owner = User.objects.create(username="google:1", first_name="송민재", email="song@example.com")
        self.me = Member.objects.create(space=self.space, user=self.owner, name="송민재", cls="4-1")
        self.guest = new_guest("정하은")
        self.g = Member.objects.create(space=self.space, user=self.guest, name="정하은", cls="4-2")
        self.client.force_login(self.owner)
        self.url = f"/s/{self.space.token}/settings"


class AreaTests(SettingsBase):
    def test_default_area_is_me_with_tabs(self):
        r = self.client.get(self.url)
        self.assertContains(r, "내 정보 설정")
        for a in ("me", "alerts", "space", "device"):
            self.assertContains(r, f'href="?a={a}"')
        self.assertContains(r, 'href="?a=me" aria-current="page"')
        self.assertContains(r, 'value="송민재"')
        self.assertContains(r, 'data-v="4-1" aria-pressed="true"')
        self.assertContains(r, 'data-v="4-전담"')
        self.assertNotContains(r, "결제")  # 학교판(R2)·결제 항목 없음

    def test_unknown_area_falls_back_to_me(self):
        self.assertContains(self.client.get(self.url + "?a=zzz"), "내 정보 설정")

    def test_alerts_area_slots_and_push_box(self):
        r = self.client.get(self.url + "?a=alerts")
        for t in ("08:20", "12:20", "14:40"):
            self.assertContains(r, t)
        self.assertContains(r, 'class="pushbox"')
        self.assertContains(r, 'data-vapid="BPUBKEY"')
        self.assertContains(r, f'data-on="/s/{self.space.token}/push"')
        with override_settings(VAPID_PUBLIC_KEY=""):
            self.assertNotContains(self.client.get(self.url + "?a=alerts"), 'class="pushbox"')

    def test_alerts_slot_switches_follow_member(self):
        r = self.client.get(self.url + "?a=alerts")
        self.assertContains(r, f'hx-post="/s/{self.space.token}/settings/slots"')
        for k in ("am", "noon", "pm"):
            self.assertContains(r, f'name="on" value="{k}" checked')
        self.assertNotContains(r, '<span class="pill">켜짐</span>')  # 동작하지 않는 표시는 없다
        Member.objects.filter(pk=self.me.pk).update(push_off="noon")
        r = self.client.get(self.url + "?a=alerts")
        self.assertContains(r, 'name="on" value="am" checked')
        self.assertContains(r, 'name="on" value="noon" aria-label')
        self.assertContains(r, "꺼짐 · 이 시간 소식은 다음 묶음으로 넘어가요")

    def test_space_area_members_invite_qr(self):
        r = self.client.get(self.url + "?a=space")
        self.assertContains(r, "정하은")
        self.assertContains(r, "학년 대표")
        self.assertContains(r, f"{SITE}/j/{self.space.invite_token}")
        self.assertContains(r, 'class="qr-img"')
        self.assertContains(r, f'action="/s/{self.space.token}/invite"')  # 만든 사람: 새로 만들기
        self.assertContains(r, f'href="/s/{self.space.token}/"')  # 내보내기는 동학년 화면에서
        self.assertContains(r, "새솔초 4학년에서 나가기")

    def test_space_area_non_creator_cannot_regen(self):
        c = Client()
        c.force_login(self.guest)
        r = c.get(self.url + "?a=space")
        self.assertContains(r, f"{SITE}/j/{self.space.invite_token}")
        self.assertNotContains(r, f'action="/s/{self.space.token}/invite"')

    def test_device_area_switch_follows_real_state(self):
        r = self.client.get(self.url + "?a=device")  # 기기 쿠키 없음
        self.assertContains(r, 'name="remember" value="1" aria-label')
        self.assertNotContains(r, 'name="remember" value="1" checked')
        self.assertContains(r, "song@example.com")
        self.assertContains(r, 'form="logoutForm"')  # 이 PC에서 로그아웃(공통 틀의 폼으로 보낸다)
        self.assertContains(r, "모든 기기에서 로그아웃")
        c = Client()
        c.force_login(self.owner)
        c.cookies[DEVICE_COOKIE] = DeviceToken.issue(self.owner)
        self.assertContains(c.get(self.url + "?a=device"), 'name="remember" value="1" checked')

    def test_other_space_404(self):
        for path in ("settings", "settings?a=space", "settings/me", "settings/leave"):
            u = f"/s/{self.other.token}/{path}"
            r = self.client.post(u, {"name": "x", "cls": "5-1", "confirm": "새솔초 5학년"}) if "/" in path \
                else self.client.get(u)
            self.assertEqual(r.status_code, 404, path)
        self.assertTrue(Space.objects.filter(pk=self.other.pk).exists())


class SlotTests(SettingsBase):
    """설정 > 알림 묶음 스위치: POST(CSRF)·즉시 저장·내 회원만·다른 공간 404."""

    def post(self, on, client=None, space=None, hx=True):
        headers = {"HX-Request": "true"} if hx else {}
        return (client or self.client).post(f"/s/{(space or self.space).token}/settings/slots", {"on": on},
                                            headers=headers)

    def test_turn_off_and_on(self):
        r = self.post(["am", "pm"])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="slotPanel"')
        self.assertContains(r, 'name="on" value="noon" aria-label')
        self.assertContains(r, "하루 2번")
        self.me.refresh_from_db()
        self.assertEqual(self.me.push_off, "noon")
        self.g.refresh_from_db()
        self.assertEqual(self.g.push_off, "")  # 다른 선생님은 그대로
        self.post(["am", "noon", "pm"])
        self.me.refresh_from_db()
        self.assertEqual(self.me.push_off, "")

    def test_all_off_and_unknown_keys_ignored(self):
        r = self.post(["zz", "<b>"])
        self.assertContains(r, "모두 껐어요")
        self.assertNotContains(r, "&lt;b&gt;")
        self.me.refresh_from_db()
        self.assertEqual(self.me.push_off, "am,noon,pm")

    def test_without_htmx_redirects(self):
        r = self.post(["am"], hx=False)
        self.assertRedirects(r, self.url + "?a=alerts", fetch_redirect_response=False)

    def test_get_not_allowed_and_csrf_required(self):
        self.assertEqual(self.client.get(f"/s/{self.space.token}/settings/slots").status_code, 405)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.owner)
        self.assertEqual(self.post([], client=c).status_code, 403)
        self.me.refresh_from_db()
        self.assertEqual(self.me.push_off, "")

    def test_other_space_404(self):
        self.assertEqual(self.post([], space=self.other).status_code, 404)
        self.assertEqual(self.client.post("/s/nope/settings/slots", {}).status_code, 404)
        self.me.refresh_from_db()
        self.assertEqual(self.me.push_off, "")


class MeTests(SettingsBase):
    def post(self, data, client=None, htmx=True):
        h = {"HX-Request": "true"} if htmx else {}
        return (client or self.client).post(f"{self.url}/me", data, headers=h)

    def test_change_name_and_class(self):
        r = self.post({"name": "  송민재2 ", "cls": "4-3"})
        self.assertContains(r, "저장했어요")
        self.assertNotContains(r, "<html")
        self.me.refresh_from_db()
        self.assertEqual((self.me.name, self.me.cls), ("송민재2", "4-3"))
        r = self.post({"name": "송민재", "cls": "4-전담"}, htmx=False)
        self.assertEqual(r.status_code, 302)
        self.me.refresh_from_db()
        self.assertEqual(self.me.cls, "4-전담")

    def test_rejects_bad_values(self):
        self.assertEqual(self.post({"name": "", "cls": "4-1"}).status_code, 400)
        self.assertEqual(self.post({"name": "​", "cls": "4-1"}).status_code, 400)
        self.assertEqual(self.post({"name": "송민재", "cls": "5-1"}).status_code, 400)  # 다른 학년
        self.assertEqual(self.post({"name": "송민재", "cls": "4-9"}).status_code, 400)  # 없는 반
        self.me.refresh_from_db()
        self.assertEqual((self.me.name, self.me.cls), ("송민재", "4-1"))

    def test_rejects_same_class_and_name_as_other(self):
        r = self.post({"name": "정하은", "cls": "4-2"})
        self.assertContains(r, "같은 반에 같은 이름", status_code=400)
        self.me.refresh_from_db()
        self.assertEqual(self.me.name, "송민재")
        self.assertEqual(self.post({"name": "정하은", "cls": "4-3"}).status_code, 200)  # 반이 다르면 된다

    def test_neis_missing_uses_four_classes(self):
        with mock.patch("spaces.neis.class_names", return_value=[]):
            self.assertEqual(self.post({"name": "송민재", "cls": "4-4"}).status_code, 200)
            self.assertEqual(self.post({"name": "송민재", "cls": "4-5"}).status_code, 400)


class LeaveTests(SettingsBase):
    def leave(self, client=None, confirm="새솔초 4학년"):
        return (client or self.client).post(f"{self.url}/leave", {"confirm": confirm})

    def test_needs_space_name(self):
        r = self.leave(confirm="새솔초")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Member.objects.filter(pk=self.me.pk).exists())

    def test_guest_leaves_account_deleted_logged_out(self):
        c = Client()
        c.force_login(self.guest)
        c.cookies[DEVICE_COOKIE] = DeviceToken.issue(self.guest)
        mine = Collection.objects.create(space=self.space, created_by=self.g, title="손님 모으기", source="sheet")
        r = self.leave(c)
        self.assertEqual((r.status_code, r["Location"]), (302, "/"))
        self.assertEqual(r.cookies[DEVICE_COOKIE].value, "")
        self.assertFalse(User.objects.filter(pk=self.guest.pk).exists())
        self.assertFalse(DeviceToken.objects.filter(user_id=self.guest.pk).exists())
        mine.refresh_from_db()
        self.assertEqual(mine.created_by, self.me)  # 학년 대표가 넘겨받는다
        self.assertEqual(c.get(self.url).status_code, 302)  # 로그아웃

    def test_creator_leaves_next_member_takes_over(self):
        mine = Collection.objects.create(space=self.space, created_by=self.me, title="대표 모으기", source="sheet")
        r = self.leave()
        self.assertEqual((r.status_code, r["Location"]), (302, "/"))
        self.assertTrue(Space.objects.filter(pk=self.space.pk).exists())
        self.assertFalse(Member.objects.filter(pk=self.me.pk).exists())
        mine.refresh_from_db()
        self.assertEqual(mine.created_by, self.g)
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())  # 구글 계정은 그대로
        self.assertEqual(self.client.get(self.url).status_code, 404)
        c = Client()
        c.force_login(self.guest)
        self.assertContains(c.get(self.url + "?a=space"), f'action="/s/{self.space.token}/invite"')  # 새 대표

    def test_last_member_leaving_deletes_space(self):
        self.g.delete()
        Collection.objects.create(space=self.space, created_by=self.me, title="혼자", source="sheet")
        r = self.client.get(self.url + "?a=space")
        self.assertContains(r, "마지막 선생님")
        self.leave()
        self.assertFalse(Space.objects.filter(pk=self.space.pk).exists())
        self.assertFalse(Collection.objects.filter(title="혼자").exists())
        self.assertTrue(Space.objects.filter(pk=self.other.pk).exists())


class QaSettingsEdgeTests(SettingsBase):
    """QA 추가: 반·이름을 바꾼 뒤 모으기 줄·명부·초대 재진입, 나가기 경계."""

    def test_class_change_keeps_existing_row_and_moves_next_roster(self):
        from collect.lists import _mine_q
        from collect.models import Row
        from collect.recipients import roster_rows

        c = Collection.objects.create(space=self.space, created_by=self.me, title="명단", source="sheet")
        row = Row.objects.create(collection=c, pos=0, unit="4-2", who="정하은", member=self.g)
        g = Client()
        g.force_login(self.guest)
        r = g.post(f"{self.url}/me", {"name": "정하은", "cls": "4-3"}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.g.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual((row.unit, row.member_id), ("4-2", self.g.pk))  # 이미 만든 줄은 그대로, 연결도 그대로
        self.assertTrue(Row.objects.filter(_mine_q(self.g)).filter(pk=row.pk).exists())  # 내가 낼 것에 남는다
        self.assertEqual(g.get(f"/c/{c.link_token}/u/{row.key}").status_code, 200)  # 받는 사람 화면
        units = [(x["unit"], x["who"]) for x in roster_rows(self.space, "class")]
        self.assertIn(("4-3", "정하은"), units)  # 다음 모으기 명부는 새 반
        self.assertNotIn(("4-2", "정하은"), units)

    def test_guest_reenters_with_new_class_as_same_person(self):
        from django.core.cache import caches
        caches["ratelimit"].clear()
        g = Client()
        g.force_login(self.guest)
        g.post(f"{self.url}/me", {"cls": "4-3"}, headers={"HX-Request": "true"})
        other_pc = Client()
        other_pc.get(f"/j/{self.space.invite_token}")
        r = other_pc.post("/join", {"cls": "4-3", "name": "정하은"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(other_pc.session["_auth_user_id"]), self.guest.pk)
        self.assertEqual(Member.objects.in_space(self.space).count(), 2)

    def test_after_last_leaves_same_grade_can_be_made_again(self):
        from spaces.views import _grade_taken
        school = {"atpt": "J10", "code": "7531234"}
        self.assertTrue(_grade_taken(school, 4))
        self.g.delete()
        self.client.post(f"{self.url}/leave", {"confirm": "새솔초 4학년"})
        self.assertFalse(_grade_taken(school, 4))
        self.assertTrue(_grade_taken(school, 5))

    def test_leave_and_me_need_post_and_csrf(self):
        self.assertEqual(self.client.get(f"{self.url}/leave").status_code, 405)
        self.assertEqual(self.client.get(f"{self.url}/me").status_code, 405)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.owner)
        self.assertEqual(c.post(f"{self.url}/leave", {"confirm": "새솔초 4학년"}).status_code, 403)
        self.assertEqual(c.post(f"{self.url}/me", {"name": "x"}).status_code, 403)
        self.assertTrue(Member.objects.filter(pk=self.me.pk).exists())
