"""Guards the rate-limit keying of the leaderboard routes.

ip_key() is for pre-auth routes. Every @token_required leaderboard route must
use user_or_ip_key(), otherwise students sharing a campus IP throttle each
other (a leaderboard page open issues 4-5 requests).
"""
import ast
import pathlib

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "routes/student/leaderboard.py"


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


def _route_functions():
    tree = ast.parse(SOURCE.read_text())
    return {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "route"
                for d in node.decorator_list)
    }


def test_every_leaderboard_limit_is_keyed_by_user_not_bare_ip():
    limited = list(_limited_functions())
    assert limited, "expected rate-limited leaderboard routes"
    bad = [name for name, keys in limited if keys != ["user_or_ip_key"]]
    assert bad == [], f"routes keyed by plain IP (or unkeyed): {bad}"


def test_every_leaderboard_route_has_a_rate_limit():
    limited = {name for name, _ in _limited_functions()}
    missing = sorted(_route_functions() - limited)
    assert missing == [], f"routes without a rate limit: {missing}"
