"""500 오류 로그(G6). Django 기본 설정은 DEBUG=0에서 django.request 오류를 어디에도 남기지 않는다(콘솔은 DEBUG일 때만).

stderr(docker logs)에 남기되 제출 값·개인정보가 들어가지 않게:
- 요청 본문·쿠키·POST 값은 처음부터 형식에 넣지 않는다(%(message)s는 "Internal Server Error: 경로"뿐).
- 경로의 공간·제출·초대 토큰(/s/·/c/·/j/ 다음 한 칸)은 가린다.
- 예외 메시지는 버린다(DB 오류 "Key (email)=(...)"처럼 값이 들어갈 수 있다). 예외 종류와 코드 위치(파일·줄·함수)만.
서버 감시(ops/monitor.sh)가 5분마다 "Internal Server Error" 줄 수를 센다.
"""
import logging
import re
import traceback

_TOKEN = re.compile(r"(/[scj]/)[^/\s]+")


class SafeErrorFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def formatMessage(self, record):
        return _TOKEN.sub(r"\1***", super().formatMessage(record))

    def formatException(self, ei):
        frames = traceback.extract_tb(ei[2])
        where = "\n".join(f"  {f.filename}:{f.lineno} {f.name}" for f in frames[-8:])
        return f"{ei[0].__module__}.{ei[0].__qualname__} (메시지 생략)\n{where}"
