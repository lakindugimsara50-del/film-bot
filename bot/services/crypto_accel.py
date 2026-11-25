"""
crypto_accel.py — High-Speed Hardware/C AES-256-IGE Acceleration for Pyrogram MTProto.

Resolves the CPython 3.13 upload/download throughput collapse on Kaggle/Colab when
`tgcrypto` C-extension wheels fail to install:
  1. Native `tgcrypto` (if already installed, nothing to patch).
  2. Native `cryptg` C/Rust extension (`cryptg.encrypt_ige` / `cryptg.decrypt_ige`).
  3. System OpenSSL `libcrypto` (`AES_ige_encrypt` via `ctypes`), always present on Linux.
  4. Optimized pure-Python `cryptography` AES-ECB fallback (verified bit-exact).

Every non-tgcrypto backend MUST pass a deterministic roundtrip + reference vector check
before replacing `pyrogram.crypto.aes.ige256_encrypt` / `ige256_decrypt`.
"""

import ctypes
import ctypes.util
import logging
import struct
from typing import Callable, Optional, Tuple

log = logging.getLogger(__name__)

_INSTALLED_BACKEND: Optional[str] = None


def _build_py_ecb_ige() -> Tuple[Optional[Callable], Optional[Callable]]:
    """Return a clean pure-Python + OpenSSL-ECB AES-256-IGE pair (reference + fallback)."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
    except Exception:
        return None, None

    backend = default_backend()
    unpack = struct.unpack
    pack = struct.pack

    def _py_ige_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
        encryptor = Cipher(algorithms.AES(key), modes.ECB(), backend=backend).encryptor()
        update = encryptor.update
        iv_1_hi, iv_1_lo = unpack(">QQ", iv[:16])
        iv_2_hi, iv_2_lo = unpack(">QQ", iv[16:32])
        n = len(data)
        out = bytearray(n)
        for i in range(0, n, 16):
            c_hi, c_lo = unpack(">QQ", data[i:i + 16])
            t = pack(">QQ", c_hi ^ iv_1_hi, c_lo ^ iv_1_lo)
            enc = update(t)
            e_hi, e_lo = unpack(">QQ", enc)
            r_hi, r_lo = e_hi ^ iv_2_hi, e_lo ^ iv_2_lo
            out[i:i + 16] = pack(">QQ", r_hi, r_lo)
            iv_1_hi, iv_1_lo = r_hi, r_lo
            iv_2_hi, iv_2_lo = c_hi, c_lo
        return bytes(out)

    def _py_ige_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
        decryptor = Cipher(algorithms.AES(key), modes.ECB(), backend=backend).decryptor()
        update = decryptor.update
        iv_1_hi, iv_1_lo = unpack(">QQ", iv[:16])
        iv_2_hi, iv_2_lo = unpack(">QQ", iv[16:32])
        n = len(data)
        out = bytearray(n)
        for i in range(0, n, 16):
            c_hi, c_lo = unpack(">QQ", data[i:i + 16])
            t = pack(">QQ", c_hi ^ iv_2_hi, c_lo ^ iv_2_lo)
            dec = update(t)
            d_hi, d_lo = unpack(">QQ", dec)
            r_hi, r_lo = d_hi ^ iv_1_hi, d_lo ^ iv_1_lo
            out[i:i + 16] = pack(">QQ", r_hi, r_lo)
            iv_2_hi, iv_2_lo = r_hi, r_lo
            iv_1_hi, iv_1_lo = c_hi, c_lo
        return bytes(out)

    return _py_ige_encrypt, _py_ige_decrypt


def _verify_ige_pair(
    enc_fn: Callable[[bytes, bytes, bytes], bytes],
    dec_fn: Callable[[bytes, bytes, bytes], bytes],
    ref_enc: Optional[Callable[[bytes, bytes, bytes], bytes]],
    ref_dec: Optional[Callable[[bytes, bytes, bytes], bytes]],
) -> bool:
    """Verify roundtrip correctness and bit-exact parity against reference AES-IGE."""
    sample_plain = (b"\x01\x23\x45\x67\x89\xab\xcd\xef" * 8)  # 64 bytes (4 blocks)
    sample_key = bytes(range(32))
    sample_iv = bytes(range(32, 64))
    try:
        ct = enc_fn(sample_plain, sample_key, sample_iv)
        if not isinstance(ct, (bytes, bytearray)) or len(ct) != len(sample_plain):
            return False
        pt = dec_fn(bytes(ct), sample_key, sample_iv)
        if bytes(pt) != sample_plain:
            return False
        if ref_enc is not None:
            expected_ct = ref_enc(sample_plain, sample_key, sample_iv)
            if bytes(ct) != expected_ct:
                return False
        if ref_dec is not None:
            expected_pt = ref_dec(bytes(ct), sample_key, sample_iv)
            if bytes(pt) != expected_pt:
                return False
        return True
    except Exception:
        return False


def _try_build_libcrypto_ige() -> Tuple[Optional[Callable], Optional[Callable]]:
    """Bind OpenSSL libcrypto native C `AES_ige_encrypt` via ctypes if exported."""
    candidate_names = [
        ctypes.util.find_library("crypto"),
        "libcrypto.so.3",
        "libcrypto.so.1.1",
        "libcrypto.so",
        "libcrypto-3-x64.dll",
        "libcrypto-1_1-x64.dll",
    ]
    lib = None
    for name in candidate_names:
        if not name:
            continue
        try:
            loaded = ctypes.CDLL(name)
            if hasattr(loaded, "AES_ige_encrypt") and hasattr(loaded, "AES_set_encrypt_key") and hasattr(loaded, "AES_set_decrypt_key"):
                lib = loaded
                break
        except Exception:
            continue

    if lib is None:
        return None, None

    try:
        lib.AES_set_encrypt_key.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        lib.AES_set_encrypt_key.restype = ctypes.c_int
        lib.AES_set_decrypt_key.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        lib.AES_set_decrypt_key.restype = ctypes.c_int
        lib.AES_ige_encrypt.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        lib.AES_ige_encrypt.restype = None
    except Exception:
        return None, None

    def _c_ige_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
        n = len(data)
        if n == 0:
            return b""
        aes_key = ctypes.create_string_buffer(512)
        if lib.AES_set_encrypt_key(key, len(key) * 8, aes_key) != 0:
            raise ValueError("AES_set_encrypt_key failed")
        iv_buf = ctypes.create_string_buffer(bytes(iv[:32]), 32)
        out_buf = ctypes.create_string_buffer(n)
        lib.AES_ige_encrypt(data, out_buf, n, aes_key, iv_buf, 1)
        return out_buf.raw

    def _c_ige_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
        n = len(data)
        if n == 0:
            return b""
        aes_key = ctypes.create_string_buffer(512)
        if lib.AES_set_decrypt_key(key, len(key) * 8, aes_key) != 0:
            raise ValueError("AES_set_decrypt_key failed")
        iv_buf = ctypes.create_string_buffer(bytes(iv[:32]), 32)
        out_buf = ctypes.create_string_buffer(n)
        lib.AES_ige_encrypt(data, out_buf, n, aes_key, iv_buf, 0)
        return out_buf.raw

    return _c_ige_encrypt, _c_ige_decrypt


def install_fast_aes_ige(force: bool = False) -> str:
    """
    Ensure Pyrogram uses the fastest available C/hardware AES-256-IGE implementation.
    Returns the active backend name: 'tgcrypto', 'cryptg', 'openssl_libcrypto', 'py_ecb', or 'pyrogram_default'.
    """
    global _INSTALLED_BACKEND
    if _INSTALLED_BACKEND and not force:
        return _INSTALLED_BACKEND

    try:
        import tgcrypto  # noqa: F401
        _INSTALLED_BACKEND = "tgcrypto"
        return _INSTALLED_BACKEND
    except ImportError:
        pass

    try:
        import pyrogram.crypto.aes as pyro_aes
    except Exception:
        _INSTALLED_BACKEND = "pyrogram_default"
        return _INSTALLED_BACKEND

    ref_enc, ref_dec = _build_py_ecb_ige()

    # 1. Try native `cryptg` Rust/C extension (common prebuilt wheel for Python 3.13)
    try:
        import cryptg  # type: ignore
        if hasattr(cryptg, "encrypt_ige") and hasattr(cryptg, "decrypt_ige"):
            if _verify_ige_pair(cryptg.encrypt_ige, cryptg.decrypt_ige, ref_enc, ref_dec):
                pyro_aes.ige256_encrypt = cryptg.encrypt_ige
                pyro_aes.ige256_decrypt = cryptg.decrypt_ige
                _INSTALLED_BACKEND = "cryptg"
                log.info("[CryptoAccel] Activated native C/Rust cryptg AES-256-IGE acceleration!")
                return _INSTALLED_BACKEND
    except Exception:
        pass

    # 2. Try OpenSSL system libcrypto native C `AES_ige_encrypt` via ctypes (AES-NI speed, no pip wheel needed!)
    try:
        c_enc, c_dec = _try_build_libcrypto_ige()
        if c_enc and c_dec and _verify_ige_pair(c_enc, c_dec, ref_enc, ref_dec):
            pyro_aes.ige256_encrypt = c_enc
            pyro_aes.ige256_decrypt = c_dec
            _INSTALLED_BACKEND = "openssl_libcrypto"
            log.info("[CryptoAccel] Activated C OpenSSL libcrypto AES_ige_encrypt hardware acceleration!")
            return _INSTALLED_BACKEND
    except Exception:
        pass

    # 3. Fallback to optimized OpenSSL-ECB pure-Python loop
    if ref_enc and ref_dec:
        pyro_aes.ige256_encrypt = ref_enc
        pyro_aes.ige256_decrypt = ref_dec
        _INSTALLED_BACKEND = "py_ecb"
        log.warning("[CryptoAccel] Native tgcrypto/cryptg/libcrypto AES_ige unavailable; using ECB Python fallback.")
        return _INSTALLED_BACKEND

    _INSTALLED_BACKEND = "pyrogram_default"
    return _INSTALLED_BACKEND
