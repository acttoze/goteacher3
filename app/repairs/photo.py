"""수리 요청 사진 재인코딩(K1). 올린 바이트를 그대로 두지 않고 픽셀만 새 JPEG로 옮긴다: EXIF·GPS·주석·ICC·PNG 글자 칸이 모두 빠진다.

- 받는 형식은 내용으로 JPEG·PNG만(이름은 안 본다). Pillow의 다른 디코더(GIF·TIFF·WEBP 등)는 열지 않는다. HEIC는 Pillow가 못 읽어 안내한다.
- 픽셀 폭탄: 머리의 가로×세로로 먼저 거부하고 그다음에 픽셀을 푼다. JPEG는 draft로 줄여 풀어 메모리를 아낀다.
- 오류에는 파일 내용·이름을 담지 않는다.
"""
import io

from PIL import Image, ImageOps

MB = 1024 * 1024
MAX_BYTES = 8 * MB
MAX_PIXELS = 20_500_000  # 공용 서버 메모리 보호. 2000만 화소 폰 사진의 흔한 크기(5184×3888 = 2015만)까지 받는다
LONG_SIDE = 1600
QUALITY = 85


class BadPhoto(Exception):
    """화면에 보여 줄 안내 한 줄을 담는다."""


NOT_IMAGE = "JPG·PNG 사진만 올릴 수 있어요. 아이폰 HEIC 사진은 JPG로 바꿔서 올려 주세요."
TOO_MANY_PIXELS = "사진 화소가 너무 많아요(2000만 화소까지). 폰 카메라의 고화소 모드 사진은 크기를 줄여 다시 올려 주세요."


def reencode(f):
    """파일 객체 → 긴 변 1600px 이하 JPEG 바이트. 못 받는 파일이면 BadPhoto."""
    try:
        with Image.open(f, formats=("JPEG", "PNG")) as im:
            if im.width * im.height > MAX_PIXELS:
                raise BadPhoto(TOO_MANY_PIXELS)
            im.draft("RGB", (LONG_SIDE, LONG_SIDE))
            ImageOps.exif_transpose(im, in_place=True)
            alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            want = "RGBA" if alpha else "RGB"  # 투명이 없으면 RGBA로 불리지 않는다(메모리)
            pic = im if im.mode == want else im.convert(want)
            pic.thumbnail((LONG_SIDE, LONG_SIDE), Image.Resampling.LANCZOS)
            clean = Image.new("RGB", pic.size, "white")  # 새 이미지라 info(주석 등)가 따라오지 않는다
            clean.paste(pic, mask=pic.getchannel("A") if pic.mode == "RGBA" else None)
    except BadPhoto:
        raise
    except Image.DecompressionBombError:
        raise BadPhoto(TOO_MANY_PIXELS) from None
    except Exception:  # 믿을 수 없는 입력의 디코더 오류는 종류가 많다(OSError·SyntaxError·ValueError·struct.error…). 어느 것이든 '못 읽음'
        raise BadPhoto(NOT_IMAGE) from None
    out = io.BytesIO()
    clean.save(out, "JPEG", quality=QUALITY, optimize=True)
    return out.getvalue()
