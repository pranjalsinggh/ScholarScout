"""Transparent column-level encryption types.

EncryptedText encrypts on write and decrypts on read with the server key, so
the rest of the codebase works with plain values while the database file only
holds ciphertext. Legacy plaintext rows are tolerated on read and re-encrypted
on their next write.
"""
import json

from sqlalchemy import Text, TypeDecorator

from app.security.owner import server_decrypt, server_encrypt


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return server_encrypt(value)

    def process_result_value(self, value, dialect):
        return server_decrypt(value)


class EncryptedJSON(TypeDecorator):
    """Encrypt structured metadata while returning ordinary Python values.

    Existing SQLite JSON rows are decoded on read; startup maintenance rewrites
    them so a database copied after migration no longer exposes their content.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return server_encrypt(json.dumps(value, ensure_ascii=False))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(server_decrypt(value))
