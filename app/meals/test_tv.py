"""M2 급식 TV: 토큰 주소로 급식 정보만, 교직원 문구·오류 문구 없음, 재발급, 플랜 만료, 캐시(Redis).

화면 자체(40px·넘침·끊김 유지)는 브라우저로 확인한다. 여기서는 서버가 내주는 것만 본다.
"""
import json
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import Client, SimpleTestCase

from config.logsafe import SafeErrorFormatter
from notices.models import Notice
from spaces import neis
from spaces.merge import open_school
from spaces.models import Attendance, Member, School

from . import content
from .models import MealPhoto, MealSettings, new_tv_token
from .tests import TUE, Base, meal

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"
SAT = date(2026, 11, 7)
STAFF_WORDS = ("올려", "선생님", "영양사", "담당", "관리자", "플랜", "오류", "실패", "연결", "불러오", "NEIS", "로그인", "고티처")
ALL_KEYS = {"school", "date", "date_label", "known", "next", "week_title", "days", "hygiene", "slogan", "screens", "photo", "theme"}
MEAL_KEYS = {"dishes", "kcal", "nutr", "origin", "allergens", "groups"}


class TvBase(Base):
    def setUp(self):
        super().setUp()
        self.row, _ = MealSettings.objects.get_or_create(space=self.hub)
        self.url = f"/tv/{self.row.tv_token}"
        self.anon = Client()

    def data(self):
        r = self.anon.get(f"{self.url}/data")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def plant(self):
        """학교 안 정보를 심는다. TV 응답 어디에도 나오면 안 된다."""
        Member.objects.filter(pk=self.m4.pk).update(position="비밀직책표식")
        Notice.objects.create(space=self.hub, title="비밀공지표식", by_name="비밀작성자표식")
        Attendance.objects.create(space=self.hub, date=TUE, name="비밀복무표식", kind="연가")
        self.post_photo()  # by = 김도윤
        return ("비밀직책표식", "비밀공지표식", "비밀작성자표식", "비밀복무표식", "연가",
                self.m4.name, self.m4b.name, self.m5.name, self.m4.key)


