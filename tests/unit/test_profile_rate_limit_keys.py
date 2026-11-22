"""Guards the rate-limit keying of the profile routes.

services/rate_limit_service.py documents that ip_key() is for pre-auth
routes (login/register) and user_or_ip_key() must be used on every
@token_required route: students on one campus network share an IP, so an
IP-keyed bucket on logged-in routes makes them throttle each other.
"""
import ast
import pathlib

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "routes/student/profile.py"


def _limited_functions():
    tree = ast.parse(SOURCE.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "limit"):
                keys = [kw.value.id for kw in dec.keywords
                        if kw.arg == "key_func" and isinstance(kw.value, ast.Name)]
                yield node.name, keys


def test_every_profile_limit_is_keyed_by_user_not_bare_ip():
    limited = list(_limited_functions())

    assert limited, "expected rate-limited profile routes"
    bad = [name for name, keys in limited if keys != ["user_or_ip_key"]]
    assert bad == [], f"routes keyed by plain IP (or unkeyed): {bad}"
