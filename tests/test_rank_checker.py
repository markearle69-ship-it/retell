from unittest.mock import MagicMock, patch

import httpx
import pytest

from app import rank_checker
from app.models import NicheTemplate, Tenant


def _tenant(**overrides) -> Tenant:
    t = Tenant(
        business_name="Joe's Plumbing",
        to_number="+15550001111",
        domain="joesplumbingsocorro.com",
        location="Socorro, Texas",
    )
    for key, value in overrides.items():
        setattr(t, key, value)
    return t


def test_build_query_uses_target_keyword_override():
    tenant = _tenant(target_keyword="emergency plumber")
    assert rank_checker.build_query(tenant) == "emergency plumber Socorro, Texas"


def test_build_query_falls_back_to_niche_service_description():
    tenant = _tenant()
    niche = NicheTemplate(name="Plumbing", service_description="plumbing repair", voice_id="v1")
    assert rank_checker.build_query(tenant, niche) == "plumbing repair Socorro, Texas"


def test_build_query_falls_back_to_business_name_without_niche():
    tenant = _tenant(location=None)
    assert rank_checker.build_query(tenant) == "Joe's Plumbing"


@pytest.mark.parametrize(
    "url,domain,matches",
    [
        ("https://www.joesplumbingsocorro.com/", "joesplumbingsocorro.com", True),
        ("http://joesplumbingsocorro.com/contact", "www.joesplumbingsocorro.com", True),
        ("https://joesplumbingsocorro.com.example.com/", "joesplumbingsocorro.com", False),
        ("https://otherplumber.com/", "joesplumbingsocorro.com", False),
    ],
)
def test_normalize_domain_matching(url, domain, matches):
    assert (rank_checker._normalize_domain(url) == rank_checker._normalize_domain(domain)) == matches


def test_check_ranking_without_domain_returns_error():
    tenant = _tenant(domain=None)
    result = rank_checker.check_ranking(tenant)
    assert result.error == "Tenant has no domain configured"
    assert result.position is None


def test_check_ranking_without_api_key_returns_error(monkeypatch):
    monkeypatch.setattr(rank_checker.settings, "serpapi_key", "")
    tenant = _tenant()
    result = rank_checker.check_ranking(tenant)
    assert result.error == "SERPAPI_KEY is not configured"


def test_check_ranking_finds_matching_domain(monkeypatch):
    monkeypatch.setattr(rank_checker.settings, "serpapi_key", "test-key")
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "organic_results": [
            {"position": 1, "link": "https://otherplumber.com/"},
            {"position": 2, "link": "https://www.joesplumbingsocorro.com/"},
        ]
    }

    with patch("app.rank_checker.httpx.get", return_value=mock_response) as mock_get:
        result = rank_checker.check_ranking(_tenant())

    assert result.position == 2
    assert result.matched_url == "https://www.joesplumbingsocorro.com/"
    assert result.num_results_checked == 2
    kwargs = mock_get.call_args.kwargs
    assert kwargs["params"]["q"] == "Joe's Plumbing Socorro, Texas"
    assert kwargs["params"]["location"] == "Socorro, Texas"


def test_check_ranking_not_found_in_results(monkeypatch):
    monkeypatch.setattr(rank_checker.settings, "serpapi_key", "test-key")
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "organic_results": [{"position": 1, "link": "https://otherplumber.com/"}]
    }

    with patch("app.rank_checker.httpx.get", return_value=mock_response):
        result = rank_checker.check_ranking(_tenant())

    assert result.position is None
    assert result.matched_url is None
    assert result.num_results_checked == 1
    assert result.error is None


def test_check_ranking_reports_http_errors(monkeypatch):
    monkeypatch.setattr(rank_checker.settings, "serpapi_key", "test-key")

    with patch("app.rank_checker.httpx.get", side_effect=httpx.ConnectError("boom")):
        result = rank_checker.check_ranking(_tenant())

    assert result.position is None
    assert "boom" in result.error


def test_check_ranking_reports_serpapi_error_payload(monkeypatch):
    monkeypatch.setattr(rank_checker.settings, "serpapi_key", "test-key")
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"error": "Invalid API key."}

    with patch("app.rank_checker.httpx.get", return_value=mock_response):
        result = rank_checker.check_ranking(_tenant())

    assert result.error == "Invalid API key."
