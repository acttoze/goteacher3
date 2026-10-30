# hwp — 한글 문서 엔진

`research/hwp_poc/troad_hwp.py`를 옮긴 순수 파이썬 패키지다. Django를 import하지 않는다. 바이트를 받아 바이트나 dict를 돌려준다.
python-hwpx **6.10 고정**(Apache-2.0). pyhwp(AGPL)는 쓰지 않는다.

```python
import hwp
kind = hwp.sniff(upload)                        # 웹 요청 안: 매직 바이트·크기·가벼운 구조만(압축 안 풂)
hwpx, kind, report = hwp.to_hwpx(upload)        # 매직 바이트 판별·크기·zip 가드·hwp→HWPX 변환
cands = hwp.candidates(hwpx)                    # 표 후보 점수순. 자동 확정 금지(1순위 적중 약 70%)
s = hwp.schema(hwpx, section, index)            # 입력 칸·라벨·합계 행
hwpx = hwp.add_rows(hwpx, section, index, ref_row, n)  # 명부가 표 줄보다 많을 때 ref_row 아래 빈 줄 n개(검증 포함)
out = hwp.fill(hwpx, section, index, values)    # values={(r, c): '값'} → 검증 통과한 hwpx
if hwp.output_format(kind) == 'hwp':
    out_hwp = hwp.to_hwp(out, section, index, values)   # HWP 5.0 다시 쓰고 되읽어 확인
```

- `fill`은 재파싱 검증(`verify`: 값·구조·다른 칸·다른 표·바뀐 파트)과 `zip_ok`를 통과한 결과만 돌려준다. `to_hwp`는 HWP 5.0으로 다시 쓴 뒤 되읽어 값을 확인한다. 채우기는 한 번만 돌고 hwpx·hwp 둘 다 줄 수 있다.
- 표 안에 표가 있으면, 바깥 칸 주소가 안쪽 표 칸 주소와 같은 칸은 입력 칸에서 빼고 `fill`도 거부한다(python-hwpx `fill_cells`가 그 주소를 안쪽 표에 씀).
- 오류는 모두 `HwpError` 하위(`UnsupportedFormat`·`TooLarge`·`UnsafeFile`·`ConvertError`·`FillError`). 문구와 `FillError.problems`에는 (구역, 표, 행, 열) 주소와 사유만 담긴다. 문서 내용은 넣지 않는다.
- 게이트 ③이 실패하면 환경 변수 `GT_HWP_OUTPUT=hwpx` 하나로 hwp 원본도 hwpx로 돌려준다(`output_format`).
- 상한: 업로드 30MB, zip 풀린 총량 200MB, 항목 1,000개, 칸 글자 2,000자. 표본 1,376개 최대치(17MB·52MB·67개)에 여유를 둔 값이다.
- hwp(OLE) 안쪽 압축 스트림: python-hwpx는 스트림 하나를 256MB까지 풀지만 스트림 수에 총량 상한이 없다. 그래서 변환 전 `check_ole`가 모든 스트림을 1MB 조각으로 풀며 크기만 세어 풀린 총량 200MB·스트림 1,000개를 넘으면 `UnsafeFile`로 거부한다(표본 hwp 1,136개 최대 123MB·81개·0.5초). 시간·메모리는 worker 상한(300초·2.5GB, 스왑 없음)이 한 번 더 막는다.
- zip 항목은 저장·deflate만 받는다(CPython은 bzip2·lzma를 조각 상한 없이 푼다). XML은 UTF-8만, `<!DOCTYPE`·`<!ENTITY`가 있으면 거부한다(python-hwpx가 읽기 전에). 우리 파서는 `resolve_entities=False`·`no_network`·`huge_tree=False`.
- 악성 표본 묶음: `hwp/tests/test_malicious.py`(폭탄·엔티티·경로 조작, 표본은 테스트 안에서 만든다).

## 테스트·벤치

```bash
uv run python -m unittest discover -s hwp/tests -t .
# 표본은 저장소 밖 복사본 폴더로(신뢰하지 않는 입력)
uv run python -m hwp.bench read <표본폴더>
uv run python -m hwp.bench fill <표본폴더> --names <대상목록.txt>
```
