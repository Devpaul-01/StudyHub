"""
Markup the notification JS depends on must exist in the template that is
actually served (audit NOTIF-03: the badge element lived only in an unused
template, so the unread badge never appeared on the main page).
"""
import re
from pathlib import Path

FEED = Path(__file__).resolve().parents[2] / "templates" / "post" / "feed2.html"


def test_notifications_nav_item_has_an_unread_badge():
    html = FEED.read_text(encoding="utf-8")
    nav_item = re.search(
        r'<div class="nav-item"[^>]*data-target="notifications"[^>]*>(.*?)</div>', html, re.S
    )
    assert nav_item, "Notifications nav item not found in feed2.html"
    assert "data-notification-badge" in nav_item.group(1)
