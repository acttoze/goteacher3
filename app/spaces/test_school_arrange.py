"""Q2 학교 화면 순서·소속 바꾸기: 관리자만 저장(일반 교사 403, 다른 공간 404, 플랜 끝 403), 새로고침 뒤 순서 유지,
다른 소속으로 옮기면 MemberLog·🔔, 학년으로 옮기면 반 필요, 업무분장 부서·줄 순서와 부서 간 이동, 취합 소속순 반영."""
import json

from django.test import Client

from collect.models import Notification
from collect.recipients import audience_form, audience_rows

from .models import Duty, Member, MemberLog
from .test_school_page import Base


class ArrangeBase(Base):
    def setUp(self):
        super().setUp()
        self.url = f"{self.base}/school/arrange"
        self.boss = self.member("b", "교장선생", "교무실", "office", "교장")
        self.vice = self.member("c", "교감선생", "교무실", "office", "교감")
        self.cook = self.member("k", "영양선생", "급식소", "meal", "영양교사")

    def post(self, client=None, **data):
        data["keys"] = json.dumps(data.get("keys", []))
        return (client or self.manager).post(self.url, data, headers={"HX-Request": "true"})

    def office(self, page):
        return [r["m"].name for c in page.context["group_cards"] if c["g"] == "office" for r in c["rows"]]


class PermissionTests(ArrangeBase):
    def test_teacher_403_other_space_404_expired_403_and_nothing_changes(self):
        self.assertEqual(self.post(self.teacher, what="groups", keys=["office"]).status_code, 403)
        far = Client()
        far.force_login(self.far_user)
        self.assertEqual(far.post(f"/s/{self.far.token}/school/arrange", {"what": "groups", "keys": '["office"]'}).status_code, 404)
        self.assertEqual(far.post(self.url, {"what": "groups", "keys": '["office"]'}).status_code, 404)
        self.expire()
        self.assertEqual(self.post(what="groups", keys=["office"]).status_code, 403)
        self.hub.refresh_from_db()
        self.assertEqual(self.hub.group_order, "")
        self.assertEqual(self.manager.get(f"{self.base}/school?arrange=1").context["arrange"], False)

    def test_get_not_allowed_and_handles_only_for_manager(self):
        self.assertEqual(self.manager.get(self.url).status_code, 405)
        page = self.manager.get(f"{self.base}/school?arrange=1")
        self.assertContains(page, 'data-arr-box="groups"')
        self.assertContains(page, "school_arrange.js")
        page = self.teacher.get(f"{self.base}/school?arrange=1")
        self.assertNotIn(b"data-arr-box", page.content)
        self.assertNotContains(page, "school_arrange.js")
        self.assertNotContains(page, "순서·소속 바꾸기")

    def test_other_space_member_key_is_ignored(self):
        r = self.post(what="members", group="office", keys=[self.far_member.key, self.vice.key, self.boss.key])
        self.assertEqual(r.status_code, 200)
        self.far_member.refresh_from_db()
        self.assertEqual(self.far_member.group, "")

    def test_bad_keys_400(self):
        r = self.manager.post(self.url, {"what": "groups", "keys": "{"}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.post(what="members", group="nope", keys=[]).status_code, 400)


class GroupOrderTests(ArrangeBase):
    def test_group_order_saved_and_kept_after_reload(self):
        r = self.post(what="groups", keys=["office", "meal", "g5"])
        self.assertEqual(r.status_code, 200)
        page = self.teacher.get(f"{self.base}/school")
        labels = [c["label"] for c in page.context["group_cards"]]
        self.assertEqual(labels, ["교무실", "급식소", "5학년", "4학년"])

    def test_member_order_inside_group(self):
        self.assertEqual(self.office(self.teacher.get(f"{self.base}/school")), ["교장선생", "교감선생"])
        self.post(what="members", group="office", keys=[self.vice.key, self.boss.key])
        self.assertEqual(self.office(self.teacher.get(f"{self.base}/school")), ["교감선생", "교장선생"])
        self.assertFalse(MemberLog.objects.in_space(self.hub).exists())  # 순서만 바꾸면 이력 없음
        late = self.member("d", "새로온분", "교무실", "office", "교무")  # 순서를 정한 뒤 들어온 사람은 뒤에
        self.assertEqual(self.office(self.teacher.get(f"{self.base}/school")), ["교감선생", "교장선생", "새로온분"])
        self.assertIsNone(late.order)

    def test_arrange_shows_empty_groups_as_cards(self):
        page = self.manager.get(f"{self.base}/school?arrange=1")
        self.assertEqual(len(page.context["group_cards"]), 10)
        self.assertContains(page, "여기로 끌어 놓을 수 있어요")


