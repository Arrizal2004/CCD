"""
Reset password akun CCD langsung dari server, untuk keadaan darurat: misalnya satu-satunya superadmin
lupa password sehingga tidak ada admin lain yang bisa mereset dari dashboard.

    cd backend && docker compose exec backend python scripts/reset_password.py <username>

Mencetak password sementara. Semua sesi lama akun itu berakhir, dan pengguna wajib menggantinya saat login.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from auth import hash_password  # noqa: E402
from database import DATABASE_URL  # noqa: E402
from services.guest_accounts import generate_password  # noqa: E402


async def main(username: str) -> int:
    password = generate_password()
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow(
            """UPDATE users SET password_hash = $1, password_version = password_version + 1,
                      must_change_password = TRUE, updated_at = NOW()
               WHERE username = $2 AND deleted_at IS NULL RETURNING id, role, is_active""",
            hash_password(password), username)
        if not row:
            print(f"Akun '{username}' tidak ditemukan.", file=sys.stderr)
            return 1
        await conn.execute(
            """INSERT INTO audit_logs (user_id, username, user_role, action_type, severity_level, detail_message)
               VALUES ($1, $2, $3, 'USER_PASSWORD_RESET', 'WARNING', $4)""",
            row["id"], username, row["role"], f"Password akun '{username}' direset dari server (scripts/reset_password.py)")
    finally:
        await conn.close()
    print(f"Password sementara untuk {username}: {password}")
    print("Pengguna wajib menggantinya saat login. Kalau akun terkunci karena salah password, tunggu 5 menit.")
    if not row["is_active"]:
        print("Catatan: akun ini nonaktif. Aktifkan dulu dari dashboard atau database sebelum login.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Pemakaian: python scripts/reset_password.py <username>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
