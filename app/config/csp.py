"""모든 화면 공통 CSP(G3 2차). 화면이 제 CSP를 이미 붙였으면(한글 미리보기 'wasm-unsafe-eval', 내려받기 sandbox) 그대로 둔다.

- 스크립트는 우리 파일만(인라인·eval 없음). 인라인 style 속성이 많아 style-src만 'unsafe-inline'.
- 글꼴은 Wanted Sans(jsDelivr) 경로 하나만. 구글 로그인은 GET 폼 → /auth/google/login → 구글로 리디렉트라 form-action에 구글.
"""
FONT_SRC = "https://cdn.jsdelivr.net/gh/wanteddev/wanted-sans@v1.0.3/"
CSP = ("default-src 'self'; script-src 'self'; "
       f"style-src 'self' 'unsafe-inline' {FONT_SRC}; font-src 'self' {FONT_SRC}; img-src 'self' data: blob:; "
       "connect-src 'self'; form-action 'self' https://accounts.google.com; "
       "frame-ancestors 'none'; base-uri 'none'; object-src 'none'")


class CspMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        return response
