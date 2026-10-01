"""
Enkripsi application-layer untuk jalur agent ↔ backend.

Tujuan: data agent tidak mentah di jaringan, namun transport tetap HTTP/Redis
biasa (bukan TLS). Payload di-"encapsulate" pakai AES-256-GCM (rahasia + integritas).

Skema envelope (identik di sisi C# agent):
    base64( nonce[12] || ciphertext || tag[16] )
Kunci  = SHA-256(AGENT_ENC_SECRET).  Kosong → enkripsi NONAKTIF (mode kompatibel).

Dua jalur yang dilindungi:
  1. HTTP  : body request agent (register/heartbeat/metrics) + respons register.
  2. Redis : channel "commands:*" (perintah backend→agent) dan semua result-key
             yang ditulis agent (dibungkus transparan oleh SecureRedis).
"""
import os
import base64
import hashlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_secret = os.getenv("AGENT_ENC_SECRET", "")
ENABLED = bool(_secret)
_key = hashlib.sha256(_secret.encode()).digest() if _secret else None

# Channel & key Redis yang dipakai bersama agent (melintasi jaringan tak tepercaya).
COMMAND_PREFIX = "commands:"
RESULT_PREFIXES = (
    "agent-cmd-result:", "snapshot-list:", "vm-nics:", "vswitch-list:",
    "net-adapters:", "nic-op:", "ps-exec:", "host-exec:",
)


def seal(plaintext: str) -> str:
    """Enkripsi string → envelope base64. No-op bila enkripsi nonaktif."""
    if not ENABLED or plaintext is None:
        return plaintext
    nonce = os.urandom(12)
    ct = AESGCM(_key).encrypt(nonce, plaintext.encode(), None)  # ct = ciphertext||tag
    return base64.b64encode(nonce + ct).decode()


def unseal(blob: str) -> str:
    """Dekripsi envelope → plaintext. No-op bila enkripsi nonaktif."""
    if not ENABLED or blob is None:
        return blob
    try:
        raw = base64.b64decode(blob)
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(_key).decrypt(nonce, ct, None).decode()
    except Exception:
        raise ValueError("unseal: dekripsi gagal — kemungkinan pesan ditamper")


class SecureRedis:
    """
    Proxy tipis di atas client redis.asyncio. Hanya menimpa publish/get supaya
    perintah ke agent (channel 'commands:*') terenkripsi dan hasil dari agent
    (result-key) terdekripsi secara transparan. Method lain diteruskan apa adanya.
    Key internal backend (metrics cache, registry agent) tidak tersentuh.
    """

    def __init__(self, client):
        self._c = client

    def __getattr__(self, name):
        return getattr(self._c, name)

    async def publish(self, channel, message):
        if ENABLED and isinstance(channel, str) and channel.startswith(COMMAND_PREFIX):
            if isinstance(message, (bytes, bytearray)):
                message = message.decode()
            message = seal(message)
        return await self._c.publish(channel, message)

    async def get(self, key):
        val = await self._c.get(key)
        if val is not None and isinstance(key, str) and key.startswith(RESULT_PREFIXES):
            return unseal(val)
        return val


def secure_from_url(client):
    """Bungkus client redis dengan SecureRedis (no-op transparan bila nonaktif)."""
    return SecureRedis(client)