class TokenTests(TvBase):
    def test_token_is_at_least_32_bytes_and_each_school_has_its_own(self):
        self.assertGreaterEqual(len(new_tv_token()), 43)
        far = open_school(self.far_school, None, self.far_member)
        other, _ = MealSettings.objects.get_or_create(space=far)
        self.assertNotEqual(other.tv_token, self.row.tv_token)

    def test_no_login_needed(self):
        for path in ("", "/data"):
            self.assertEqual(self.anon.get(self.url + path).status_code, 200)

    def test_unknown_and_old_token_look_the_same(self):
        old = self.url
        self.assertEqual(self.writer.post(f"{self.base}/tv/reissue").status_code, 200)
        self.row.refresh_from_db()
        self.assertNotEqual(f"/tv/{self.row.tv_token}", old)
        self.assertEqual(self.anon.get(f"/tv/{self.row.tv_token}/data").status_code, 200)
        for suffix in ("", "/data", "/photo/20261103"):
            a, b = self.anon.get(old + suffix), self.anon.get("/tv/없는토큰" + suffix)
            self.assertEqual((a.status_code, b.status_code), (404, 404))
            self.assertEqual(a.content, b.content)

    def test_get_only(self):
        for path in ("", "/data", "/photo/20261103"):
            for method in (self.anon.post, self.anon.put, self.anon.delete):
                self.assertEqual(method(self.url + path).status_code, 405)

    def test_headers_no_cache_no_index_no_cookie(self):
        self.post_photo()
        for path in ("", "/data", "/photo/20261103"):
            r = self.anon.get(self.url + path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("no-store", r["Cache-Control"])
            self.assertIn("noindex", r["X-Robots-Tag"])
            self.assertFalse(r.cookies)

    def test_page_names_an_icon(self):
        self.assertIn('rel="icon"', self.anon.get(self.url).content.decode())
        self.assertTrue((STATIC / "mark.svg").exists())

    def test_page_has_csp_and_no_inline_script(self):
        r = self.anon.get(self.url)
        self.assertNotIn("unsafe-inline", r["Content-Security-Policy"].split("script-src")[1].split(";")[0])
        html = r.content.decode()
        self.assertIn('name="robots"', html)
        self.assertEqual(html.count("<script"), html.count("<script src="))
        self.assertNotIn(" onclick", html)

    def test_many_misses_from_one_ip_do_not_stop_the_real_tv(self):
        """학교 전체가 IP 하나(NAT): 학생이 /tv/x를 많이 불러도 진짜 TV는 429를 받지 않는다."""
        ip = {"HTTP_X_FORWARDED_FOR": "203.0.113.7"}
        with mock.patch("meals.tv.MISSES_PER_IP", 3):
            codes = [self.anon.get("/tv/x", **ip).status_code for _ in range(5)]
            self.assertEqual(codes, [404, 404, 404, 429, 429])
            self.assertEqual(self.anon.get(f"{self.url}/data", **ip).status_code, 200)
            self.assertEqual(self.anon.get("/tv/x", HTTP_X_FORWARDED_FOR="203.0.113.8").status_code, 404)

    def test_real_token_is_counted_per_token_not_per_ip(self):
        with mock.patch("meals.tv.PER_TOKEN", 2):
            codes = [self.anon.get(f"{self.url}/data", HTTP_X_FORWARDED_FOR=f"203.0.113.{i}").status_code for i in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    def test_429_says_nothing(self):
        with mock.patch("meals.tv.PER_TOKEN", 0):
            r = self.anon.get(f"{self.url}/data")
        self.assertEqual(r.status_code, 429)
        self.assertNotIn(self.school.name, r.content.decode())

    def test_log_masks_tv_token(self):
        rec = logging.LogRecord("x", logging.ERROR, "", 0, f"Internal Server Error: /tv/{self.row.tv_token}/data", None, None)
        line = SafeErrorFormatter().format(rec)
        self.assertNotIn(self.row.tv_token, line)
        self.assertIn("/tv/***/data", line)


class OnlyMealInfoTests(TvBase):
    def test_nothing_but_meal_info_anywhere(self):
        words = self.plant()
        for path in ("", "/data", "/photo/20261103"):
            r = self.anon.get(self.url + path)
            self.assertEqual(r.status_code, 200)
            blob = b"".join(r.streaming_content) if r.streaming else r.content
            text = blob.decode("utf-8", "ignore") + json.dumps(dict(r.headers), ensure_ascii=False)
            for w in words:
                self.assertNotIn(w, text, f"{path}: {w}")

    def test_json_keys_are_exactly_the_whitelist(self):
        self.post_photo()
        d = self.data()
        self.assertEqual(set(d), ALL_KEYS)
        self.assertEqual(d["school"], self.school.name)
        self.assertEqual(d["date"], "20261103")
        self.assertEqual(d["date_label"], "11월 3일 화요일")
        day = next(x for x in d["days"] if x["ymd"] == "20261103")
        self.assertEqual(set(day), {"ymd", "label", "meal"})
        self.assertEqual(set(day["meal"]), MEAL_KEYS)
        self.assertEqual(set(day["meal"]["dishes"][0]), {"name", "allergy", "self"})
        self.assertNotIn("count", json.dumps(day["meal"]))  # 급식 인원은 넘기지 않는다

    def test_nutrition_only_three_kinds(self):
        rows = [meal("20261103", ("쌀밥", [])) | {"nutr": [{"name": n, "unit": "g", "value": 1.0}
                                                         for n in ("탄수화물", "단백질", "지방", "칼슘", "철분", "비타민C")]}]
        self.meals.return_value = rows
        day = next(x for x in self.data()["days"] if x["ymd"] == "20261103")
        self.assertEqual([x["name"] for x in day["meal"]["nutr"]], ["탄수화물", "단백질", "지방"])

    def test_allergens_list_names_and_dishes(self):
        day = next(x for x in self.data()["days"] if x["ymd"] == "20261103")
        got = {a["no"]: a for a in day["meal"]["allergens"]}
        self.assertEqual(sorted(got), [5, 9, 10])
        self.assertEqual(got[9]["name"], "새우")
        self.assertEqual(got[9]["dishes"], ["돈육김치찌개", "깍두기"])

    def test_screens_follow_saved_order_and_off(self):
        MealSettings.objects.in_space(self.hub).update(screens=[{"key": "week", "on": True}, {"key": "menu", "on": False}, {"key": "photo", "on": False}])
        keys = [s["key"] for s in self.data()["screens"]]
        self.assertEqual(keys[0], "week")
        self.assertNotIn("menu", keys)
        self.assertIn("photo", keys)  # 식판 사진은 끌 수 없다
        self.assertTrue(all("sec" in s for s in self.data()["screens"]))

    def test_hygiene_and_slogan_rotate_by_date(self):
        d = self.data()
        self.assertEqual(d["hygiene"]["title"], content.hygiene_of(TUE)["title"])
        self.assertEqual(d["slogan"], content.slogan_of(TUE))


class PhotoTests(TvBase):
    def test_photo_address_and_response(self):
        self.post_photo()
        p = self.row.space.meal_photos.get(date=TUE)
        d = self.data()
        self.assertEqual(d["photo"], f"{self.url}/photo/20261103?v={p.version}")
        r = self.anon.get(d["photo"])
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertTrue(b"".join(r.streaming_content).startswith(b"\xff\xd8"))

    def test_other_school_photo_is_404(self):
        far = open_school(self.far_school, None, self.far_member)
        MealPhoto.objects.create(space=far, date=TUE, photo="meal/far/x.jpg", stored=1)
        self.assertEqual(self.anon.get(f"{self.url}/photo/20261103").status_code, 404)  # 우리 학교에는 사진이 없다

    def test_only_today_is_served(self):
        self.post_photo(ymd="20261104")
        self.assertEqual(self.anon.get(f"{self.url}/photo/20261104").status_code, 404)
        self.assertEqual(self.anon.get(f"{self.url}/photo/nonsense").status_code, 404)

    def test_no_photo_means_null_and_menu_still_there(self):
        d = self.data()
        self.assertIsNone(d["photo"])
        self.assertTrue(next(x for x in d["days"] if x["ymd"] == "20261103")["meal"])


class StateTests(TvBase):
    def clean(self, payload=None):
        text = json.dumps(payload if payload is not None else self.data(), ensure_ascii=False)
        for w in STAFF_WORDS:
            self.assertNotIn(w, text, w)

    def test_no_photo_has_no_staff_words(self):
        self.clean()

    def test_screen_script_and_page_have_no_staff_or_error_words(self):
        for text in ((STATIC / "js" / "tv.js").read_text(), self.anon.get(self.url).content.decode()):
            body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
            for w in STAFF_WORDS:
                self.assertNotIn(w, body, w)

    def test_weekend_points_to_next_meal(self):
        self.localdate.return_value = SAT
        self.meals.return_value = [meal("20261109", ("쌀밥", []))]
        d = self.data()
        self.assertEqual(d["date"], "20261107")
        self.assertEqual(d["week_title"], "다음 주 급식")
        self.assertEqual(d["next"], {"ymd": "20261109", "label": "11월 9일 월요일"})
        self.assertIsNone(d["photo"])
        self.clean(d)

    def test_vacation_looks_further_for_next_meal(self):
        self.meals.side_effect = lambda a, c, s, e: [meal("20261201", ("쌀밥", []))] if s == "20261104" else []
        d = self.data()
        self.assertEqual(self.meals.call_args.args[2:], ("20261104", "20270102"))
        self.assertEqual(d["next"]["ymd"], "20261201")
        self.assertTrue(d["known"])
        self.assertEqual(self.meals.call_count, 2)

    def test_long_vacation_without_next_meal(self):
        self.meals.return_value = []
        d = self.data()
        self.assertIsNone(d["next"])
        self.assertTrue(d["known"])
        self.assertTrue(all(x["meal"] is None for x in d["days"]))

    def test_meal_today_does_not_look_further(self):
        d = self.data()
        self.assertIsNone(d["next"])
        self.assertEqual(self.meals.call_count, 1)

    def test_day_with_no_dishes_counts_as_no_meal(self):
        self.meals.return_value = [meal("20261103")]
        self.assertTrue(all(x["meal"] is None for x in self.data()["days"]))

    def test_neis_failure_is_quietly_unknown(self):
        self.meals.return_value = neis.Failed()
        d = self.data()
        self.assertFalse(d["known"])
        self.assertTrue(all(x["meal"] is None for x in d["days"]))
        self.assertIsNone(d["next"])
        self.assertEqual(self.meals.call_count, 1)
        self.clean(d)

    def test_expired_plan_is_school_name_and_date_only(self):
        self.post_photo()
        self.expire()
        self.meals.reset_mock()
        r = self.anon.get(f"{self.url}/data")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"school": self.school.name, "date": "20261103", "date_label": "11월 3일 화요일", "quiet": True})
        self.assertEqual(self.anon.get(f"{self.url}/photo/20261103").status_code, 404)
        self.meals.assert_not_called()
        self.assertEqual(self.anon.get(self.url).status_code, 200)
        self.assertNotIn("플랜", self.anon.get(self.url).content.decode())

    def test_plan_off_is_quiet_too(self):
        School.objects.filter(pk=self.school.pk).update(plan=School.Plan.FREE)
        self.assertTrue(self.data()["quiet"])


