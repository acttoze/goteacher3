"""Q4 공지 쓰기 대화상자: 목록 위에서 쓰고, 오류면 열린 채 입력값 유지. 고치기는 화면 유지."""
import re

from . import views
from .models import Notice
from .tests import Base, up


class ModalTests(Base):
    def test_list_has_closed_dialog_and_new_opens_it(self):
        html = self.client.get(self.base).content.decode()
        self.assertRegex(html, r'id="noticeDlg" hidden')
        self.assertIn('data-open="#noticeDlg"', html)
        self.assertIn('role="dialog"', html)
        self.assertNotIn(f'href="{self.base}/new"', html)
        html = self.client.get(f"{self.base}/new").content.decode()
        self.assertIn('id="noticeDlg"', html)
        self.assertNotRegex(html, r'id="noticeDlg" hidden')

    def test_success_returns_to_list_with_toast(self):
        res = self.write(title="표식공지")
        self.assertEqual((res.status_code, res["Location"]), (302, self.base))
        page = self.client.get(self.base)
        self.assertContains(page, "표식공지")
        self.assertContains(page, "공지를 올렸어요.")
        self.assertNotContains(self.client.get(self.base), "공지를 올렸어요.")

    def test_error_keeps_dialog_open_with_values(self):
        res = self.write(title="표식제목값", body="표식본문값", attach=[up("가짜.pdf", b"not pdf")])
        self.assertEqual(res.status_code, 400)
        html = res.content.decode()
        self.assertNotRegex(html, r'id="noticeDlg" hidden')
        self.assertIn('value="표식제목값"', html)
        self.assertIn("표식본문값</textarea>", html)
        self.assertRegex(html, r'role="alert"[^>]*>「')
        self.assertFalse(Notice.objects.in_space(self.hub).filter(title="표식제목값").exists())

    def test_too_big_declared_keeps_page(self):
        res = self.client.post(f"{self.base}/new", b"x", content_type="multipart/form-data; boundary=x", CONTENT_LENGTH=views.MAX_REQUEST + 1)
        self.assertEqual(res.status_code, 400)
        self.assertNotRegex(res.content.decode(), r'id="noticeDlg" hidden')

    def test_edit_stays_a_page(self):
        n = self.made()
        html = self.client.get(f"{self.base}/{n.key}/edit").content.decode()
        self.assertNotIn('id="noticeDlg"', html)
        self.assertIn('id="noticeForm"', html)
        self.assertIn('name="base_v"', html)
        self.assertTrue(re.search(r'<h1>공지 고치기</h1>', html))
