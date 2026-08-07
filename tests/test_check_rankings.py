from unittest.mock import patch

from scripts import check_rankings
from app.rank_checker import RankCheckResult


def test_should_alert_on_error():
    assert check_rankings._should_alert(previous=1, result=RankCheckResult(query="q", error="boom"))


def test_should_alert_when_ranked_site_falls_off_results(monkeypatch):
    monkeypatch.setattr(check_rankings.settings, "rank_check_alert_threshold", 10)
    result = RankCheckResult(query="q", position=None, num_results_checked=100)
    assert check_rankings._should_alert(previous=5, result=result)


def test_should_alert_when_position_worsens_past_threshold(monkeypatch):
    monkeypatch.setattr(check_rankings.settings, "rank_check_alert_threshold", 10)
    result = RankCheckResult(query="q", position=25)
    assert check_rankings._should_alert(previous=8, result=result)


def test_should_not_alert_when_already_ranked_worse_than_threshold(monkeypatch):
    monkeypatch.setattr(check_rankings.settings, "rank_check_alert_threshold", 10)
    result = RankCheckResult(query="q", position=30)
    assert not check_rankings._should_alert(previous=25, result=result)


def test_should_not_alert_when_still_within_threshold(monkeypatch):
    monkeypatch.setattr(check_rankings.settings, "rank_check_alert_threshold", 10)
    result = RankCheckResult(query="q", position=4)
    assert not check_rankings._should_alert(previous=6, result=result)


def test_alert_skips_without_recipient_configured(monkeypatch, caplog):
    monkeypatch.setattr(check_rankings.settings, "rank_check_alert_email", "")
    from app.models import Tenant

    tenant = Tenant(business_name="Joe's Plumbing", to_number="+15550001111", domain="joesplumbing.com")
    with patch("app.notify.send_rank_alert") as mock_send:
        check_rankings._alert(tenant, previous=3, result=RankCheckResult(query="q", position=20))
        mock_send.assert_not_called()


def test_alert_sends_with_formatted_subject_and_body(monkeypatch):
    monkeypatch.setattr(check_rankings.settings, "rank_check_alert_email", "owner@example.com")
    from app.models import Tenant

    tenant = Tenant(business_name="Joe's Plumbing", to_number="+15550001111", domain="joesplumbing.com")
    with patch("app.notify.send_rank_alert") as mock_send:
        check_rankings._alert(tenant, previous=3, result=RankCheckResult(query="plumber austin", position=20))

        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        assert args[0] == "owner@example.com"
        assert "Joe's Plumbing" in args[1]
        assert "Previous position: 3" in args[2]
        assert "Current position: 20" in args[2]