class MoveMemberTests(ArrangeBase):
    def test_move_out_of_grade_logs_and_bells(self):
        r = self.post(what="members", group="office", keys=[self.boss.key, self.m5.key, self.vice.key])
        self.assertEqual(r.status_code, 200)
        self.m5.refresh_from_db()
        self.assertEqual((self.m5.group, self.m5.cls, self.m5.position), ("office", "교무실", ""))
        log = MemberLog.objects.in_space(self.hub).get()
        self.assertEqual((log.by, log.name, log.before, log.after), ("송민재", "김도윤", "5학년 1반", "교무실"))
        bell = Notification.objects.get(member=self.m5, text__contains="옮겼어요")
        self.assertIn("교무실로 옮겼어요", bell.text)
        self.assertEqual(self.office(self.teacher.get(f"{self.base}/school")), ["교장선생", "김도윤", "교감선생"])

    def test_move_between_non_grade_keeps_position(self):
        self.post(what="members", group="support", keys=[self.cook.key])
        self.cook.refresh_from_db()
        self.assertEqual((self.cook.group, self.cook.cls, self.cook.position), ("support", "기타/지원", "영양교사"))

    def test_move_into_grade_needs_class(self):
        r = self.post(what="members", group="g4", keys=[self.cook.key])
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "몇 반인지", status_code=400)
        self.cook.refresh_from_db()
        self.assertEqual(self.cook.group, "meal")
        self.assertFalse(MemberLog.objects.in_space(self.hub).exists())
        r = self.post(what="members", group="g4", keys=[self.m4.key, self.cook.key, self.m4b.key], cls="4-전담")
        self.assertEqual(r.status_code, 200)
        self.cook.refresh_from_db()
        self.assertEqual((self.cook.group, self.cook.cls, self.cook.position), ("g4", "4-전담", "전담"))
        self.assertEqual(MemberLog.objects.in_space(self.hub).get().after, "4학년 전담")
        g4 = next(c for c in self.teacher.get(f"{self.base}/school").context["group_cards"] if c["g"] == "g4")
        self.assertEqual([r["m"].name for r in g4["rows"]], ["송민재", "영양선생", "정하은"])

    def test_class_choices_in_dialog_default_jeondam(self):
        page = self.manager.get(f"{self.base}/school?arrange=1")
        self.assertContains(page, 'data-arr-cls="g4"')
        self.assertContains(page, '<option value="4-전담" selected>전담</option>', html=False)

    def test_one_person_at_a_time(self):
        r = self.post(what="members", group="support", keys=[self.cook.key, self.boss.key])
        self.assertEqual(r.status_code, 400)
        self.cook.refresh_from_db()
        self.assertEqual(self.cook.group, "meal")

    def test_moving_myself_has_log_but_no_bell(self):
        self.post(what="members", group="office", keys=[self.m4.key])
        self.assertEqual(MemberLog.objects.in_space(self.hub).count(), 1)
        self.assertFalse(Notification.objects.filter(member=self.m4, text__contains="옮겼어요").exists())


class DutyArrangeTests(ArrangeBase):
    def setUp(self):
        super().setUp()
        rows = [("교무부", "학사", "송민재"), ("교무부", "학적", "김도윤"), ("연구부", "연수", "정하은"), ("연구부", "평가", "")]
        self.duties = [Duty.objects.create(space=self.hub, dept=d, task=t, who=w, order=i) for i, (d, t, w) in enumerate(rows)]

    def cards(self):
        page = self.teacher.get(f"{self.base}/school?tab=duty")
        return [(c["dept"], [r["d"].task for r in c["rows"]]) for c in page.context["dept_cards"]]

    def test_dept_order(self):
        self.assertEqual(self.post(what="depts", keys=["연구부", "교무부"]).status_code, 200)
        self.assertEqual(self.cards(), [("연구부", ["연수", "평가"]), ("교무부", ["학사", "학적"])])

    def test_row_order_and_move_to_other_dept(self):
        d = self.duties
        self.post(what="duties", group="교무부", keys=[str(d[1].pk), str(d[0].pk)])
        self.assertEqual(self.cards(), [("교무부", ["학적", "학사"]), ("연구부", ["연수", "평가"])])
        r = self.post(what="duties", group="교무부", keys=[str(d[1].pk), str(d[3].pk), str(d[0].pk)])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.cards(), [("교무부", ["학적", "평가", "학사"]), ("연구부", ["연수"])])

    def test_unknown_dept_and_other_space_duty(self):
        far = Duty.objects.create(space=self.far, dept="먼부", task="먼일", who="", order=0)
        self.assertEqual(self.post(what="duties", group="없는부", keys=[str(self.duties[0].pk)]).status_code, 400)
        self.post(what="duties", group="교무부", keys=[str(far.pk)])
        far.refresh_from_db()
        self.assertEqual(far.dept, "먼부")

    def test_dept_order_used_by_collect_dept_sort(self):
        self.post(what="depts", keys=["연구부", "교무부"])
        rows = audience_rows(self.hub, {"mode": "all", "sort": "dept"})
        self.assertEqual(rows[0]["unit"], "연구부")


class CollectOrderTests(ArrangeBase):
    def test_collect_group_order_follows_school(self):
        self.post(what="groups", keys=["meal", "office"])
        self.post(what="members", group="office", keys=[self.vice.key, self.boss.key])
        self.hub.refresh_from_db()
        names = [r["who"] for r in audience_rows(self.hub, {"mode": "all"})]
        self.assertEqual(names[:3], ["영양선생", "교감선생", "교장선생"])
        form = audience_form(self.hub, {"mode": "all"})
        self.assertEqual([g["id"] for g in form["aud_groups"]][:3], ["meal", "office", "g1"])
        self.assertEqual(form["aud_people"][0]["label"], "급식소")

    def test_default_order_unchanged(self):
        self.assertEqual([g for g, _ in self.hub.group_choices], Member.Group.values)
