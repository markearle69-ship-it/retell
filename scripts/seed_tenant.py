"""Add or update a tenant (a local biz owner / rank-and-rent site) directly in the DB.

Usage:
    python scripts/seed_tenant.py \\
        --business-name "Joe's Plumbing" \\
        --to-number +15551234567 \\
        --notify-email joe@example.com \\
        --notify-sms +15559876543

--to-number must match the "to" number Retell sees for inbound calls to that
site (the Twilio number you forward to Retell via the SIP trunk).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Tenant  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-name", required=True)
    parser.add_argument("--to-number", required=True, help="E.164 format, e.g. +15551234567")
    parser.add_argument("--notify-email")
    parser.add_argument("--notify-sms")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.to_number == args.to_number).first()
        if tenant is None:
            tenant = Tenant(to_number=args.to_number)
            db.add(tenant)

        tenant.business_name = args.business_name
        tenant.notify_email = args.notify_email
        tenant.notify_sms_number = args.notify_sms

        db.commit()
        print(f"Saved tenant id={tenant.id} business_name={tenant.business_name!r} to_number={tenant.to_number}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
