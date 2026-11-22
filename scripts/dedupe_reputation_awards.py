#!/usr/bin/env python3
"""
Remove reputation that was paid out more than once for the same milestone or
solution (see services/reputation_cleanup.py for exactly what counts).

Dry run by default: prints what would change and exits.

    python scripts/dedupe_reputation_awards.py            # report only
    python scripts/dedupe_reputation_awards.py --apply    # delete + adjust totals

Take a database backup before running with --apply.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually delete the rows and adjust totals")
    args = parser.parse_args()

    from app import app
    from extensions import db
    from services import cache_service, reputation_cleanup

    with app.app_context():
        rows = reputation_cleanup.find_surplus_awards()
        per_user = reputation_cleanup.summarize(rows)
        print(f"{len(rows)} duplicate award rows across {len(per_user)} users "
              f"({sum(per_user.values())} points in total)")
        for user_id, points in sorted(per_user.items(), key=lambda kv: -kv[1])[:20]:
            print(f"  user {user_id}: -{points}")
        if len(per_user) > 20:
            print(f"  ... and {len(per_user) - 20} more users")

        if not rows:
            return 0
        if not args.apply:
            print("\nDry run. Re-run with --apply to make these changes.")
            return 0

        reputation_cleanup.apply_cleanup(rows)
        db.session.commit()
        for user_id in per_user:
            from services.reputation_service import invalidate_user_caches
            invalidate_user_caches(user_id)
        print("Applied.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
