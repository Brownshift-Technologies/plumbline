"""Profile photo upload -- `POST/DELETE /api/auth/photo`.

The route existed only in the frontend for most of this build:
`ProfilePane.tsx` called it, no backend route answered, and because
`app/production.py` serves the SPA as a catch-all the call came back 405
rather than anything the pane could explain. Found by
`tests/test_frontend_backend_contract.py`, which is the real fix for that
whole class.
"""

import base64
import io

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _upload(client, data: bytes, filename="a.png", content_type="image/png"):
    return client.post(
        "/api/auth/photo",
        files={"photo": (filename, io.BytesIO(data), content_type)},
    )


@pytest.mark.parametrize("raw,expected", [
    (PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp"),
])
def test_each_accepted_format_round_trips_as_a_data_uri(client_as_owner, raw, expected):
    assert _upload(client_as_owner, raw).status_code == 200
    photo = client_as_owner.get("/api/auth/me").json()["photo_url"]
    assert photo.startswith(f"data:{expected};base64,")
    assert base64.b64decode(photo.split(",", 1)[1]) == raw


def test_an_svg_is_rejected_however_it_is_labelled(client_as_owner):
    """SVG renders script. A `data:image/svg+xml` URI served from our own
    origin and dropped into an <img> is stored XSS, so it is not in
    `_PHOTO_TYPES` and no header can talk the route into accepting it."""
    for content_type in ("image/svg+xml", "image/png", "image/jpeg"):
        r = _upload(client_as_owner, SVG, "x.svg", content_type)
        assert r.status_code == 400, content_type
    assert client_as_owner.get("/api/auth/me").json()["photo_url"] == ""


def test_the_stored_type_comes_from_the_bytes_not_the_clients_header(client_as_owner):
    """The security property, asserted directly.

    A client that uploads a real PNG while claiming `image/svg+xml` must
    end up with `data:image/png`, never the header it sent. Echoing a
    caller-controlled Content-Type into a data: URI is exactly how an
    <img> turns into script execution on our own origin.
    """
    assert _upload(client_as_owner, PNG, "lie.png", "image/svg+xml").status_code == 200
    photo = client_as_owner.get("/api/auth/me").json()["photo_url"]
    assert photo.startswith("data:image/png;base64,")
    assert "svg" not in photo


def test_a_photo_over_the_cap_is_refused_and_nothing_is_stored(client_as_owner):
    from app.auth_routes import MAX_PHOTO_BYTES

    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_PHOTO_BYTES
    r = _upload(client_as_owner, oversized)
    assert r.status_code == 413
    assert "KB" in r.json()["detail"]
    assert client_as_owner.get("/api/auth/me").json()["photo_url"] == ""


def test_an_empty_file_is_refused(client_as_owner):
    assert _upload(client_as_owner, b"").status_code == 400


def test_removing_a_photo_clears_it(client_as_owner):
    assert _upload(client_as_owner, PNG).status_code == 200
    assert client_as_owner.get("/api/auth/me").json()["photo_url"] != ""

    assert client_as_owner.delete("/api/auth/photo").status_code == 200
    assert client_as_owner.get("/api/auth/me").json()["photo_url"] == ""


def test_a_demo_session_is_refused_with_a_reason_not_an_error(client):
    """Consistent with every other write a sandbox cannot back: 200 with a
    reason, so the pane can say what happened."""
    client.post("/api/auth/demo")
    r = _upload(client, PNG)
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] is False
    assert "real account" in body["reason"]


def test_an_anonymous_caller_cannot_set_a_photo(client):
    assert _upload(client, PNG).status_code == 401