class ReissueTests(TvBase):
    def post(self, client):
        return client.post(f"{self.base}/tv/reissue")

    def token(self):
        self.row.refresh_from_db()
        return self.row.tv_token

    def test_writer_and_manager_may_teacher_and_stranger_may_not(self):
        t0 = self.token()
        self.assertEqual(self.post(self.teacher).status_code, 403)
        self.assertEqual(self.post(self.anon).status_code, 302)
        self.assertEqual(self.token(), t0)
        self.assertEqual(self.post(self.writer).status_code, 200)
        t1 = self.token()
        self.assertNotEqual(t1, t0)
        self.assertEqual(self.post(self.manager).status_code, 200)
        self.assertNotEqual(self.token(), t1)

    def test_get_does_not_reissue(self):
        t0 = self.token()
        self.assertEqual(self.writer.get(f"{self.base}/tv/reissue").status_code, 405)
        self.assertEqual(self.token(), t0)

    def test_expired_school_can_still_reissue(self):
        self.expire()
        t0 = self.token()
        self.assertEqual(self.post(self.manager).status_code, 200)
        self.assertNotEqual(self.token(), t0)

    def test_reissue_keeps_screen_settings(self):
        MealSettings.objects.in_space(self.hub).update(screens=[{"key": "week", "on": False}])
        self.post(self.writer)
        self.row.refresh_from_db()
        self.assertEqual(self.row.screens, [{"key": "week", "on": False}])

    def test_other_school_writer_cannot_reach(self):
        far_client = Client()
        far_client.force_login(self.far_user)
        self.assertEqual(far_client.post(f"{self.base}/tv/reissue").status_code, 404)


