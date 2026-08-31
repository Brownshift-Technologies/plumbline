"""Task 18's own smoke test on `/_health` -- offline, via the `client`
fixture (tests/conftest.py), so it needs no live GCP credentials and no
deployed service. `tests/test_main.py` already covers `/_health` in more
depth; these two are the brief's own literal assertions, kept here under
the name the brief gave the file.
"""


def test_health_is_not_at_healthz(client):
    assert client.get("/_health").status_code == 200


def test_health_reports_the_model_and_location(client):
    body = client.get("/_health").json()
    assert body["model"] == "gemini-3.5-flash" and body["gemini_location"] == "global"
