"""
Tingkat akses student ke sebuah VM: 'full' (Connect dan Open Web) atau 'web' (hanya lihat status, Open Web,
dan Helpdesk). Tingkat 'web' ditolak di semua jalur Connect, kredensial, power, dan snapshot. Admin tidak
terpengaruh.
"""
from fastapi import HTTPException

from database import get_student_vm_access
from i18n import tr

LEVELS = ("full", "web")


def web_only_error() -> HTTPException:
    return HTTPException(403, tr("VM ini hanya bisa dibuka lewat Open Web",
                                 "This VM is only available through Open Web"))


def no_access_error() -> HTTPException:
    return HTTPException(403, tr("Anda tidak punya akses ke VM ini", "You do not have access to this VM"))


async def require_full_access(user_id: int, vm_id: str, host_name: str) -> None:
    """403 kalau VM tidak ditugaskan ke student, atau ditugaskan hanya untuk Open Web."""
    level = (await get_student_vm_access(user_id, host_name)).get((str(vm_id), host_name))
    if level is None:
        raise no_access_error()
    if level != "full":
        raise web_only_error()


async def has_full_access(user_id: int, vm_id: str, host_name: str) -> bool:
    return (await get_student_vm_access(user_id, host_name)).get((str(vm_id), host_name)) == "full"