class InputPageTests(TvBase):
    def page(self, client=None):
        return (client or self.writer).get(f"{self.base}/screens").content.decode()

    def test_writer_sees_address_qr_and_guide(self):
        html = self.page()
        self.assertIn(f"/tv/{self.row.tv_token}", html)
        self.assertIn("data:image/svg+xml", html)
        self.assertIn('data-copy="#tvUrl"', html)
        self.assertIn("화면 보호기", html)
        self.assertIn("전체 화면", html)
        self.assertIn("/meals/tv/reissue", html)
        self.assertIn("담당이 바뀌면 주소를 새로 만들어", html)

    def test_photo_page_says_delete_can_linger_on_tv(self):
        self.assertIn("사진을 지워도 TV에는 최대 5분 남을 수 있어요", self.writer.get(self.base).content.decode())

    def test_first_visit_makes_the_settings_row(self):
        MealSettings.objects.in_space(self.hub).delete()
        html = self.page(self.manager)
        row = MealSettings.objects.in_space(self.hub).get()
        self.assertIn(row.tv_token, html)

    def test_teacher_cannot_see_page(self):
        self.assertEqual(self.teacher.get(f"{self.base}/screens").status_code, 403)

    def test_expired_notice_for_staff_only(self):
        self.assertNotIn("학교 이름과 날짜만", self.page())
        self.expire()
        self.assertIn("학교 이름과 날짜만", self.page())

    def test_other_pages_do_not_expose_token(self):
        for path in (f"/s/{self.hub.token}/today", f"/s/{self.hub.token}/school", self.base):
            self.assertNotIn(self.row.tv_token, self.teacher.get(path).content.decode())


