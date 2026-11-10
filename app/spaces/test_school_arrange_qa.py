"""Q2 QA 보강: 구현 테스트(test_school_arrange)에 없는 경계 사례만.
손님·비로그인·동학년 공간 거절, 요청 본문 조작(중복·누락·없는 키·다른 학년 반·다른 공간 줄), 독촉 묶음 순서, 일반 GET 회귀."""
import json

from django.test import Client

from accounts.models import new_guest
from collect.models import Notification
from collect.views import _miss_groups

from .models import Duty, Member, MemberLog, Space
from .test_school_arrange import ArrangeBase


class RefuseTests(ArrangeBase):
    def snapshot(self):
        return (list(Member.objects.in_space(self.hub).order_by("pk").values_list("group", "cls", "order")),
                Space.objects.get(pk=self.hub.pk).group_order, MemberLog.objects.count())

    def test_guest_member_403(self):
        guest_user = new_guest("손님")
        Member.objects.create(space=self.hub, user=guest_user, name="손님", cls="4-3", group="g4")
        c = Client()
        c.force_login(guest_user)
        before = self.snapshot()
        r = c.post(self.url, {"what": "members", "group": "office", "keys": json.dumps([self.m5.key])})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_never_saves(self):
        before = self.snapshot()
        r = Client().post(self.url, {"what": "groups", "keys": '["meal"]'})
        self.assertIn(r.status_code, (302, 403, 404))
        self.assertEqual(self.snapshot(), before)

    def test_grade_space_404_and_its_member_cannot_reach_school(self):
        g3 = Space.objects.create(school=self.school, grade=3, name="새솔초 3학년")
        u = Member.objects.create(space=g3, user=self.far_user.__class__.objects.create(username="google:g3", first_name="삼학년"),
                                  name="삼학년", cls="3-1", is_manager=True)
        c = Client()
        c.force_login(u.user)
        self.assertEqual(c.post(f"/s/{g3.token}/school/arrange", {"what": "groups", "keys": "[]"}).status_code, 404)
        before = self.snapshot()
        self.assertEqual(c.post(self.url, {"what": "groups", "keys": '["meal"]'}).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_expired_hides_button(self):
        self.expire()
        page = self.manager.get(f"{self.base}/school")
        self.assertNotContains(page, "순서·소속 바꾸기")
        self.assertNotContains(page, "school_arrange.js")


class TamperTests(ArrangeBase):
    def test_group_keys_duplicates_and_unknown_dropped(self):
        self.post(what="groups", keys=["meal", "meal", "nope", "", "office"])
        self.hub.refresh_from_db()
        self.assertEqual(self.hub.group_order, "meal,office")
        self.assertEqual([g for g, _ in self.hub.group_choices][:3], ["meal", "office", "g1"])

    def test_non_list_or_non_string_keys_400(self):
        for bad in ('{"a":1}', "[1,2]", '"office"', json.dumps(["x"] * 501)):
            r = self.manager.post(self.url, {"what": "groups", "keys": bad}, headers={"HX-Request": "true"})
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.post(what="nope", keys=[]).status_code, 400)

    def test_member_keys_missing_and_duplicated_keep_everyone(self):
        late = self.member("d", "교무교사", "교무실", "office", "교무")
        self.post(what="members", group="office", keys=[late.key, late.key, "nokey"])
        office = self.office(self.teacher.get(f"{self.base}/school"))
        self.assertEqual(office, ["교무교사", "교장선생", "교감선생"])  # 빠진 사람은 원래 순서로 뒤에
        self.assertEqual(Member.objects.in_space(self.hub).filter(group="office").count(), 3)

    def test_class_of_other_grade_refused(self):
        r = self.post(what="members", group="g4", keys=[self.cook.key], cls="5-1")
        self.assertEqual(r.status_code, 400)
        self.cook.refresh_from_db()
        self.assertEqual((self.cook.group, self.cook.cls), ("meal", "급식소"))
        self.assertFalse(MemberLog.objects.exists())

    def test_move_into_grade_class_and_bell_link(self):
        self.post(what="members", group="g5", keys=[self.cook.key], cls="5-3")
        self.cook.refresh_from_db()
        self.assertEqual((self.cook.group, self.cook.cls, self.cook.position), ("g5", "5-3", "3반"))
        bell = Notification.objects.get(member=self.cook, text__contains="옮겼어요")
        self.assertEqual(bell.space_id, self.hub.pk)
        self.assertEqual(bell.link, f"/s/{self.hub.token}/school")
        self.assertIn("5학년 3반으로", bell.text)

    def test_reorder_only_no_bell(self):
        self.post(what="members", group="office", keys=[self.vice.key, self.boss.key])
        self.assertFalse(Notification.objects.filter(text__contains="옮겼어요").exists())

    def test_duty_keys_garbage_keep_all_rows(self):
        ds = [Duty.objects.create(space=self.hub, dept=d, task=t, who="", order=i)
              for i, (d, t) in enumerate([("교무부", "학사"), ("교무부", "학적"), ("연구부", "연수")])]
        far = Duty.objects.create(space=self.far, dept="교무부", task="먼일", who="", order=0)
        r = self.post(what="duties", group="교무부", keys=["abc", str(far.pk), str(ds[1].pk), str(ds[1].pk)])
        self.assertEqual(r.status_code, 200)
        rows = list(Duty.objects.in_space(self.hub).order_by("order").values_list("dept", "task"))
        self.assertEqual(rows, [("교무부", "학적"), ("교무부", "학사"), ("연구부", "연수")])
        far.refresh_from_db()
        self.assertEqual((far.dept, far.order), ("교무부", 0))
        self.post(what="depts", keys=["없는부", "연구부"])
        rows = list(Duty.objects.in_space(self.hub).order_by("order").values_list("dept", flat=True))
        self.assertEqual(rows, ["연구부", "교무부", "교무부"])
        extra = Duty.objects.create(space=self.hub, dept="연구부", task="평가", who="", order=9)
        self.assertEqual(self.post(what="duties", group="교무부", keys=[str(ds[2].pk), str(extra.pk), str(ds[0].pk)]).status_code, 400)
        self.assertEqual(Duty.objects.in_space(self.hub).get(pk=ds[2].pk).dept, "연구부")  # 두 줄 이상 옮기기는 통째로 취소


class OrderUseTests(ArrangeBase):
    def test_nudge_groups_follow_school_order(self):
        self.post(what="groups", keys=["meal", "office", "g5"])
        self.hub.refresh_from_db()
        from collect.models import Collection, Row
        c = Collection.objects.create(space=self.hub, created_by=self.m4, title="취합", source=Collection.Source.SHEET)
        miss = [{"row": Row.objects.create(collection=c, pos=i, unit=m.name, member=m, who=m.name, group=m.group), "label": m.name}
                for i, m in enumerate((self.m4, self.boss, self.cook, self.m5))]
        labels = [g["label"] for g in _miss_groups(c, miss)]
        self.assertEqual(labels, ["급식소", "교무실", "5학년", "4학년"])

    def test_plain_get_is_full_page_htmx_get_is_body(self):
        full = self.teacher.get(f"{self.base}/school")
        self.assertContains(full, "<html")
        part = self.teacher.get(f"{self.base}/school", headers={"HX-Request": "true"})
        self.assertNotContains(part, "<html")
        self.assertContains(part, 'id="schoolMain"')
