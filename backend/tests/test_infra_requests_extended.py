"""
test_infra_requests_extended.py

Extended TDD coverage for /api/v1/infra-requests/* endpoints:

  1. test_student_infrastructure_submission_with_pdf
     Student submits a VPS request then uploads a valid PDF.

  2. test_malicious_script_upload_interception
     PHP web-shell upload is caught at the extension guard (HTTP 400).

  3. test_unauthorized_rbac_rejection_on_approval
     Student role is blocked from the admin status-change path (HTTP 403).

Implementation notes drawn from routers/infra_requests.py:
  - Upload guard order: extension check → MIME check → magic-byte check.
    A .php file is rejected at the extension step with HTTP 400 before the
    declared MIME type is ever inspected.
  - document_url returned by the API is the *fetch* path
    (/api/v1/infra-requests/{id}/document), not a filename URL ending in
    .pdf. The uploaded filename is surfaced in the 'original_name' field.
  - PATCH /{id}/status uses require_sysadmin; the RBAC dependency fires
    before any DB lookup, so a valid request-id is not required.
"""

import io
import uuid

import pytest

from tests.conftest import _run, _create_user, auth

BASE = "/api/v1/infra-requests"

# Minimal valid PDF (magic bytes %PDF required by _verify_magic).
MOCK_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"


# ── Shared helper ──────────────────────────────────────────────────────────────

def _new_infra_request(client, token: str, request_type: str = "VPS") -> str:
    """POST a new infra request and return its UUID id."""
    r = client.post(
        BASE,
        json={
            "request_type": request_type,
            "specs": {"cpu": 2, "ram_gb": 4, "disk_gb": 40},
            "notes": "Automated test request",
        },
        headers=auth(token),
    )
    assert r.status_code == 200, f"create_request failed ({r.status_code}): {r.text}"
    return r.json()["id"]


# ── Scenario 1 ─────────────────────────────────────────────────────────────────

def test_student_infrastructure_submission_with_pdf(client, student_token):
    """
    GIVEN a student with a pending VPS request
    WHEN  they upload a valid PDF document (correct extension, MIME, magic bytes)
    THEN  the endpoint returns HTTP 200 with a non-empty document_url and
          confirms the stored filename carries the .pdf extension.

    The 'document_url' value is the authenticated fetch path
    (/api/v1/infra-requests/{id}/document), not a raw filename URL — the
    filename itself is exposed via the 'original_name' field.
    """
    req_id = _new_infra_request(client, student_token, request_type="VPS")

    r = client.post(
        f"{BASE}/{req_id}/document",
        files={
            "file": ("student_support.pdf", io.BytesIO(MOCK_PDF), "application/pdf")
        },
        headers=auth(student_token),
    )

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    data = r.json()

    # document_url must be present and point at the authenticated fetch route
    assert "document_url" in data, "Response must contain 'document_url'"
    assert data["document_url"], "document_url must be non-empty"
    assert req_id in data["document_url"], (
        "document_url must reference the request id"
    )

    # The stored original filename must carry the .pdf extension
    assert "original_name" in data, "Response must contain 'original_name'"
    assert data["original_name"].endswith(".pdf"), (
        f"original_name '{data['original_name']}' must end with '.pdf'"
    )

    # Byte count must be faithfully echoed back
    assert data["size"] == len(MOCK_PDF), (
        f"Reported size {data['size']} != uploaded size {len(MOCK_PDF)}"
    )


# ── Scenario 2 ─────────────────────────────────────────────────────────────────

def test_malicious_script_upload_interception(client, student_token):
    """
    GIVEN a student with a pending request
    WHEN  they attempt to upload a PHP web-shell (extension: .php,
          content-type: application/x-php)
    THEN  the extension guard intercepts the request and returns HTTP 400
          with a detail message indicating the format is not supported.

    The guard runs in this order:
      1. Extension whitelist check  ← catches .php (HTTP 400)
      2. MIME whitelist check
      3. Magic-byte verification
    So the declared content-type 'application/x-php' is never reached.
    """
    req_id = _new_infra_request(client, student_token, request_type="VPS")

    malicious_content = b"<?php system($_GET['cmd']); ?>"

    r = client.post(
        f"{BASE}/{req_id}/document",
        files={
            "file": ("backdoor.php", io.BytesIO(malicious_content), "application/x-php")
        },
        headers=auth(student_token),
    )

    assert r.status_code == 400, (
        f"Security guard must return 400 for .php upload, got {r.status_code}: {r.text}"
    )

    detail = r.json().get("detail", "")
    # The endpoint returns: "Format tidak didukung '.php'. Diperbolehkan: ..."
    assert "tidak didukung" in detail or "format" in detail.lower(), (
        f"Detail must mention unsupported format, got: '{detail}'"
    )

    # Confirm the body contains no reference to a saved document
    assert "document_url" not in r.json(), (
        "A rejected upload must not return a document_url"
    )


# ── Scenario 3 ─────────────────────────────────────────────────────────────────

def test_unauthorized_rbac_rejection_on_approval(client, student_token):
    """
    GIVEN a user authenticated with the 'student' role
    WHEN  they attempt PATCH /api/v1/infra-requests/{id}/status
          (the administrative review / approval path, guarded by require_sysadmin)
    THEN  the RBAC dependency rejects the request with HTTP 403 Forbidden
          before any database lookup is performed.

    A well-formed but non-existent request UUID is used deliberately to
    prove that the 403 fires at the auth layer, not at the DB layer (404).
    """
    non_existent_req_id = str(uuid.uuid4())

    r = client.patch(
        f"{BASE}/{non_existent_req_id}/status",
        json={
            "status": "DONE",
            "admin_note": "Unauthorized approval attempt by student",
        },
        headers=auth(student_token),
    )

    assert r.status_code in (401, 403), (
        f"Student must be rejected with 401 or 403 on the admin path, "
        f"got {r.status_code}: {r.text}"
    )

    # Must not be a 404 — that would mean the DB was reached before auth
    assert r.status_code != 404, (
        "Got 404, which means RBAC did not fire before DB lookup. "
        "The auth guard must run first."
    )
