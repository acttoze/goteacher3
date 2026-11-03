"""H1 독립 검증(gt-qa): test_merge.py에 없는 경계만. expectedFailure는 확인된 결함이다(고치면 표시를 지운다)."""
import unittest
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import Client

from accounts.models import DeviceToken
from collect.models import Cell, Collection, Notification, Row
from collect.test_hwp import table_doc
from hwpfiles.models import ResultFile, SourceFile
from today.models import Event

from .merge import open_school
from .models import Member, School, Space
from .test_merge import UNTIL, MergeBase, fill

User = get_user_model()


class IsolationQa(MergeBase):
    """다른 학교 사용자·바꿔 끼운 토큰은 합친 뒤에도 404."""

    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)
        self.client.force_login(self.u4)
        self.far_client = Client()
        self.far_client.force_login(self.far_user)

    def test_row_key_of_other_collection_in_same_school_space_is_404(self):
        """합치면 두 모으기가 같은 공간이 된다. 링크와 줄 키를 바꿔 끼워도 열리지 않아야 한다."""
        anon = Client()
        path = f"/c/{self.c4.link_token}/u/{self.r5.key}"
        self.assertEqual(anon.get(path).status_code, 404)
        self.assertEqual(anon.post(path, {"f0": "99", "by": "끼움"}).status_code, 404)
        self.assertEqual(Cell.objects.get(row=self.r5).value, "3")

    def test_far_member_cannot_open_moved_objects_through_own_space(self):
        base = f"/s/{self.far.token}"
        for path in (f"/c/{self.c4.key}/", f"/c/{self.c4.key}/copy", f"/c/{self.c4.key}/export.csv", f"/c/{self.c4.key}/live",
                     f"/hwp/{self.sf4.key}/", f"/hwp/{self.sf4.key}/hwpx", f"/hwp/out/{self.rf4.key}/hwpx"):
            self.assertEqual(self.far_client.get(base + path).status_code, 404, path)

    def test_hub_member_cannot_open_far_objects_or_write(self):
        base = f"/s/{self.hub.token}"
        far_sf = SourceFile.objects.get(space=self.far)
        for path in (f"/c/{self.far_c.key}/", f"/c/{self.far_c.key}/copy", f"/hwp/{far_sf.key}/hwpx"):
            self.assertEqual(self.client.get(base + path).status_code, 404, path)
        self.assertEqual(self.client.post(f"{base}/c/{self.far_c.key}/lock").status_code, 404)
        self.far_c.refresh_from_db()
        self.assertIsNone(self.far_c.closed_at)

    def test_two_merged_schools_do_not_forward_into_each_other(self):
        far_hub = open_school(self.far_school, UNTIL)
        for c, old in ((self.far_client, self.g4), (self.client, self.far)):
            self.assertEqual(c.get(f"/s/{old.token}/today").status_code, 404)
        self.assertRedirects(self.far_client.get(f"/s/{self.far.token}/today"), f"/s/{far_hub.token}/today",
                             fetch_redirect_response=False)

    def test_old_invite_does_not_seat_member_of_other_school(self):
        self.assertContains(self.far_client.get(f"/j/{self.g4.invite_token}"), "학교 공간으로 바뀌었어요")
        r = self.far_client.post("/join", {"cls": "4-3", "name": "끼어들기"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Member.objects.filter(name="끼어들기").exists())
        self.assertEqual(Member.objects.filter(user=self.far_user).count(), 1)


class MergeDataQa(MergeBase):
    def ids(self):
        spaces = self.school.spaces.all()
        return {
            "members": sorted(Member.objects.filter(space__in=spaces).values_list("pk", "key", "name", "cls")),
            "colls": sorted(Collection.objects.filter(space__in=spaces).values_list("pk", "key", "link_token", "title")),
            "rows": sorted(Row.objects.filter(collection__space__in=spaces).values_list("pk", "key", "unit", "who", "member_id")),
            "cells": sorted(Cell.objects.filter(row__collection__space__in=spaces).values_list("pk", "value", "by")),
            "events": sorted(Event.objects.filter(space__in=spaces).values_list("pk", "key", "title", "by_id")),
            "notes": sorted(Notification.objects.filter(space__in=spaces).values_list("pk", "key", "member_id", "kind", "text")),
            "hwp": sorted(SourceFile.objects.filter(space__in=spaces).values_list("pk", "key", "original", "collection_id")),
            "out": sorted(ResultFile.objects.filter(space__in=spaces).values_list("pk", "key", "hwpx", "source_id")),
        }

    def test_nothing_but_space_changes(self):
        """받은 링크·줄 키·제출 값·파일 경로까지 그대로여야 이미 보낸 쪽지가 산다."""
        before = self.ids()
        open_school(self.school, UNTIL, self.m4)
        self.assertEqual(self.ids(), before)

    def test_user_seated_in_two_schools_keeps_other_school_seat(self):
        seat = Member.objects.create(space=self.far, user=self.u4, name="송민재", cls="4-2")
        open_school(self.school, UNTIL)
        seat.refresh_from_db()
        self.assertEqual(seat.space, self.far)
        self.assertEqual(Member.objects.filter(user=self.u4).count(), 2)

    def test_atpt_keeps_same_code_school_apart(self):
        twin = School.objects.create(neis_atpt="C10", neis_code=self.school.neis_code, name="쌍둥이초")
        twin_sp = Space.objects.create(school=twin, grade=4, name="쌍둥이 4학년")
        with self.assertRaises(CommandError):
            call_command("school_plan", self.school.neis_code, "--on", "--until", "2027-02-28", stdout=StringIO())
        self.school.refresh_from_db()
        self.assertEqual(self.school.plan, "free")
        call_command("school_plan", self.school.neis_code, "--atpt", "J10", "--on", "--until", "2027-02-28",
                     f"--manager={self.m4.key}", stdout=StringIO())
        twin.refresh_from_db()
        twin_sp.refresh_from_db()
        self.assertEqual((twin.plan, twin_sp.merged_into_id), ("free", None))

    def test_off_then_on_keeps_one_school_space(self):
        hub = open_school(self.school, UNTIL)
        call_command("school_plan", self.school.neis_code, "--off", stdout=StringIO())
        self.assertEqual(open_school(self.school, UNTIL).pk, hub.pk)
        self.assertEqual(Space.objects.filter(school=self.school, grade=0).count(), 1)

    def test_school_without_spaces_opens_and_invite_page_lives(self):
        empty = School.objects.create(neis_atpt="D10", neis_code="1110001", name="빈초등학교")
        hub = open_school(empty, UNTIL)
        r = Client().get(f"/j/{hub.invite_token}", follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "0학년")


class NoManagerQa(MergeBase):
    """--manager 없이 켠 학교: 대표가 없어도 화면이 깨지면 안 된다."""

    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL)
        self.client.force_login(self.u5)
        self.base = f"/s/{self.hub.token}"

    def test_screens_live(self):
        for path in ("/today", "/c/", f"/c/{self.c4.key}/", f"/c/{self.c5.key}/", "/c/new", "/schedule", "/", "/members",
                     "/welcome", "/made", "/settings", "/settings?a=alerts", "/settings?a=space", "/settings?a=device"):
            r = self.client.get(self.base + path, follow=True)
            self.assertEqual(r.status_code, 200, path)
            self.assertNotContains(r, "0학년")

    def test_same_seat_from_new_browser_without_manager_has_no_alert_and_no_error(self):
        guest = User.objects.create(username="guest:qa")
        seat = Member.objects.create(space=self.hub, user=guest, name="손님", cls="4-3", group="g4")
        DeviceToken.issue(guest)
        c = Client()
        c.get(f"/j/{self.hub.invite_token}")
        r = c.post("/join", {"group": "g4", "cls": "4-3", "name": "손님"})
        self.assertEqual(r.status_code, 403)  # 학교 공간은 구글 필수, 같은 반·이름으로 자리를 잇지 않는다(리드 결정)
        self.assertEqual(Member.objects.filter(space=self.hub, name="손님").count(), 1)
        self.assertFalse(Notification.objects.filter(kind="seat", text__contains="손님").exists())
        self.assertEqual(seat.pk, Member.objects.get(name="손님").pk)


