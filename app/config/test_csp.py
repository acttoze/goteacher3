"""G3 2차: 모든 화면 공통 CSP(config/csp.py).

불변식
- 모든 응답(로그인 전·후, 받는 사람, 404)에 공통 CSP. 스크립트는 'self'만(인라인·eval 없음), 글꼴은 wanted-sans 경로만.
- 화면이 제 CSP를 이미 붙였으면(한글 미리보기 'wasm-unsafe-eval', 내려받기 sandbox) 덮어쓰지 않는다.
- 템플릿에 인라인 <script>와 on…= 이벤트 속성이 없다(script-src 'self'가 막지 않게).
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from .csp import CSP, FONT_SRC

TEMPLATE_DIRS = [settings.BASE_DIR / "templates", *settings.BASE_DIR.glob("*/templates")]


class CspTests(TestCase):
    def test_policy_parts(self):
        d = dict(p.strip().split(" ", 1) for p in CSP.split(";") if p.strip())
        self.assertEqual(d["default-src"], "'self'")
        self.assertEqual(d["script-src"], "'self'")
        self.assertEqual(d["img-src"], "'self' data: blob:")
        self.assertEqual(d["connect-src"], "'self'")
        self.assertEqual(d["frame-ancestors"], "'none'")
        self.assertEqual(d["base-uri"], "'none'")
        self.assertEqual(d["object-src"], "'none'")
        self.assertEqual(d["font-src"], f"'self' {FONT_SRC}")
        self.assertIn("wanted-sans", FONT_SRC)
        self.assertNotIn("unsafe-eval", CSP)

    def test_every_screen_gets_it(self):
        for url in ("/", "/start", "/privacy", "/forms", "/c/없는링크", "/없는화면"):
            r = self.client.get(url)
            self.assertEqual(r["Content-Security-Policy"], CSP, url)

    def test_preview_keeps_its_own(self):
        from django.http import HttpResponse

        from .csp import CspMiddleware
        own = HttpResponse()
        own["Content-Security-Policy"] = "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'"
        r = CspMiddleware(lambda req: own)(None)
        self.assertIn("wasm-unsafe-eval", r["Content-Security-Policy"])

    def test_no_inline_script_or_handlers_in_templates(self):
        bad = []
        for d in TEMPLATE_DIRS:
            for f in Path(d).rglob("*.html"):
                text = f.read_text()
                if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text) or re.search(r"\son[a-z]+\s*=", text):
                    bad.append(str(f))
        self.assertEqual(bad, [])