class ContentTests(SimpleTestCase):
    def test_groups_by_dish_name(self):
        self.assertEqual(content.groups_of("쌀밥"), ["grain"])
        self.assertIn("protein", content.groups_of("돈육김치찌개"))
        self.assertIn("veg", content.groups_of("돈육김치찌개"))
        self.assertEqual(content.groups_of("귤"), ["fruit"])
        self.assertEqual(content.groups_of("우유"), ["milk"])
        self.assertNotIn("grain", content.groups_of("떡갈비"))
        self.assertNotIn("protein", content.groups_of("콩나물국"))
        for name in ("카레라이스", "오므라이스", "우동", "수제비", "샌드위치"):
            self.assertIn("grain", content.groups_of(name), name)


class SecondsTests(SimpleTestCase):
    def test_week_and_origin_are_twelve_seconds_a_page(self):
        by_key = {s["key"]: s for s in content.screens([])}
        self.assertEqual((by_key["week"]["sec"], by_key["origin"]["sec"]), (12, 12))


class CacheTests(SimpleTestCase):
    def test_with_redis_neis_cache_stays_in_process_and_ratelimit_has_timeouts(self):
        """큐와 96MB noeviction Redis를 나눠 쓰지 않는다."""
        out = subprocess.run([sys.executable, "-c", "import json, config.settings as s; print(json.dumps(s.CACHES))"], capture_output=True, text=True, check=True,
                             cwd=STATIC.parents[1] / "app", env={**os.environ, "SECRET_KEY": "x", "REDIS_URL": "redis://redis:6379/0"})
        got = json.loads(out.stdout)
        self.assertIn("locmem", got["default"]["BACKEND"])
        self.assertEqual(got["ratelimit"]["LOCATION"], "redis://redis:6379/1")
        self.assertEqual(got["ratelimit"]["OPTIONS"], {"socket_connect_timeout": 1, "socket_timeout": 1})

    def test_meal_failure_outlasts_tv_polling_and_retry_forgets_it(self):
        self.assertGreater(neis.MEAL_FAIL_TTL, 5 * 60)
        cache.clear()
        self.addCleanup(cache.clear)
        with mock.patch("spaces.neis.urlopen", side_effect=OSError) as down:
            neis.meals("A", "B", "20261102", "20261106")
            neis.meals("A", "B", "20261102", "20261106")
            self.assertEqual(down.call_count, 1)
            neis.forget_failed_meals("A", "B", "20261102", "20261106")
            neis.meals("A", "B", "20261102", "20261106")
            self.assertEqual(down.call_count, 2)

    def test_neis_keys_differ_by_school_and_range(self):
        a = neis._cache_key("mealServiceDietInfo", neis._meal_params("A", "1", "20261102", "20261106"))
        self.assertNotEqual(a, neis._cache_key("mealServiceDietInfo", neis._meal_params("A", "2", "20261102", "20261106")))
        self.assertNotEqual(a, neis._cache_key("mealServiceDietInfo", neis._meal_params("A", "1", "20261103", "20270102")))