class SeatAlertQa(MergeBase):
    def test_school_seat_cannot_be_reentered_so_no_alert(self):
        hub = open_school(self.school, UNTIL, self.m5)
        guest = User.objects.create(username="guest:qa")
        Member.objects.create(space=hub, user=guest, name="손님", cls="4-3", group="g4")
        DeviceToken.issue(guest)
        c = Client()
        c.get(f"/j/{hub.invite_token}")
        self.assertEqual(c.post("/join", {"group": "g4", "cls": "4-3", "name": "손님"}).status_code, 403)
        self.assertFalse(Notification.objects.filter(kind="seat", text__contains="손님").exists())


class SettingsLeadQa(MergeBase):
    def test_lead_badge_follows_manager_not_join_order(self):
        hub = open_school(self.school, UNTIL, self.m5)  # 관리자는 나중에 들어온 김도윤, 가장 먼저 들어온 사람은 송민재
        self.client.force_login(self.u5)
        r = self.client.get(f"/s/{hub.token}/settings?a=space")
        html = r.content.decode()
        first_row = html[html.index("송민재"):html.index("정하은")]
        self.assertNotIn("학년 대표", first_row)


class HwpAfterMergeQa(MergeBase):
    def form(self, base, sf, title):
        f = self.client.get(f"{base}/c/new/hwp/{sf.key}").context["hwpf"]
        self.assertFalse(f["error"])
        data = {"src": "hwp", "title": title, "hwp": sf.key, "hwp_sent": "1", "hwp_table": f["table"],
                "hwp_items": str(len(f["items"])), "outside": "on"}
        for it in f["items"]:
            data.update({f"item_{it['i']}_role": it["role"], f"item_{it['i']}_name": it["name"],
                         f"item_{it['i']}_kind": it["kind"]})
        for x in f["rows"]:
            if x["on"]:
                data[f"row_{x['r']}"] = "on"
        return data

    def make(self, base, space, heads, name):
        doc = table_doc([["반", "담임", "참가(명)", "불참(명)", "비고"]] + [[h, "", "", "", ""] for h in heads] + [["합계", "", "", "", ""]])
        r = self.client.post(f"{base}/hwp/upload?pick=1", {"file": SimpleUploadedFile(name, doc)})
        self.assertEqual(r.status_code, 200)
        sf = SourceFile.objects.get(space=space, name=name)
        self.assertEqual(self.client.post(f"{base}/hwp/{sf.key}/pick", {"sec": 0, "index": 1}).status_code, 200)
        sf.refresh_from_db()
        self.assertEqual(self.client.post(f"{base}/c/new", self.form(base, sf, name)).status_code, 302)
        sf.refresh_from_db()
        return sf, sf.collection

    def test_real_form_made_before_merge_fills_after_merge(self):
        self.client.force_login(self.u4)
        sf, c = self.make(f"/s/{self.g4.token}", self.g4, ["4-1", "4-2"], "합치기전.hwpx")
        hub = open_school(self.school, UNTIL, self.m4)
        fields = list(c.fields.all())
        anon = Client()
        for row in c.rows.all():
            vals = {f"f{f.pos}": "2" if f.kind == "number" else "메모" for f in fields}
            self.assertEqual(anon.post(f"/c/{c.link_token}/u/{row.key}", {**vals, "by": row.who}).status_code, 200)
        self.assertEqual(self.client.post(f"/s/{hub.token}/c/{c.key}/hwp").status_code, 200)
        rf = ResultFile.objects.get(collection=c)
        self.assertEqual((rf.status, rf.space_id), (ResultFile.Status.DONE, hub.pk))
        r = self.client.get(f"/s/{hub.token}/hwp/out/{rf.key}/hwpx")
        self.assertEqual(b"".join(r.streaming_content)[:2], b"PK")

    @unittest.expectedFailure  # 결함(H4로 넘김): 학교 공간에서 hwpform에 grade=0이 넘어가 'n-m' 머리가 다른 학년으로 읽힌다
    def test_new_form_in_school_space_matches_rows_by_grade_and_class(self):
        hub = open_school(self.school, UNTIL, self.m4)
        self.client.force_login(self.u4)
        sf, c = self.make(f"/s/{hub.token}", hub, ["5-1", "4-1", "4-2"], "학교표.hwpx")
        unit = dict(c.rows.values_list("key", "unit"))
        got = {unit[x["row"]]: x["r"] for x in sf.cellmap["lines"]}
        self.assertEqual(got, {"5-1": 1, "4-1": 2, "4-2": 3})


class LateMembersQa(MergeBase):
    def test_duplicate_seat_rows_files_survive_second_run(self):
        hub = open_school(self.school, UNTIL)
        late = Space.objects.create(school=self.school, grade=2, name="2학년")
        dup = Member.objects.create(space=late, user=self.u4, name="송민재", cls="2-1")
        c, row, sf, rf = fill(late, dup, "늦은겹침")
        open_school(self.school, UNTIL)
        with self.assertRaises(ObjectDoesNotExist):
            dup.refresh_from_db()
        c.refresh_from_db()
        row.refresh_from_db()
        sf.refresh_from_db()
        self.assertEqual((c.space_id, c.created_by_id, row.member_id, sf.space_id, sf.created_by_id),
                         (hub.pk, self.m4.pk, self.m4.pk, hub.pk, self.m4.pk))
