"""
Bahasa pesan backend: Accept-Language (atau ?lang= untuk WebSocket) memilih Indonesia atau Inggris, dan
setiap tr("Indonesia", "English") di kode punya dua teks yang lengkap dengan placeholder yang sama.
"""
import ast
import re
from pathlib import Path

import pytest

from i18n import parse_lang
from services.system_settings import DEFAULTS
from tests.conftest import auth

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("header,lang", [
    (None, "id"), ("", "id"), ("en", "en"), ("id", "id"), ("en-US,en;q=0.9,id;q=0.8", "en"),
    ("id-ID,id;q=0.9,en;q=0.8", "id"), ("fr-FR,fr;q=0.9", "id"), ("fr,en;q=0.5", "en"),
])
def test_parse_lang(header, lang):
    assert parse_lang(header) == lang


def test_login_error_follows_language(client):
    body = {"username": "tst_tidak_ada_i18n", "password": "salah-salah"}
    assert client.post("/api/v1/users/login", json=body).json()["detail"] == "Username atau password salah"
    r = client.post("/api/v1/users/login", json=body, headers={"Accept-Language": "en"})
    assert r.json()["detail"] == "Wrong username or password"
    r = client.post("/api/v1/users/login", json=body, headers={"Accept-Language": "en-GB,en;q=0.9"})
    assert r.json()["detail"] == "Wrong username or password"


def test_validation_from_service_follows_language(client, superadmin_token):
    body = {**DEFAULTS, "default_theme": "pink"}
    r = client.put("/api/v1/system/settings", json=body, headers={**auth(superadmin_token), "Accept-Language": "en"})
    assert r.status_code == 400 and r.json()["detail"] == "The theme must be 'dark', 'light' or 'system'"
    r = client.put("/api/v1/system/settings", json=body, headers={**auth(superadmin_token), "Accept-Language": "id"})
    assert r.json()["detail"] == "Tema harus 'dark', 'light', atau 'system'"


def test_role_check_follows_language(client, student_token):
    r = client.get("/api/v1/users", headers={**auth(student_token), "Accept-Language": "en"})
    assert r.status_code == 403 and r.json()["detail"] == "Forbidden: this feature is for admins only."


def _placeholders(node):
    """Nama variabel di dalam {…} sebuah f-string (teks pelengkap seperti '(tidak ada)' boleh berbeda)."""
    if not isinstance(node, ast.JoinedStr):
        return []
    return sorted({n.id for v in node.values if isinstance(v, ast.FormattedValue)
                   for n in ast.walk(v.value) if isinstance(n, ast.Name)})


def test_every_tr_call_is_complete():
    bad = []
    for path in sorted(ROOT.glob("**/*.py")):
        if "tests" in path.parts or path.name == "i18n.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr"):
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if len(node.args) != 2 or not all(isinstance(a, (ast.Constant, ast.JoinedStr)) for a in node.args):
                bad.append(f"{where}: harus tr(literal, literal)")
                continue
            id_text, en_text = (ast.unparse(a) for a in node.args)
            if not re.search(r"\w", id_text) or not re.search(r"\w", en_text):
                bad.append(f"{where}: teks kosong")
            if _placeholders(node.args[0]) != _placeholders(node.args[1]):
                bad.append(f"{where}: placeholder berbeda")
    assert bad == []


def test_every_audit_detail_is_built_in_both_languages():
    """Detail Activity Log harus disusun lewat both(...) supaya ada versi Inggrisnya. Teks langsung
    (string, f-string, atau gabungan) di argumen detail berarti ada catatan yang hanya berbahasa Indonesia."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] in ("tests", "scripts", "migrations") or "__pycache__" in rel.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "log_activity"):
                continue
            detail = node.args[4] if len(node.args) > 4 else next((k.value for k in node.keywords if k.arg == "detail"), None)
            if detail is None:
                continue
            if isinstance(detail, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, "detail audit tanpa both(): " + ", ".join(offenders)


def test_both_builds_two_languages_and_fragments_follow():
    from i18n import tr
    from services.audit import Bi, both
    part = both(lambda: tr("selesai", "done"))
    whole = both(lambda: tr(f"Tugas {part.t()}", f"Task {part.t()}"))
    assert isinstance(whole, Bi) and str(whole) == "Tugas selesai" and whole.en == "Task done"
    assert whole.t() == "Tugas selesai"                                     # di luar permintaan: Indonesia
