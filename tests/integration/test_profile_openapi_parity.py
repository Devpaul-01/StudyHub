"""The profile blueprint and openapi.yml must describe the same API.

Integrators work from openapi.yml; routes missing from it (fcm-token,
view-profile, ...) or documented routes that no longer exist both
mislead them.
"""
import re

import pytest
import yaml

# HTML shells (no JSON body) are intentionally not part of the JSON API spec.
PAGE_ROUTES = {
    "/student/profile/me",
    "/student/profile/homepage",
    "/student/profile/view/{username}",
}


def _flask_routes(app):
    routes = {}
    for rule in app.url_map.iter_rules():
        if ".student_profile." not in rule.endpoint:
            continue
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule.rule)
        routes.setdefault(path, set()).update(m.lower() for m in rule.methods - {"HEAD", "OPTIONS"})
    return routes


@pytest.fixture(scope="module")
def spec():
    with open("openapi.yml") as fh:
        return yaml.safe_load(fh)["paths"]


def test_every_profile_json_route_is_documented(app, spec):
    missing = []
    for path, methods in _flask_routes(app).items():
        if path in PAGE_ROUTES:
            continue
        documented = {m for m in spec.get(path, {}) if m in {"get", "post", "put", "patch", "delete"}}
        for method in sorted(methods - documented):
            missing.append(f"{method.upper()} {path}")
    assert missing == [], "undocumented profile routes: " + ", ".join(missing)


def test_no_documented_profile_route_is_missing_from_the_app(app, spec):
    live = _flask_routes(app)
    stale = []
    for path, item in spec.items():
        # /profile/notifications/* shares the URL prefix but belongs to the
        # notifications blueprint.
        if (not path.startswith("/student/profile/") or path in PAGE_ROUTES
                or path.startswith("/student/profile/notifications")):
            continue
        for method in item:
            if method in {"get", "post", "put", "patch", "delete"} and method not in live.get(path, set()):
                stale.append(f"{method.upper()} {path}")
    assert stale == [], "documented but not served: " + ", ".join(stale)
