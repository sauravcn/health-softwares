"""Health + admin UI smoke tests."""


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_admin_pages_render(client):
    for path in ["/admin", "/admin/shifts", "/admin/visits", "/admin/claims",
                 "/admin/caregivers", "/admin/workflows"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "HealthSoftwares" in r.text


def test_root_redirects_to_admin(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307)
