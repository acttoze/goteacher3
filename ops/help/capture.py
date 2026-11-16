"""V7 도움말 캡처를 한 번에 전부 다시 찍는다 → web/static/help/*.png

실행(저장소 루트에서, 플레이라이트가 깔린 파이썬으로):
    uv sync                      # 앱 가상환경(.venv)이 없으면 한 번
    python3 ops/help/capture.py  # pip install playwright 필요. 브라우저는 플레이라이트 크로뮴, 없으면 설치된 크롬

하는 일:
1. 임시 폴더에 새 sqlite DB를 만들고 migrate → manage.py seed_help_demo(가상 '고티처초등학교', DEBUG=1)
2. ops/help/serve.py로 로컬 서버(가짜 급식 메뉴)를 띄운다. 개발 DB(app/db.sqlite3)와 media는 건드리지 않는다
3. 아래 SCENES를 차례로 돌며 1536×750 화면을 찍고, 색 수를 줄여 PNG로 저장한다
4. 서버를 끄고 임시 폴더를 지운다

V8 영상은 같은 SCENES를 다시 쓴다: Stage를 상속해 shot()에서 자막·멈춤을 넣고 녹화하면 된다(단계 = login·go·click·fill·shot).
"""
import io
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP = os.path.join(ROOT, "app")
OUT = os.path.join(ROOT, "web", "static", "help")
PY = os.path.join(ROOT, ".venv", "bin", "python")
VIEW = {"width": 1536, "height": 750}
PASSWORD = "help-demo-2026"  # seed_help_demo.PASSWORD
FORM_NAME = "교실_환경_점검표.hwpx"
LOGIN = {"admin": "haneul_kim", "41": "gyeoul_jung", "meal": "dasom_seo"}  # seed_help_demo.LOGIN과 같게
SITE_URL = "https://goteacher.xyz"  # 화면에 찍히는 링크 주소(실제 접속은 127.0.0.1)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def save_png(raw, path):
    """색 256개로 줄여 저장(글자는 그대로 읽힌다). Pillow가 없으면 그대로."""
    try:
        from PIL import Image
    except ImportError:
        with open(path, "wb") as f:
            f.write(raw)
        return
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)


class Stage:
    """시나리오 단계. 한 사람(브라우저 컨텍스트) 하나, 페이지 하나."""

    def __init__(self, browser, base, ctx):
        self.browser, self.base, self.ctx = browser, base, ctx
        self.page = browser.new_page(viewport=VIEW, locale="ko-KR")

    def close(self):
        self.page.context.close()

    def login(self, who):
        self.go("/start")
        self.page.fill("input[name=username]", LOGIN[who])
        self.page.fill("input[name=password]", PASSWORD)
        self.page.click("form[action='/auth/login'] button[type=submit]:has-text('로그인')")
        self.page.wait_for_url("**/s/**")
        self.ctx["space"] = self.page.url.split("/s/")[1].split("/")[0]

    def go(self, path):
        """'/start' 같은 주소, 또는 공간 안 주소('today')."""
        url = self.base + path if path.startswith("/") else f"{self.base}/s/{self.ctx['space']}/{path}"
        self.page.goto(url)
        self.page.wait_for_load_state("networkidle")

    def click(self, selector):
        self.page.click(selector)
        self.page.wait_for_load_state("networkidle")

    def fill(self, selector, value):
        self.page.fill(selector, value)

    def shot(self, name, target=None, pad=12, height=None):
        """target이 없으면 지금 보이는 화면 전체(1536×750). 있으면 그 요소 둘레만 잘라 강조한다(높이는 height, 없으면 750까지)."""
        self.page.wait_for_timeout(400)  # 글꼴·htmx 마무리
        if not target:
            raw = self.page.screenshot()
        else:
            el = self.page.locator(target).first
            el.scroll_into_view_if_needed()
            box = el.bounding_box()
            top = self.page.evaluate("window.scrollY")
            x, y = max(box["x"] - pad, 0), max(box["y"] + top - pad, 0)
            w = min(box["width"] + 2 * pad, VIEW["width"] - x)
            h = min(box["height"] + 2 * pad, height or VIEW["height"])
            raw = self.page.screenshot(full_page=True, clip={"x": x, "y": y, "width": w, "height": h})
        save_png(raw, os.path.join(OUT, name + ".png"))
        print("  찍음", name)


# ---------- 시나리오(도움말 주제 순서). 이름은 help 화면(library/help.py TOPICS)의 그림 이름과 같다 ----------

def scene_start(st):
    st.go("/start")
    st.shot("start-1", "main", height=470)


def scene_today(st):
    st.login("admin")
    st.go("today")
    st.shot("today-1")


def scene_schedule(st):
    st.login("admin")
    st.go("schedule")
    st.shot("schedule-1")
    st.click(".sched-tabs a:has-text('개인')")
    st.shot("schedule-2", "main", pad=0, height=600)


