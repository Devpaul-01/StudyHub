"""Profile handlers must report unexpected exceptions as 5xx with a traceback.

Catching Exception and returning 400 hid server faults from 5xx monitoring,
and logging only str(e) left Sentry events without a stack trace.
"""
import ast
import pathlib

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "routes/student/profile.py"


def test_no_handler_logs_a_bare_message_for_a_caught_exception():
    tree = ast.parse(SOURCE.read_text())
    offenders = []
    for node in ast.walk(tree):
        is_broad = (isinstance(node, ast.ExceptHandler)
                    and isinstance(node.type, ast.Name) and node.type.id == "Exception")
        if is_broad and node.name:                      # `except Exception as e`
            offenders.append(node.lineno)
    assert offenders == [], f"`except Exception as e` at lines {offenders}; use _server_error()"
