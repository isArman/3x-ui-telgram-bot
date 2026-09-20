#!/usr/bin/env python3
"""Enforce limitIp=1 on every 3x-ui client (existing subscriptions).

Usage (inside bot container or with PYTHONPATH=/app):
  python scripts/enforce_limit_ip.py --dry-run
  python scripts/enforce_limit_ip.py
  python scripts/enforce_limit_ip.py --email someuser
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from app.database.session import AsyncSessionLocal
from app.services.panel_settings import get_panel_settings, xui_client_for_panel
from app.xui.client import DEFAULT_CLIENT_LIMIT_IP, XUIError


def _limit_hist(clients: list[dict]) -> Counter:
    hist: Counter = Counter()
    for row in clients:
        hist[int(row.get("limitIp") or 0)] += 1
    return hist


async def run(*, dry_run: bool, only_email: str | None) -> int:
    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        if not ps.is_verified:
            print("Panel not verified; aborting.", file=sys.stderr)
            return 2

        async with xui_client_for_panel(ps) as client:
            clients = await client.list_clients()
            print(f"clients_total={len(clients)} limitIp_hist={dict(_limit_hist(clients))}")

            targets = []
            for row in clients:
                email = (row.get("email") or "").strip()
                if not email:
                    continue
                if only_email and email != only_email:
                    continue
                current = int(row.get("limitIp") or 0)
                if current != DEFAULT_CLIENT_LIMIT_IP:
                    targets.append((email, current))

            print(
                f"need_update={len(targets)} target_limitIp={DEFAULT_CLIENT_LIMIT_IP}"
            )
            if dry_run:
                for email, current in targets[:20]:
                    print(f"  would_set {email!r}: {current} -> {DEFAULT_CLIENT_LIMIT_IP}")
                if len(targets) > 20:
                    print(f"  ... and {len(targets) - 20} more")
                return 0

            ok = 0
            fail = 0
            for email, current in targets:
                try:
                    detail = await client.enforce_limit_ip(email)
                    new_lim = int((detail.get("client") or {}).get("limitIp") or -1)
                    if new_lim != DEFAULT_CLIENT_LIMIT_IP:
                        raise XUIError(
                            f"update returned limitIp={new_lim}, expected {DEFAULT_CLIENT_LIMIT_IP}"
                        )
                    ok += 1
                    print(f"OK {email}: {current} -> {new_lim}")
                except Exception as exc:
                    fail += 1
                    print(f"FAIL {email}: {exc}", file=sys.stderr)

            # verify
            after = await client.list_clients()
            print(
                f"done ok={ok} fail={fail} "
                f"limitIp_hist={dict(_limit_hist(after))}"
            )
            bad = [
                c.get("email")
                for c in after
                if int(c.get("limitIp") or 0) != DEFAULT_CLIENT_LIMIT_IP
                and (not only_email or c.get("email") == only_email)
            ]
            if bad and not only_email:
                print(f"still_not_1={len(bad)} sample={bad[:10]}", file=sys.stderr)
                return 1
            if only_email and bad:
                print(f"target still wrong: {bad}", file=sys.stderr)
                return 1
            return 0 if fail == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--email", default=None, help="Only update this client email")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(dry_run=args.dry_run, only_email=args.email)))


if __name__ == "__main__":
    main()