def scene_collect_new(st):
    st.login("admin")
    st.go("c/new")
    st.fill("input[name=title]", "교실 환경 점검표")
    st.click("button:has-text('한글 양식 올리기')")
    st.page.set_input_files("#hwpFile", st.ctx["form"])
    st.page.locator("#hwpBox button:has-text('추천')").first.click()
    st.page.wait_for_selector("#hwpFields table")
    st.page.wait_for_timeout(1500)  # 쪽 미리보기(rhwp)
    st.page.locator("#hwpBox").scroll_into_view_if_needed()
    st.shot("collect_new-1", "[data-pane=hwp]", height=620)
    st.click("button:has-text('만들고 링크 받기')")
    st.page.wait_for_selector("#expH")
    with sqlite3.connect(st.ctx["db"]) as db:  # 받는 링크(화면의 복사 칸과 같은 값)
        st.ctx["link"] = db.execute("SELECT link_token FROM collect_collection WHERE title = ?", ("교실 환경 점검표",)).fetchone()[0]
    st.shot("collect_new-2", "section:has(h2:text-is('받는 링크'))")


def scene_collect_send(st):
    st.go("/c/" + st.ctx["link"])
    st.shot("collect_send-1")
    st.fill("#findQ", "정겨울")
    st.click("button:has-text('찾기')")
    st.fill("input[name=f0]", "24")
    st.fill("input[name=f1]", "뒤쪽 블라인드")
    st.shot("collect_send-2", "#f")
    st.click("button:has-text('제출하기')")
    st.shot("collect_send-3")
    for name, a, b in [("이봄", "20", ""), ("최가을", "22", "프로젝터 전원")]:  # 합본에 값이 보이게 몇 분 더
        st.go(f"/c/{st.ctx['link']}?find=1")
        st.fill("#findQ", name)
        st.click("button:has-text('찾기')")
        st.fill("input[name=f0]", a)
        st.fill("input[name=f1]", b)
        st.click("button:has-text('제출하기')")


def scene_hwp(st):
    st.login("admin")
    st.go("c/")
    st.click("a:has-text('교실 환경 점검표')")
    st.shot("hwp-1", "section:has(h2:text-is('합본'))", height=560)
    st.click("button:has-text('한글 파일로 받기')")
    st.page.wait_for_selector("button:has-text('다시 만들기')", timeout=60000)
    st.page.wait_for_timeout(2000)  # 채운 쪽 미리보기
    st.shot("hwp-2", "#expH >> xpath=ancestor::section[1]")


def scene_board(st):
    st.login("41")
    st.go("board")
    st.shot("board-1")
    st.click("a:has-text('학부모 공개수업 안내')")
    st.page.wait_for_timeout(2500)  # 2초 보면 '봄'으로 친다
    st.shot("board-2")


def scene_meals(st):
    st.login("meal")
    st.go("meals")
    st.shot("meals-1")


def scene_settings(st):
    st.login("41")
    st.go("settings?a=me")
    st.shot("settings-1", "#pwArea")
    st.go("settings?a=alerts")
    st.shot("settings-2", "main", pad=0, height=520)


def scene_feedback(st):
    st.login("41")
    st.go("today")
    st.click("button[data-open='#fbDlg']")
    st.shot("feedback-1")


SCENES = [scene_start, scene_today, scene_schedule, scene_collect_new, scene_collect_send, scene_hwp, scene_board,
          scene_meals, scene_settings, scene_feedback]


def launch(p):
    try:
        return p.chromium.launch()
    except Exception:  # 플레이라이트 크로뮴이 없으면 설치된 크롬
        return p.chromium.launch(channel="chrome")


def main(stage=Stage, scenes=SCENES):
    """V8 record.py는 영상용 단계(stage)와 시나리오(scenes)를 넘긴다."""
    tmp = tempfile.mkdtemp(prefix="gt_help_")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "SECRET_KEY": "help-demo-local-only", "DEBUG": "1", "SITE_URL": SITE_URL,
           "DATABASE_URL": f"sqlite:///{tmp}/db.sqlite3", "MEDIA_ROOT": os.path.join(tmp, "media"),
           "REDIS_URL": "", "LEGACY_READONLY": "", "VAPID_PUBLIC_KEY": "", "VAPID_PRIVATE_KEY": ""}
    form = os.path.join(tmp, FORM_NAME)
    for cmd in (["manage.py", "migrate", "-v0"], ["manage.py", "seed_help_demo"], [os.path.join(ROOT, "ops", "help", "demo_form.py"), form]):
        subprocess.run([PY, *cmd], cwd=APP, env=env, check=True)
    log = open(os.path.join(tmp, "server.log"), "w")
    server = subprocess.Popen([PY, os.path.join(ROOT, "ops", "help", "serve.py"), f"127.0.0.1:{port}"], cwd=APP, env=env, stdout=log, stderr=log)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        os.makedirs(OUT, exist_ok=True)
        ctx = {"form": form, "db": os.path.join(tmp, "db.sqlite3")}
        with sync_playwright() as p:
            browser = launch(p)
            for scene in scenes:
                print(scene.__name__)
                st = stage(browser, base, ctx)
                try:
                    scene(st)
                finally:
                    st.close()
            browser.close()
    except Exception:
        log.flush()
        print(open(os.path.join(tmp, "server.log")).read()[-3000:], file=sys.stderr)
        raise
    finally:
        server.terminate()
        server.wait(10)
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)
    print("저장:", OUT)


if __name__ == "__main__":
    main()