class FrameTests(TvBase):
    """Q7: /tv/<token> 문서만 같은 출처 iframe 허용. 나머지는 그대로 none."""

    def test_tv_page_allows_same_origin_frame_only(self):
        r = self.anon.get(self.url)
        self.assertEqual(r["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", r["Content-Security-Policy"])
        self.assertNotIn("frame-ancestors 'none'", r["Content-Security-Policy"])
        self.assertNotIn("frame-ancestors *", r["Content-Security-Policy"])

    def test_everything_else_stays_none(self):
        data = self.anon.get(f"{self.url}/data")
        self.assertEqual(data["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", data["Content-Security-Policy"])
        for url in ("/", "/start", f"{self.base}/screens", "/tv/없는토큰"):
            r = self.writer.get(url)
            self.assertEqual(r["X-Frame-Options"], "DENY", url)
            self.assertIn("frame-ancestors 'none'", r["Content-Security-Policy"], url)

    def test_button_above_tv_card_and_script_loaded(self):
        html = self.writer.get(f"{self.base}/screens").content.decode()
        self.assertLess(html.index("data-tv-open"), html.index("<b>← →</b> 키"))
        self.assertLess(html.index("<b>← →</b> 키"), html.index("<h2>TV 주소</h2>"))
        self.assertIn(f'href="{self.url}"', html)
        self.assertIn("js/meals.js", html)
        self.assertIn("<b>F</b> 키", html)


class ThemeTests(TvBase):
    """TV 테마: 설정은 쓰기 권한만, 1~5만, TV 데이터에 실린다. 그림·글꼴 파일은 저장소에 있다(외부 주소 없음)."""
    def post(self, client, no):
        return client.post(f"{self.base}/screens", {"theme": no})

    def theme(self):
        self.row.refresh_from_db()
        return self.row.theme

    def test_default_is_one_and_data_carries_it(self):
        self.assertEqual(self.data()["theme"], 1)
        self.assertEqual(self.post(self.writer, "4").status_code, 200)
        self.assertEqual(self.data()["theme"], 4)

    def test_only_writer_or_manager_and_only_known_numbers(self):
        self.assertEqual(self.post(self.teacher, "3").status_code, 403)
        self.assertEqual(self.post(self.anon, "3").status_code, 302)
        for bad in ("0", "6", "x", "", "-1"):
            self.assertEqual(self.post(self.writer, bad).status_code, 400, bad)
        self.assertEqual(self.theme(), 1)
        self.assertEqual(self.post(self.manager, "5").status_code, 200)
        self.assertEqual(self.theme(), 5)

    def test_theme_keeps_screens_and_token(self):
        MealSettings.objects.in_space(self.hub).update(screens=[{"key": "week", "on": False}])
        t0 = self.row.tv_token
        self.post(self.writer, "2")
        self.row.refresh_from_db()
        self.assertEqual((self.row.screens, self.row.tv_token), ([{"key": "week", "on": False}], t0))

    def test_page_lists_themes_with_preview_links(self):
        html = self.writer.get(f"{self.base}/screens").content.decode()
        for t in content.THEMES:
            self.assertIn(t["name"], html)
            self.assertIn(f"/tv/{self.row.tv_token}?theme={t['no']}", html)

    def test_hygiene_key_for_pictures(self):
        self.assertEqual(self.data()["hygiene"]["key"], content.hygiene_of(TUE)["key"])

    def test_theme_assets_are_local(self):
        css = (STATIC / "css" / "tv-themes.css").read_text()
        self.assertNotIn("http", css.replace("http://www.w3.org", ""))
        import re
        for ref in re.findall(r'url\("\.\./([^"]+)"\)', css):
            self.assertTrue((STATIC / ref).exists(), ref)
        for no in content.THEME_NOS:
            self.assertTrue((STATIC / "tv" / "thumb" / f"{no}.jpg").exists())
