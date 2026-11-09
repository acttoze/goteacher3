"""P1 구버전(그누보드) 비밀번호 해시를 그대로 받는 해셔. 확인만 하고, 맞으면 Django가 기본 해셔로 바꿔 저장한다.

저장 형식: g5pbkdf2$sha256:<반복>:<솔트>:<해시>
해시 = base64(PBKDF2-HMAC-SHA256(비밀번호, 솔트 문자열 그대로의 바이트, 반복, 24바이트)). 솔트는 base64 디코드하지 않는다.
"""
import base64
import hashlib
import logging

from django.contrib.auth.hashers import BasePasswordHasher, PBKDF2PasswordHasher, mask_hash
from django.utils.crypto import constant_time_compare

log = logging.getLogger(__name__)


class G5PBKDF2PasswordHasher(BasePasswordHasher):
    algorithm = "g5pbkdf2"

    def decode(self, encoded):
        """형식이 틀리면 ValueError."""
        algorithm, rest = encoded.split("$", 1)
        digest, iterations, salt, hash_ = rest.split(":")
        if algorithm != self.algorithm or digest != "sha256" or not salt or not hash_:
            raise ValueError("g5pbkdf2 형식이 아님")
        return {"algorithm": algorithm, "iterations": int(iterations), "salt": salt, "hash": hash_}

    def encode(self, password, salt, iterations=12000):
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations, 24)
        return f"{self.algorithm}$sha256:{iterations}:{salt}:{base64.b64encode(dk).decode()}"

    def verify(self, password, encoded):
        """깨진 구 해시는 틀린 비밀번호로 본다(500 대신). 로그에는 형식 오류만 남기고 비밀번호·해시는 남기지 않는다."""
        try:
            d = self.decode(encoded)
        except ValueError:
            log.warning("구버전 비밀번호 해시 형식 오류")
            return False
        return constant_time_compare(self.encode(password, d["salt"], d["iterations"]), encoded)

    def safe_summary(self, encoded):
        d = self.decode(encoded)
        return {"algorithm": d["algorithm"], "iterations": d["iterations"], "salt": mask_hash(d["salt"]), "hash": mask_hash(d["hash"])}

    def must_update(self, encoded):
        return True

    def harden_runtime(self, password, encoded):
        """틀렸을 때 기본 해셔와 같은 시간이 걸리게 반복 수 차이만큼 더 돌린다.
        Django check_password는 기본 해셔가 아닌 해셔에는 이것을 부르지 않으므로 아이디 로그인 뷰가 따로 맞춘다."""
        try:
            d = self.decode(encoded)
        except ValueError:
            d = {"salt": "", "iterations": 0}
        extra = PBKDF2PasswordHasher.iterations - d["iterations"]
        if extra > 0:
            hashlib.pbkdf2_hmac("sha256", password.encode(), d["salt"].encode(), extra, 24)
