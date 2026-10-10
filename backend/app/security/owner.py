"""Privacy core: anonymous owner identity + encryption at rest.

Model
─────
- The browser generates a random 256-bit token on first visit (sessionStorage).
  Every API call carries it in the X-Owner-Token header.
- The server never persists the token. It stores only a keyed hash
  (owner_hash = sha256(token + pepper)) as a pseudonymous owner id, and
  derives an AES-256 key from the token for encryption.
- Chat messages are encrypted with that key. Uploaded PDFs use per-file keys
  wrapped for both the browser and server so background processing can resume.
  The server can decrypt PDFs and paper text while processing; anonymous
  visitors cannot read another session's data.
- No accounts, no emails, no personal information anywhere.

Pepper
──────
A server-side random pepper is generated once (data/secret.key) and mixed
into both the owner hash and the encryption key so two ScholarScout
installations produce incompatible identities for the same token.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import DATA_DIR, settings

PEPPER_FILE = DATA_DIR / "secret.key"
ENC_PREFIX = "enc:v1:"
_KEY_CACHE: dict[str, bytes] = {}


def _load_or_create_pepper() -> bytes:
    configured = os.getenv("SCHOLARSCOUT_SECRET_KEY", "")
    if configured:
        if len(configured) < 32:
            raise RuntimeError("SCHOLARSCOUT_SECRET_KEY must contain at least 32 random characters.")
        return hashlib.sha256(configured.encode()).digest()
    if settings.production:
        raise RuntimeError("Production requires SCHOLARSCOUT_SECRET_KEY from the deployment secret store.")
    if PEPPER_FILE.exists():
        return PEPPER_FILE.read_bytes()
    pepper = secrets.token_bytes(32)
    try:
        descriptor = os.open(PEPPER_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(pepper)
        return pepper
    except FileExistsError:
        return PEPPER_FILE.read_bytes()


PEPPER = _load_or_create_pepper()


def forget_token(token: str) -> None:
    """Drop volatile key material after erasure; never persist bearer tokens."""
    _KEY_CACHE.pop(token, None)


def new_token() -> str:
    """Generate a fresh owner token (client-side equivalent lives in JS)."""
    return "ss_" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def owner_hash(token: str) -> str:
    """Pseudonymous owner id - stored in the DB, cannot be reversed to the token."""
    return hashlib.sha256(b"owner:" + PEPPER + token.encode()).hexdigest()[:32]


def _derive_key(token: str) -> bytes:
    cached = _KEY_CACHE.get(token)
    if cached:
        return cached
    key = hashlib.pbkdf2_hmac("sha256", token.encode(), b"enc:" + PEPPER, 120_000, dklen=32)
    if len(_KEY_CACHE) > 512:  # bound memory on long-lived processes
        _KEY_CACHE.clear()
    _KEY_CACHE[token] = key
    return key


# ------------------------------------------------------------------ bytes --

def encrypt_bytes(token: str, plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    ct = AESGCM(_derive_key(token)).encrypt(nonce, plaintext, b"scholarscout")
    return nonce + ct


def decrypt_bytes(token: str, blob: bytes) -> bytes:
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(_derive_key(token)).decrypt(nonce, ct, b"scholarscout")


# ------------------------------------------------------------------- text --

def encrypt_text(token: str, plaintext: str) -> str:
    blob = encrypt_bytes(token, plaintext.encode("utf-8"))
    return ENC_PREFIX + base64.urlsafe_b64encode(blob).decode()


def decrypt_text(token: str, stored: str | None) -> str:
    if not stored:
        return ""
    if not isinstance(stored, str):
        return str(stored)  # legacy non-string column value
    if not stored.startswith(ENC_PREFIX):
        return stored  # legacy plaintext row
    try:
        blob = base64.urlsafe_b64decode(stored[len(ENC_PREFIX):].encode())
        return decrypt_bytes(token, blob).decode("utf-8")
    except Exception:
        return ""  # wrong/absent token → unreadable by design


def encrypt_json(token: str, obj) -> str:
    import json
    return encrypt_text(token, json.dumps(obj, ensure_ascii=False))


def decrypt_json(token: str, stored):
    if stored and not isinstance(stored, str):
        return stored  # legacy rows: JSON already decoded by the DB driver
    raw = decrypt_text(token, stored)
    if not raw:
        return []
    import json
    try:
        return json.loads(raw)
    except Exception:
        return []


# ------------------------------------------------------------------- files --

def encrypt_file_in_place(token: str, path: str) -> str:
    """Encrypt a file on disk (plaintext removed). Returns the .enc path."""
    blob = encrypt_bytes(token, open(path, "rb").read())
    enc_path = path + ".enc"
    with open(enc_path, "wb") as f:
        f.write(blob)
    os.remove(path)
    return enc_path


def decrypt_file(token: str, enc_path: str, dest: str) -> str:
    blob = decrypt_bytes(token, open(enc_path, "rb").read())
    with open(dest, "wb") as f:
        f.write(blob)
    return dest


def is_encrypted(path: str) -> bool:
    return path.endswith(".enc")


# ------------------------------------------------------- file key wrapping --

def wrap_file_key(file_key: bytes, token: str) -> str:
    """Owner wrap - needs the browser token to unwrap."""
    nonce = os.urandom(12)
    ct = AESGCM(_derive_key(token)).encrypt(nonce, file_key, b"filekey")
    return base64.urlsafe_b64encode(nonce + ct).decode()


def server_wrap_file_key(file_key: bytes) -> str:
    """Server wrap - lets a restarted server resume processing without the token."""
    nonce = os.urandom(12)
    ct = AESGCM(_server_key).encrypt(nonce, file_key, b"filekey")
    return base64.urlsafe_b64encode(nonce + ct).decode()


def unwrap_file_key(token: str, w1: str | None, w2: str | None) -> bytes | None:
    for wrapped, key in ((w1, _derive_key(token) if token else None), (w2, _server_key)):
        if not wrapped or key is None:
            continue
        try:
            blob = base64.urlsafe_b64decode(wrapped.encode())
            return AESGCM(key).decrypt(blob[:12], blob[12:], b"filekey")
        except Exception:
            continue
    return None


def decrypt_paper_file(token: str, w1: str | None, w2: str | None, enc_path: str, dest: str) -> str:
    fk = unwrap_file_key(token, w1, w2)
    if fk is None:
        raise PermissionError("file key unavailable for this paper")
    blob = decrypt_bytes_from_raw(fk, enc_path)
    with open(dest, "wb") as f:
        f.write(blob)
    return dest


def decrypt_bytes_from_raw(file_key: bytes, enc_path: str) -> bytes:
    blob = open(enc_path, "rb").read()
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(file_key).decrypt(nonce, ct, b"scholarscout")


def encrypt_bytes_with_key(file_key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(file_key).encrypt(nonce, plaintext, b"scholarscout")


# ------------------------------------------------ field-level encryption ----
# Server-held key (derived from secret.key). This encrypts database content at
# rest: a stolen data folder WITHOUT secret.key reveals nothing. The running
# server can read it - that is unavoidable for search to work. Uploaded PDFs
# remain locked to the owner's browser key (stronger).

_server_key = hashlib.pbkdf2_hmac("sha256", b"fields", PEPPER, 120_000, dklen=32)
_SRV_PREFIX = "srv:v1:"
_SRV_BLOB_PREFIX = b"ssblob:v1:"


def server_encrypt(text: str | None) -> str | None:
    if text is None or text == "":
        return text
    nonce = os.urandom(12)
    ct = AESGCM(_server_key).encrypt(nonce, text.encode("utf-8"), b"fields")
    return _SRV_PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()


def server_decrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    if not value.startswith(_SRV_PREFIX):
        return value  # legacy plaintext
    try:
        blob = base64.urlsafe_b64decode(value[len(_SRV_PREFIX):].encode())
        return AESGCM(_server_key).decrypt(blob[:12], blob[12:], b"fields").decode("utf-8")
    except Exception:
        return value


def server_encrypt_blob(plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    return _SRV_BLOB_PREFIX + nonce + AESGCM(_server_key).encrypt(nonce, plaintext, b"index")


def server_decrypt_blob(stored: bytes) -> bytes:
    if not stored.startswith(_SRV_BLOB_PREFIX):
        raise ValueError("Unencrypted legacy index must be rebuilt")
    blob = stored[len(_SRV_BLOB_PREFIX):]
    return AESGCM(_server_key).decrypt(blob[:12], blob[12:], b"index")


class EncryptedText:
    """SQLAlchemy TypeDecorator mixin target - see app/database/types.py.

    (Kept as a separate module to avoid a security<->models import cycle.)"""
    pass


# ------------------------------------------------------ legacy adoption ----

_ADOPTED = False


def maybe_adopt_legacy(owner: str, token: str = "") -> None:
    """One-time adoption of pre-privacy rows (owner='local') by the first
    owner that uses the app after this upgrade - so existing papers aren't
    stranded. Adopted plaintext files are encrypted with the adopter's key."""
    global _ADOPTED
    if _ADOPTED:
        return
    _ADOPTED = True
    from app.database.database import SessionLocal
    from app.models.entities import ChatMessage, Paper, RetrievalProfile, WatchItem
    db = SessionLocal()
    try:
        n_papers = db.query(Paper).filter(Paper.owner == "local") \
            .update({Paper.owner: owner}, synchronize_session=False)
        db.query(WatchItem).filter(WatchItem.owner == "local") \
            .update({WatchItem.owner: owner}, synchronize_session=False)
        db.query(ChatMessage).filter(ChatMessage.owner == "local") \
            .update({ChatMessage.owner: owner}, synchronize_session=False)
        db.query(RetrievalProfile).filter(RetrievalProfile.owner == "local") \
            .update({RetrievalProfile.owner: owner}, synchronize_session=False)
        db.commit()
        if n_papers:
            # encrypt legacy plaintext uploads for the adopting owner
            encrypted = 0
            for p in db.query(Paper).filter(Paper.owner == owner).all():
                if p.file_path and not p.file_path.endswith(".enc") and os.path.exists(p.file_path):
                    try:
                        p.file_path = encrypt_file_in_place(token, p.file_path)
                        encrypted += 1
                    except Exception:
                        pass
            db.commit()
            import logging
            logging.getLogger("scholarscout.privacy").info(
                "adopted %d pre-privacy papers for the first owner (%d files encrypted)",
                n_papers, encrypted)
    finally:
        db.close()
