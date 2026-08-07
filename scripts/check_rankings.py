"""Cron entry point: checks Google ranking for every site with a domain set,
records the result, and emails an alert if a site's position got worse.

Why not "run an incognito search"?
-----------------------------------
Driving a real browser (incognito or not) against google.com doesn't scale to
~70 sites on a schedule: Google rate-limits/CAPTCHA's automated queries from a
single IP within a handful of searches, incognito mode doesn't change that
(it only avoids *your* personalization, it does nothing about Google's bot
detection), and it's a Terms of Service violation to scrape results that way.
This script instead uses SerpApi (https://serpapi.com), which runs the search
server-side and returns structured JSON - the standard approach every rank
tracker (Ahrefs, SEMrush, etc.) uses under the hood. Set SERPAPI_KEY in your
.env to use it; swap `app.rank_checker._fetch_serp` for another provider's
client if you'd rather use DataForSEO/ValueSerp/etc.

Usage
-----
    python scripts/check_rankings.py                  # check every site with a domain set
    python scripts/check_rankings.py --tenant-id 5     # check just one site
    python scripts/check_rankings.py --dry-run         # print results, write nothing, alert nobody

Scheduling
----------
Run it on a schedule with whatever cron-like mechanism your deployment
already has - see the "Rank tracking" section of the README for a plain
crontab line, a GitHub Actions workflow, and Railway's cron plugin.
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import notify  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.migrations import run_additive_migrations  # noqa: E402
from app.models import RankCheck, Tenant  # noqa: E402
from app.rank_checker import RankCheckResult, check_ranking  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Be a polite API citizen between checks rather than firing 70 requests at once.
SLEEP_BETWEEN_CHECKS_SECONDS = 1.5


def _previous_position(db, tenant_id: int) -> Optional[int]:
    last = (
        db.query(RankCheck)
        .filter(RankCheck.tenant_id == tenant_id, RankCheck.error.is_(None))
        .order_by(RankCheck.checked_at.desc())
        .first()
    )
    return last.position if last else None


def _should_alert(previous: Optional[int], result: RankCheckResult) -> bool:
    """Alert when a check errors out, or when a site that was ranking within
    the threshold falls out of it (or off the results entirely). Doesn't
    alert on a site that was already ranking worse than the threshold - that
    would just repeat the same alert every run."""
    if result.error:
        return True
    was_within_threshold = previous is not None and previous <= settings.rank_check_alert_threshold
    now_outside_threshold = result.position is None or result.position > settings.rank_check_alert_threshold
    return was_within_threshold and now_outside_threshold


def _alert(tenant: Tenant, previous: Optional[int], result: RankCheckResult) -> None:
    if not settings.rank_check_alert_email:
        logger.warning(
            "Alert condition hit for %s but RANK_CHECK_ALERT_EMAIL is not set - skipping",
            tenant.business_name,
        )
        return

    if result.error:
        subject = f"[Rank check failed] {tenant.business_name}"
        body = f"Ranking check for {tenant.business_name} ({tenant.domain}) failed:\n\n{result.error}"
    else:
        subject = f"[Ranking drop] {tenant.business_name}"
        new_position = result.position if result.position is not None else f"not in top {result.num_results_checked}"
        body = (
            f"{tenant.business_name} ({tenant.domain}) dropped in Google rankings.\n\n"
            f"Query: {result.query}\n"
            f"Previous position: {previous}\n"
            f"Current position: {new_position}\n"
        )

    notify.send_rank_alert(settings.rank_check_alert_email, subject, body)


def run(tenant_id: Optional[int] = None, dry_run: bool = False) -> None:
    Base.metadata.create_all(bind=engine)
    run_additive_migrations(engine)

    db = SessionLocal()
    try:
        query = db.query(Tenant).filter(Tenant.domain.isnot(None))
        if tenant_id is not None:
            query = query.filter(Tenant.id == tenant_id)
        tenants = query.order_by(Tenant.business_name).all()

        if not tenants:
            logger.warning("No tenants with a domain configured - nothing to check")
            return

        for i, tenant in enumerate(tenants):
            previous = _previous_position(db, tenant.id)
            result = check_ranking(tenant, niche=tenant.niche_template)

            status = result.error or (
                f"position {result.position}" if result.position else f"not in top {result.num_results_checked}"
            )
            logger.info("%s (%s): %s [query=%r]", tenant.business_name, tenant.domain, status, result.query)

            if not dry_run:
                db.add(
                    RankCheck(
                        tenant_id=tenant.id,
                        query=result.query,
                        position=result.position,
                        matched_url=result.matched_url,
                        num_results_checked=result.num_results_checked,
                        error=result.error,
                    )
                )
                db.commit()

            if _should_alert(previous, result) and not dry_run:
                _alert(tenant, previous, result)

            if i < len(tenants) - 1:
                time.sleep(SLEEP_BETWEEN_CHECKS_SECONDS)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant-id", type=int, default=None, help="Check only this tenant")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print results without writing to the DB or sending alerts"
    )
    args = parser.parse_args()
    run(tenant_id=args.tenant_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
