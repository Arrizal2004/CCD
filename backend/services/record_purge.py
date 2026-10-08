"""
Bantuan untuk menghapus tiket Helpdesk dan Infra Request (superadmin): hapus folder lampiran dengan
aman, dan susun ringkasan untuk Audit Trail. Isi percakapan sengaja tidak ikut disalin ke Audit Trail;
yang dicatat hanya ringkasan (nomor, judul, pemilik, status, tanggal, jumlah pesan dan lampiran).
"""
import logging
import shutil
from pathlib import Path

from services import system_settings as ss

log = logging.getLogger("record_purge")


def remove_dir(base: Path, name: str) -> bool:
    """Hapus folder `base/name`. Hanya folder langsung di bawah `base` yang boleh dihapus."""
    try:
        root = base.resolve()
        target = (base / name).resolve()
        if target.parent != root or not target.is_dir():
            return False
        shutil.rmtree(target)
        return True
    except OSError as e:
        log.warning("folder lampiran %s/%s gagal dihapus: %s", base, name, e)
        return False


def stamp(dt) -> str:
    """Tanggal dan jam untuk ringkasan, dalam zona waktu pengaturan Sistem."""
    return dt.astimezone(ss.tzinfo()).strftime("%Y-%m-%d %H:%M") if dt else "-"


def short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
