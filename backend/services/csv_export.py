"""
Ekspor CSV untuk Activity Log dan riwayat sesi (Remote, Web, SSH).

Waktu ditulis dalam zona waktu dari pengaturan Sistem (bawaan Asia/Jakarta), sama dengan yang tampil di dashboard. Isi sel yang diawali = + - @ diberi
tanda kutip tunggal di depannya: sebagian isi berasal dari pengguna (username login gagal, judul,
alamat), dan tanpa itu Excel/LibreOffice bisa menjalankannya sebagai rumus saat file dibuka.
"""
import csv
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi.responses import Response

DEFAULT_TZ = ZoneInfo("Asia/Jakarta")
MAX_ROWS = 50_000


def fmt_time(value, tz=DEFAULT_TZ) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):                       # epoch milidetik (Guacamole)
        value = datetime.fromtimestamp(value / 1000, timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    return text


def csv_response(name: str, header: list[str], rows, tz=DEFAULT_TZ) -> Response:
    """File CSV (UTF-8 dengan BOM supaya Excel membaca huruf non-ASCII dengan benar)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    stamp = datetime.now(tz).strftime("%Y%m%d-%H%M")
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ccd-{name}-{stamp}.csv"'},
    )
