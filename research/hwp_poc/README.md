# 한글(HWP/HWPX) 취합 POC

티로드 '한글 취합'(양식 표 → 웹 입력 칸 → 여러 명 제출 → 원본 파일 표에 다시 넣기)이 한컴오피스 없이 되는지 실험한 코드입니다. 판정과 수치는 `../04_hwp_feasibility.md`에 있습니다.

## 준비 (macOS/리눅스 공통, 한글 앱·Java 불필요)

```bash
cd research/hwp_poc
python3.13 -m venv venv && ./venv/bin/pip install -r requirements.txt   # python-hwpx는 3.10 이상 필요
npm install                                                              # @rhwp/core (렌더·독립 검증용)
# (선택) pyhwp 교차 검증: python3.9 venv39 에 pyhwp six cryptography lxml olefile
```

## 바로 써 보기

```bash
# 양식 하나로 공정 1~5 전체: 표 후보 → 입력 칸(schema.json) → 3명 제출 흉내 → 병합 → 원본 표에 넣어 저장
./venv/bin/python -I demo.py <양식.hwp|hwpx> out/ [--table N] [--people 3] [--add-rows K]
#   hwp 원본이면 out/result.hwp(HWP 5.0) 와 out/result.hwpx 둘 다 생성
#   --add-rows: 명단형에서 빈 행이 모자랄 때 마지막 행을 복제해 늘림

# 한글 앱 없이 눈으로 확인: rhwp(WASM)로 쪽마다 SVG → Chrome 헤드리스로 PNG
node render.mjs out/result.hwp out/render
sh svg2png.sh out/render/p1.svg out/render/p1.png

# 대안 경로: rhwp로 원본을 직접 편집해 같은 형식으로 저장(HWP→HWP, 변환 없이)
node rhwp_fill.mjs <양식.hwp> out/rhwp.hwp
```

## 파일

| 파일 | 하는 일 |
|---|---|
| `troad_hwp.py` | 핵심 모듈. `load`(hwp는 HWPX로 변환) · `analyze`(표 격자·병합·역할 분류) · `pick_table`(취합용 표 순위) · `schema`(머리글·행 머리로 칸 라벨) · `fill_lib`(python-hwpx 바이트 보존 채우기) · `fill_own`(lxml 직접 조작, 비교용) · `to_hwp`(HWPX→HWP 5.0) · `verify`(값·구조·나머지 파트 불변 검사) |
| `demo.py` | 종단 데모(위 참고) |
| `render.mjs`, `svg2png.sh` | 렌더링(한글 앱 대용 눈 확인) |
| `rhwp_fill.mjs`, `rhwp_check.mjs` | rhwp 직접 편집 경로 / 독립 구현으로 열기·쪽수 검사 |
| `bench_read.py` + `read_one.py` | 아카이브 전체 읽기 성공률(자체 파서·python-hwpx·pyhwp 비교) |
| `bench_fill.py` + `fill_one.py` | 표 고르기→채우기→HWPX/HWP 저장→재검증 일괄 실행 |
| `verify_ext.py` | 결과물을 rhwp·pyhwp·자체 레코드 파서로 독립 재검증 |
| `summarize_*.py`, `placeholder_stats.py`, `review_render.py`, `select.py` | 집계·통계·눈 검토용 묶음 이미지 |
| `examples/` | 개인정보 없는 양식(도서 구입 희망 신청서)으로 만든 결과: 행 4개 추가 + 60칸 채움, `.hwp`/`.hwpx`/렌더 PNG |

벤치 스크립트는 작업 폴더(`HWP_WORK`, 기본은 이 폴더의 상위)에 `samples/all/`(양식 복사본), `venv/`, `venv39/`, `npm/node_modules/@rhwp/core` 가 있다고 가정합니다. 아카이브 원본은 신뢰할 수 없는 입력이므로 복사본만 쓰고, 파이썬은 `-I`로 실행합니다.

## 주의

- 렌더(rhwp)는 한컴 엔진이 아닙니다. 글자 폭을 근사치로 재므로 줄바꿈·쪽 나눔이 한글과 다를 수 있습니다. **최종 판정은 한글에서 직접 열어 확인해야 합니다.**
- 라이선스: python-hwpx Apache-2.0, @rhwp/core MIT, pyhwp AGPL-3.0(검증용으로만 사용, 서비스 코드에 넣지 말 것). HWP 공개 문서를 참고해 만든 제품은 "본 제품은 한글과컴퓨터의 한글 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다." 문구를 넣어야 합니다.
