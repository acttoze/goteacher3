"""W7 QA(gt-qa): 구현 테스트(test_w7_bulk.py)에 없는 경계만 더한다.

- 접근: 로그인 안 함, 같은 학교 동학년 회원(학교 공간 회원 아님), 내 토큰 대신 다른 학교 미리보기 주소.
- 원문 비보관: 미리보기는 아무것도 저장하지 않고, 캐시·500 오류 로그에도 이름·코드가 없다.
- 폼 조작: n을 크게·음수로, n 밖 줄은 무시.
- 기존 빈 자리(주인 없음)와 같은 줄은 건너뛰고 코드도 안 나간다.
- 쪽지 코드로 /enter → 아이디 가입 또는 아이디 로그인 → 연결 → 학교 화면 완주, 코드 재사용 거절.
- 브라우저 정리: 주소 열, 제목 줄 아래 머리글.
"""
import json
import pickle
import re
import shutil
import subprocess
from unittest import mock, skipUnless

from django.core.cache import caches
from django.test import Client

from config.logsafe import SafeErrorFormatter

from .merge import open_free
from .models import EntryCode, Member, MemberLog, School, Space
from .test_w7_bulk import CODE, JS, SITE, Base, User, account, form, logged, seat, tsv


class AccessQA(Base):
    def test_anonymous_gets_no_page(self):
        c = Client()
        for r in (c.get(self.url), c.post(self.url, form([{"name": "가", "group": "g4", "cls": "4-3"}])),
                  c.post(self.url + "/preview", {"paste": "가\t4학년\t3반"})):
            self.assertIn(r.status_code, (302, 403, 404))
            self.assertNotIn("입장 코드", r.content.decode())
        self.assertFalse(self.new_seats().exists())

    def test_same_school_grade_member_not_in_hub(self):
        g5 = Space.objects.create(school=self.school, grade=5, name="5학년")
        teacher = seat(g5, "오교사", "5-2")
        c = logged(teacher.user)
        self.assertEqual(c.get(self.url).status_code, 404)
        self.assertEqual(c.post(self.url, form([{"name": "가", "group": "g4", "cls": "4-3"}])).status_code, 404)
        self.assertEqual(c.post(self.url + "/preview", {"paste": "가\t4학년\t3반"}).status_code, 404)
        self.assertFalse(self.new_seats().exists())

    def test_my_session_on_other_school_token(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7530199", name="옆초등학교")
        g = Space.objects.create(school=other, grade=4, name="4학년")
        hub = open_free(g, seat(g, "옆관리", "4-1"))
        url = f"/s/{hub.token}/school/bulk"
        self.assertEqual(self.admin.get(url).status_code, 404)
        self.assertEqual(self.admin.post(url + "/preview", {"paste": "가\t4학년\t3반"}).status_code, 404)
        self.assertEqual(self.admin.post(url, form([{"name": "가", "group": "g4", "cls": "4-3"}])).status_code, 404)
        self.assertFalse(Member.objects.in_space(hub).filter(user__isnull=True).exists())

    def test_get_with_method_put_refused(self):
        self.assertEqual(self.admin.put(self.url).status_code, 405)
        self.assertEqual(self.admin.get(self.url + "/preview").status_code, 405)


class KeepNothingQA(Base):
    ROWS = [{"name": "홍길동", "group": "g4", "cls": "4-2"}, {"name": "이교감", "group": "office", "role": "교감"}]

    def test_preview_saves_nothing(self):
        members, logs = Member.objects.count(), MemberLog.objects.count()
        r = self.preview(tsv(["이름", "소속", "반"], *[[f"사람{i}", "4학년", "1반"] for i in range(40)]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual((Member.objects.count(), MemberLog.objects.count(), EntryCode.objects.count()), (members, logs, 0))

    def test_codes_not_in_caches_or_later_pages(self):
        r = self.make(self.ROWS)
        codes = {c.replace("-", "") for c in CODE.findall(r.content.decode())}
        self.assertEqual(len(codes), 2)
        blob = b""
        for alias in ("default", "ratelimit"):
            store = getattr(caches[alias], "_cache", {})
            blob += pickle.dumps(dict(store))
        for page in (self.url, f"/s/{self.hub.token}/school", "/enter"):
            blob += self.admin.get(page).content
        text = blob.decode("utf-8", "ignore").replace("-", "")
        for code in codes:
            self.assertNotIn(code, text)

    def test_server_error_log_has_no_names_or_codes(self):
        real, calls = __import__("spaces.entry", fromlist=["issue"]).issue, []

        def flaky(m, by):
            calls.append(m)
            if len(calls) == 2:
                raise RuntimeError("홍길동 이교감 boom")
            return real(m, by)
        c = Client(raise_request_exception=False)
        c.force_login(self.head.user)
        with mock.patch("spaces.entry.issue", side_effect=flaky), self.assertLogs("django.request", level="ERROR") as cm:
            r = c.post(self.url, form(self.ROWS))
        self.assertEqual(r.status_code, 500)
        for rec in cm.records:
            rec.exc_text = None  # assertLogs 처리기가 기본 형식으로 만든 예외 글을 지우고 운영 형식으로 다시 만든다
        out = "\n".join(SafeErrorFormatter().format(rec) for rec in cm.records)
        for word in ("홍길동", "이교감", "boom", self.hub.token):
            self.assertNotIn(word, out)
        self.assertFalse(self.new_seats().exists())
        self.assertFalse(EntryCode.objects.exists())


class FormQA(Base):
    def test_huge_or_negative_n(self):
        r = self.admin.post(self.url, {"n": "1000000"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "150명", status_code=400)
        r = self.admin.post(self.url, {"n": "-3", "r0-name": "가", "r0-group": "g4", "r0-cls": "4-3"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.admin.post(self.url, {"n": "x"}).status_code, 400)
        self.assertFalse(self.new_seats().exists())

    def test_rows_beyond_n_are_ignored(self):
        data = form([{"name": "하나", "group": "g4", "cls": "4-3"}])
        data.update({"r1-name": "둘", "r1-group": "g4", "r1-cls": "4-4"})
        self.assertContains(self.admin.post(self.url, data), "1명을 만들었어요")
        self.assertEqual(list(self.new_seats().values_list("name", flat=True)), ["하나"])

    def test_150_rows_make_in_one_go(self):
        # 같은 반 담임 3명 이상은 학생 명단으로 보므로(보안 권고) 전담으로 채운다
        rows = [{"name": f"교사{i:03d}", "group": f"g{i % 6 + 1}", "cls": f"{i % 6 + 1}-전담"} for i in range(150)]
        r = self.make(rows)
        self.assertContains(r, "150명을 만들었어요")
        self.assertEqual(r.content.decode().count('class="sheet"'), 19)  # 150 / 8 = 18.75
        self.assertEqual(EntryCode.objects.in_space(self.hub).count(), 150)

    def test_existing_empty_seat_is_skipped_without_code(self):
        empty = Member.objects.create(space=self.hub, name="홍길동", cls="4-2", group="g4", position="2반")
        rows = self.preview(tsv(["이름", "소속", "반"], ["홍길동", "4학년", "2반"], ["새분", "1학년", "1반"])).context["lines"]
        self.assertEqual([(r["name"], r["status"]) for r in rows], [("홍길동", "exists"), ("새분", "ok")])
        self.assertContains(self.make([{"name": "홍길동", "group": "g4", "cls": "4-2"}, {"name": "새분", "group": "g1", "cls": "1-1"}]),
                            "1명을 만들었어요")
        self.assertFalse(EntryCode.objects.filter(member=empty).exists())

    def test_mixed_40_rows_fix_first_and_counts(self):
        body = [["이름", "소속", "반"]]
        for i in range(40):
            body.append([f"사람{i:02d}", "체육부" if i % 10 == 3 else "4학년", "전담"])  # 같은 반 담임 3명 이상은 학생 명단으로 본다
        seat(self.hub, "사람05", "4-전담", group="g4")
        r = self.preview(tsv(*body))
        rows = r.context["lines"]
        self.assertEqual([x["status"] for x in rows[:4]], ["fix"] * 4)
        self.assertNotIn("fix", [x["status"] for x in rows[4:]])
        self.assertEqual(r.context["n"]["exists"], 1)
        self.assertFalse(r.context["ready"])
        self.assertContains(r, "disabled")


class FinishQA(Base):
    def slip_code(self):
        r = self.make([{"name": "홍길동", "group": "g4", "cls": "4-2"}])
        body = r.content.decode()
        self.assertNotIn(".xlsx", body)
        self.assertNotIn(".csv", body)
        self.assertNotIn("download", body)
        return CODE.search(body).group(0)

    def follow_to_school(self, c):
        r = c.post("/enter", {"claim": "1"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r["Location"], f"/s/{self.hub.token}/school")
        page = c.get(r["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.school.name)

    def test_signup_path(self):
        code = self.slip_code()
        c = Client()
        self.assertEqual(c.post("/enter", {"code": code.lower()}).status_code, 303)
        login = c.get("/enter")
        self.assertContains(login, "홍길동 선생님 자리예요")
        self.assertContains(login, "/signup?next=/enter")
        r = c.post("/signup", {"username": "hong_new", "password": "long-enough-pass-77", "password2": "long-enough-pass-77",
                               "name": "홍길동", "next": "/enter"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/enter")
        self.assertContains(c.get("/enter"), "내 계정에 연결")
        self.follow_to_school(c)
        m = Member.objects.in_space(self.hub).get(name="홍길동")
        self.assertEqual(m.user.username, "hong_new")
        # 같은 코드는 다시 못 쓴다
        again = Client()
        self.assertEqual(again.post("/enter", {"code": code}).status_code, 400)

    def test_login_path(self):
        code = self.slip_code()
        u = User.objects.create(username="hong_old", first_name="홍길동")
        u.set_password("long-enough-pass-77")
        u.save()
        c = Client()
        c.post("/enter", {"code": code})
        r = c.post("/auth/login", {"username": "hong_old", "password": "long-enough-pass-77", "next": "/enter"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/enter")
        self.follow_to_school(c)
        self.assertEqual(Member.objects.in_space(self.hub).get(name="홍길동").user, u)

    def test_join_with_different_class_has_no_code_hint(self):
        self.make([{"name": "홍길동", "group": "g4", "cls": "4-2"}])
        from .models import INVITE
        c = logged(account("hong2"))
        c.get(f"/j/{self.hub.invite_token}")
        self.assertIn(INVITE, c.session)
        r = c.post("/join", {"group": "g4", "cls": "4-3", "name": "홍길동"})
        self.assertNotContains(r, "미리 만든 자리", status_code=r.status_code)
        self.assertNotContains(r, f"{SITE}/enter", status_code=r.status_code)


@skipUnless(shutil.which("node"), "node 필요")
class BrowserCleanQA(Base):
    def js(self, expr):
        script = f"var B = require({json.dumps(str(JS))}); process.stdout.write(JSON.stringify({expr}));"
        return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=20).stdout)

    def test_address_columns_by_head(self):
        grid = [["성명", "부서", "반", "주소", "근무지 주소", "자택주소", "직위"],
                ["홍길동", "4학년", "2", "세종시 한누리대로 1", "세종시 2", "대전 3", "부장"]]
        got = self.js(f"B.clean({json.dumps(grid, ensure_ascii=False)})")
        self.assertEqual(got["rows"], [["성명", "부서", "반", "직위"], ["홍길동", "4학년", "2", "부장"]])

    def test_head_under_title_line_and_server_never_sees_it(self):
        grid = [["2027 교직원 명단"], ["이름", "소속", "반", "휴대폰", "생일"], ["홍길동", "4학년", "2반", "010-1111-2222", "3월 4일"]]
        got = self.js(f"B.toTsv(B.clean({json.dumps(grid, ensure_ascii=False)}).rows)")
        self.assertNotIn("010", got)
        self.assertNotIn("3월 4일", got)
        rows = self.preview(got).context["lines"]
        self.assertEqual([(r["name"], r["group"], r["cls"]) for r in rows], [("홍길동", "g4", "4-2")])

    def test_no_false_drop_of_staff_columns(self):
        grid = [["이름", "소속", "반", "직책"], ["김영희", "6학년", "3반", "부장"], ["박철수", "교무실", "", "교무부장"],
                ["이주무", "행정실", "", "주무관"], ["최보건", "기타", "", "보건교사"]]
        got = self.js(f"B.clean({json.dumps(grid, ensure_ascii=False)})")
        self.assertEqual(got["dropped"], [])
        self.assertEqual(got["rows"], grid)

    def test_leave_warning_and_print_present(self):
        src = JS.read_text(encoding="utf-8")
        self.assertIn("beforeunload", src)
        self.assertIn("window.print()", src)
        self.assertTrue(re.search(r"afterprint", src))
