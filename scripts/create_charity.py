#!/usr/bin/env python
"""Create (or list) Stripe Connect accounts that serve as "charities".

The app's charity picker (/api/charities/search) lists the Connect accounts
on the platform Stripe account; a goal's pledge is transferred to the chosen
account when the goal fails. This script bootstraps one:

  # list existing connected accounts
  backend/.venv/bin/python scripts/create_charity.py --list

  # create one + print its onboarding link (recipient must complete it)
  backend/.venv/bin/python scripts/create_charity.py "My Charity" charity@example.com

Uses STRIPE_SECRET_KEY from the environment, falling back to .env in the repo
root. Works in test mode (sk_test_...) and live mode (sk_live_...); in test
mode the onboarding form accepts Stripe's magic test values (e.g. SSN 000-00-0000).
"""

import argparse
import os
import sys
from pathlib import Path

import stripe


def _load_env_key() -> str | None:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("STRIPE_SECRET_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", help="Display name for the charity")
    parser.add_argument("email", nargs="?", help="Email of the account holder")
    parser.add_argument("--list", action="store_true", help="List connected accounts")
    parser.add_argument(
        "--return-url",
        default="https://k-911-x17.porgy-boga.ts.net/",
        help="Where Stripe sends the recipient after onboarding",
    )
    args = parser.parse_args()

    key = _load_env_key()
    if not key:
        print("STRIPE_SECRET_KEY not set (env or .env)", file=sys.stderr)
        return 1
    stripe.api_key = key
    mode = "LIVE" if key.startswith("sk_live") else "TEST"
    print(f"Stripe mode: {mode}")

    if args.list:
        accounts = stripe.Account.list(limit=20)
        if not accounts.data:
            print("No connected accounts.")
        for a in accounts.data:
            name = (a.business_profile and a.business_profile.name) or "(unnamed)"
            payable = "payouts_enabled" if a.payouts_enabled else "onboarding incomplete"
            print(f"  {a.id}  {name}  [{a.type}, {payable}]")
        return 0

    if not args.name or not args.email:
        parser.error("name and email are required unless --list is given")

    account = stripe.Account.create(
        type="express",
        email=args.email,
        business_profile={"name": args.name},
        capabilities={"transfers": {"requested": True}},
        metadata={"created_by": "sacrifice create_charity.py"},
    )
    print(f"Created connected account: {account.id}  ({args.name})")

    link = stripe.AccountLink.create(
        account=account.id,
        refresh_url=args.return_url,
        return_url=args.return_url,
        type="account_onboarding",
    )
    print("\nOnboarding link (send to the recipient; expires in a few minutes,")
    print("rerun this script with --list then create a new link if needed):")
    print(f"  {link.url}")
    print(f"\nOnce onboarding completes, use charity_id={account.id} on goals,")
    print("and it will appear in the app's charity search.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
