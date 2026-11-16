"""V8 도움말 영상을 한 번에 전부 다시 녹화한다 → web/static/help/<주제>.mp4 + <주제>.jpg(포스터)

실행(저장소 루트에서, capture.py와 같은 준비 + ffmpeg):
    python3 ops/help/record.py

capture.py의 장치를 그대로 쓴다(임시 sqlite + migrate + seed_help_demo, serve.py 로컬 서버, 가상 '고티처초등학교').
- 녹화 화면은 1280×720: 도움말 카드 안 재생 폭(약 880px)에서 1536보다 글씨가 덜 줄어든다. 1100px 넘으니 PC 배치 그대로.
- 헤드리스 녹화엔 마우스가 안 나오므로 페이지에 가짜 커서·클릭 표시를 넣고, 자막도 화면 아래 한 줄로 페이지에 넣는다.
- 로그인은 녹화 밖에서 하고(처음 들어오기만 화면에서), 녹화한 webm을 H.264 mp4(소리 없음)로 바꾸고 첫 장면을 포스터로 뽑는다.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402

VIEW = {"width": 1280, "height": 720}
PACE = 1.4  # 자막 머무는 시간 배율(따라 보기 쉽게)

OVERLAY = """
(() => {
  const S = window.sessionStorage;
  function mount() {
    if (document.getElementById('gtRecSub')) return;
    const st = document.createElement('style');
    st.textContent = `
      #gtRecSub { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); max-width: 1180px;
        padding: 12px 28px; border-radius: 12px; background: var(--ink, #0d0d0d); color: #fff;
        font: 600 26px/1.4 var(--font, sans-serif); white-space: nowrap; z-index: 2147483647; pointer-events: none;
        box-shadow: 0 8px 24px rgba(0, 0, 0, .25); }
      #gtRecSub:empty { display: none; }
      #gtRecCur { position: fixed; left: 0; top: 0; width: 30px; height: 30px; z-index: 2147483647; pointer-events: none;
        transition: transform .8s cubic-bezier(.4, 0, .2, 1); filter: drop-shadow(0 2px 3px rgba(0, 0, 0, .35)); }
      .gtRecDot { position: fixed; width: 48px; height: 48px; margin: -24px 0 0 -24px; border-radius: 50%;
        border: 4px solid var(--accent, #2251ff); z-index: 2147483646; pointer-events: none; animation: gtRecDot .7s ease-out forwards; }
      .toast { bottom: 104px !important; } /* 앱 알림은 자막 위로 */
      @keyframes gtRecDot { from { transform: scale(.3); opacity: 1; } to { transform: scale(1.3); opacity: 0; } }`;
    document.head.appendChild(st);
    const sub = document.createElement('div');
    sub.id = 'gtRecSub';
    sub.textContent = S.getItem('gtSub') || '';
    const cur = document.createElement('div');
    cur.id = 'gtRecCur';
    cur.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24"><path d="M4 2l15 11.5-6.4.8 3.7 7.4-2.9 1.4-3.7-7.5L4 20z" fill="#0d0d0d" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>';
    const [x, y] = (S.getItem('gtCur') || '640,360').split(',');
    cur.style.transition = 'none';
    cur.style.transform = `translate(${x}px, ${y}px)`;
    document.body.append(sub, cur);
    requestAnimationFrame(() => { cur.style.transition = ''; });
  }
  window.gtRec = {
    sub(t) { S.setItem('gtSub', t); mount(); document.getElementById('gtRecSub').textContent = t; },
    move(x, y) { S.setItem('gtCur', x + ',' + y); mount(); document.getElementById('gtRecCur').style.transform = `translate(${x}px, ${y}px)`; },
    dot(x, y) { const d = document.createElement('div'); d.className = 'gtRecDot'; d.style.left = x + 'px'; d.style.top = y + 'px';
      document.body.appendChild(d); setTimeout(() => d.remove(), 800); },
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
"""


class VideoStage(capture.Stage):
    """한 영상 = 한 단계 객체. login()은 녹화 밖, open()부터 녹화한다. close()에서 mp4·포스터를 만든다."""

    def __init__(self, browser, base, ctx):
        self.browser, self.base, self.ctx = browser, base, ctx
        self.page = self.state = self.name = self.skip = None

    def login(self, who):
        self.page = self.browser.new_page(viewport=VIEW, locale="ko-KR")
        super().login(who)
        self.state = self.page.context.storage_state()
        self.page.context.close()
        self.page = None

    def open(self, name, path):
        """녹화를 시작하고 path로 간다. 빈 화면 시간은 나중에 잘라 낸다."""
        self.name, self.dir = name, tempfile.mkdtemp(prefix="gt_rec_")
        context = self.browser.new_context(viewport=VIEW, locale="ko-KR", storage_state=self.state,
                                           record_video_dir=self.dir, record_video_size=VIEW)
        context.add_init_script(OVERLAY)
        t0 = time.monotonic()
        self.page = context.new_page()
        self.go(path)
        self.page.wait_for_timeout(300)
        self.skip = time.monotonic() - t0

    def close(self):
        if not self.page:
            return
        if self.skip is None:  # 녹화 시작 전에 멈췄다
            self.page.context.close()
            return
        self.page.wait_for_timeout(1500)  # 마지막 자막을 읽을 시간
        video = self.page.video.path()
        self.page.context.close()
        mp4 = os.path.join(capture.OUT, self.name + ".mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{self.skip:.2f}", "-i", video, "-an",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p", "-r", "25",
                        "-movflags", "+faststart", mp4], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.2", "-i", mp4, "-frames:v", "1", "-q:v", "4",
                        os.path.join(capture.OUT, self.name + ".jpg")], check=True)
        shutil.rmtree(self.dir, ignore_errors=True)
        print("  녹화", self.name)

    # ---- 사람처럼 보이는 동작 ----
    def say(self, text, hold=None):
        """자막 한 줄을 띄우고 읽을 시간만큼 기다린다."""
        self.page.evaluate("t => gtRec.sub(t)", text)
        self.page.wait_for_timeout(PACE * (hold if hold is not None else max(2200, 110 * len(text))))

    def point(self, selector):
        el = self.page.locator(selector).first
        el.scroll_into_view_if_needed()
        self.page.wait_for_timeout(300)
        box = el.bounding_box()
        x, y = round(box["x"] + box["width"] / 2), round(box["y"] + box["height"] / 2)
        self.page.evaluate("([x, y]) => gtRec.move(x, y)", [x, y])
        self.page.wait_for_timeout(1200)
        return el, x, y

    def click(self, selector):
        el, x, y = self.point(selector)
        self.page.evaluate("([x, y]) => gtRec.dot(x, y)", [x, y])
        self.page.wait_for_timeout(250)
        el.click()
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(700)

    def type(self, selector, text):
        self.click(selector)
        self.page.locator(selector).first.press_sequentially(text, delay=110)
        self.page.wait_for_timeout(500)

    def scroll(self, selector):
        self.page.locator(selector).first.evaluate("el => el.scrollIntoView({behavior: 'smooth', block: 'center'})")
        self.page.wait_for_timeout(1200)


def offcam_submit(st, name, a, b):
    """합본에 값이 보이게 몇 분 더 낸다(녹화 밖)."""
    p = st.browser.new_page(viewport=VIEW, locale="ko-KR")
    p.goto(f"{st.base}/c/{st.ctx['link']}?find=1")
    p.fill("#findQ", name)
    p.click("button:has-text('찾기')")
    p.wait_for_load_state("networkidle")
    p.fill("input[name=f0]", a)
    p.fill("input[name=f1]", b)
    p.click("button:has-text('제출하기')")
    p.wait_for_load_state("networkidle")
    p.context.close()


# ---------- 영상(도움말 주제 순서). 자막은 help.py TOPICS의 단계 문장을 짧게 나눈 것 ----------

def video_start(st):
    st.open("start", "/start")
    st.say("처음 들어오기", 1800)
    st.say("예전 고티처에서 '새 고티처 이용해 보기'를 누르면 바로 들어와요", 4000)
    st.say("따로 들어올 때는 아이디와 비밀번호를 써요", 1200)
    st.type("input[name=username]", capture.LOGIN["41"])
    st.type("input[name=password]", capture.PASSWORD)
    st.say("내 PC라면 '이 PC 기억하기'를 켜 두세요", 1200)
    st.click("label.chk:has(input[name=remember])")
    st.say("1년 동안 다시 묻지 않아요", 2000)
    st.say("'로그인'을 누르면 시작해요", 1000)
    st.click("form[action='/auth/login'] button[type=submit]:has-text('로그인')")
    st.say("오늘 화면이 열렸어요", 2500)


def video_today(st):
    st.login("admin")
    st.open("today", "c/")
    st.say("오늘 화면 보기", 1800)
    st.say("왼쪽 메뉴에서 '오늘'을 눌러요", 1000)
    st.click(".side a:has-text('오늘')")
    st.say("오늘 일정·복무·점심과 내가 낼 것이 한 화면에 모여 있어요", 4500)
    st.say("위 칸의 숫자를 누르면 바뀐 것·낼 것·걷는 것을 바로 봐요", 1500)
    for label in ("바뀐 것", "낼 것", "걷는 것"):
        st.point(f"main :text('{label}')")
        st.page.wait_for_timeout(600)
    st.say("아래로 내리면 오늘 일과표가 이어져요", 1200)
    st.page.evaluate("window.scrollBy({top: 400, behavior: 'smooth'})")
    st.page.wait_for_timeout(3500)


def video_schedule(st):
    st.login("admin")
    st.open("schedule", "today")
    st.say("일정 보기", 1800)
    st.say("왼쪽 메뉴에서 '일정'을 눌러요", 1000)
    st.click(".side a:has-text('일정')")
    st.say("이번 주 학교 일정이 보여요", 3000)
    st.say("위 탭으로 학교·우리 소속·개인 일정을 바꿔 봐요", 1000)
    st.click(".sched-tabs a:has-text('개인')")
    st.say("개인 일정은 나만 봐요", 2500)
    st.say("빈칸의 +를 누르고 제목을 쓴 뒤 Enter를 눌러요", 1000)
    st.click("[data-cell].tcol, .tcol[data-cell]")
    st.page.keyboard.type("학년 협의 자료 준비", delay=110)
    st.page.wait_for_timeout(400)
    st.page.keyboard.press("Enter")
    st.page.wait_for_load_state("networkidle")
    st.say("일정이 들어갔어요", 2500)


def video_collect_new(st):
    st.login("admin")
    st.open("collect_new", "c/")
    st.say("취합 만들기", 1800)
    st.say("'새 취합'을 누르고 제목을 써요", 1000)
    st.click("main a:has-text('새 취합')")
    st.type("input[name=title]", "교실 환경 점검표")
    st.say("'한글 양식 올리기'에서 hwp 파일을 올려요", 1000)
    st.click("button:has-text('한글 양식 올리기')")
    st.page.set_input_files("#hwpFile", st.ctx["form"])
    st.page.wait_for_selector("#hwpBox button:has-text('추천')")
    st.say("채울 표를 한 번 눌러요", 1000)
    st.click("#hwpBox button:has-text('추천')")
    st.page.wait_for_selector("#hwpFields table")
    st.scroll("#hwpFields")
    st.say("표의 칸이 받을 항목이 돼요", 3000)
    st.say("'만들고 링크 받기'를 눌러요", 1000)
    st.click("button:has-text('만들고 링크 받기')")
    st.page.wait_for_selector("#expH")
    with capture.sqlite3.connect(st.ctx["db"]) as db:
        st.ctx["link"] = db.execute("SELECT link_token FROM collect_collection WHERE title = ?", ("교실 환경 점검표",)).fetchone()[0]
    st.scroll("section:has(h2:text-is('받는 링크'))")
    st.say("링크나 쪽지 문구를 복사해 메신저로 보내요", 1000)
    st.point("section:has(h2:text-is('받는 링크')) button")
    st.page.wait_for_timeout(2500)


def video_collect_send(st):
    st.open("collect_send", "/c/" + st.ctx["link"])
    st.say("취합 내기(받는 선생님)", 1800)
    st.say("메신저로 받은 링크를 열어요. 로그인은 없어요", 3500)
    st.say("내 이름을 쓰고 '찾기'를 눌러요", 1000)
    st.type("#findQ", "정겨울")
    st.click("button:has-text('찾기')")
    st.say("내 칸을 채워요", 1000)
    st.type("input[name=f0]", "24")
    st.type("input[name=f1]", "뒤쪽 블라인드")
    st.say("'제출하기'를 누르면 끝이에요", 1000)
    st.click("button:has-text('제출하기')")
    st.say("바쁜 분 것은 그분 이름으로 대신 내도 돼요", 3500)
    for name, a, b in [("이봄", "20", ""), ("최가을", "22", "프로젝터 전원")]:
        offcam_submit(st, name, a, b)


def video_hwp(st):
    st.login("admin")
    st.open("hwp", "c/")
    st.say("한글로 받기", 1800)
    st.click("main a:has-text('교실 환경 점검표')")
    st.say("모인 값을 실시간으로 봐요", 3000)
    st.say("안 낸 분 이름을 누르면 독촉 문구가 복사돼요", 3500)
    st.scroll("#expH")
    st.say("'한글 파일로 받기'를 눌러요", 1000)
    st.click("button:has-text('한글 파일로 받기')")
    st.page.wait_for_selector("button:has-text('다시 만들기')", timeout=60000)
    st.page.wait_for_timeout(1500)
    st.scroll("a[href$='/hwpx']")
    st.say("양식의 표 칸만 채운 한글 파일을 내려받아요", 1000)
    st.point("a[href$='/hwpx']")
    st.page.wait_for_timeout(3000)


def video_board(st):
    st.login("41")
    st.open("board", "today")
    st.say("게시판(공지 확인)", 1800)
    st.say("왼쪽 메뉴에서 '게시판'을 눌러요", 1000)
    st.click(".side a:has-text('게시판')")
    st.say("공지를 눌러 읽어요", 1000)
    st.click("a:has-text('학부모 공개수업 안내')")
    st.page.wait_for_timeout(1500)
    st.say("'확인 필수' 공지는 다 읽고 '확인했어요'를 눌러요", 1000)
    st.click("button:has-text('확인했어요')")
    st.say("확인한 것이 쓴 분께 바로 보여요", 3000)


def video_meals(st):
    st.login("meal")
    st.open("meals", "today")
    st.say("급식", 1800)
    st.say("급식 담당과 관리자에게 왼쪽 메뉴 '급식'이 보여요", 1000)
    st.click(".side a:has-text('급식')")
    st.say("'이번 주 사진' 탭을 눌러요", 1000)
    st.click("nav.mtabs a:has-text('이번 주 사진')")
    st.say("메뉴는 나이스에서 저절로 와요", 3000)
    st.say("매일 할 일은 식판 사진 한 장이에요", 2500)
    st.say("오늘 칸의 '사진 고르기'로 올려요", 1000)
    st.point("label:has-text('사진 고르기')")
    st.page.wait_for_timeout(1500)
    st.say("'폰으로 올리기'를 누르면 폰으로 찍어 올려요", 1000)
    st.point("#mealQr button")
    st.page.wait_for_timeout(2500)


def video_settings(st):
    st.login("41")
    st.open("settings", "today")
    st.say("설정(비밀번호·알림)", 1800)
    st.say("왼쪽 아래 '설정'을 눌러요", 1000)
    st.click(".side a:has-text('설정')")
    st.say("'내 정보' 탭에서 비밀번호를 바꿔요", 1000)
    st.click("nav.tabs a:has-text('내 정보')")
    st.scroll("#pwArea")
    st.point("#pwArea input[type=password]")
    st.page.wait_for_timeout(2000)
    st.say("'알림' 탭에서 알림 묶음 시간을 켜고 꺼요", 1000)
    st.click("nav.tabs a:has-text('알림')")
    st.page.wait_for_timeout(3000)


def video_feedback(st):
    st.login("41")
    st.open("feedback", "today")
    st.say("의견 보내기", 1800)
    st.say("화면 위 '의견 보내기'를 눌러요", 1000)
    st.click("button[data-open='#fbDlg']")
    st.say("불편한 점이나 바라는 점을 한 줄 써요", 1000)
    st.click("#fbDlg label.pick:has-text('바라는 점')")
    st.type("#fbDlg textarea[name=text]", "주간 일정을 인쇄할 때 글씨가 더 컸으면 좋겠어요")
    st.say("'보내기'를 누르면 고티처 팀에 바로 가요", 1000)
    st.click("#fbDlg button:has-text('보내기')")
    st.page.wait_for_timeout(2500)


VIDEOS = [video_start, video_today, video_schedule, video_collect_new, video_collect_send, video_hwp, video_board,
          video_meals, video_settings, video_feedback]

if __name__ == "__main__":
    capture.main(VideoStage, VIDEOS)
