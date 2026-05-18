"""
End-to-end pytest suite for deal-service.
Each test gets an isolated temporary storage file so tests never interfere.
Company-service HTTP calls are mocked via pytest monkeypatch so tests run
without a real company-service dependency.
"""
import base64
import json
import os
import tempfile
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.schemas.deal import Company

# ---------------------------------------------------------------------------
# Seed data written into every fresh temp storage file
# ---------------------------------------------------------------------------
SEED_DEALS = [
    {
        "id": "deal-001",
        "title": "Enterprise Software License",
        "amount": 50000.0,
        "status": "Open",
        "company_id": "1",
        "is_active": True,
        "created_at": "2024-01-15T09:00:00+00:00",
        "updated_at": "2024-01-15T09:00:00+00:00",
    },
    {
        "id": "deal-002",
        "title": "Cloud Migration Project",
        "amount": 120000.0,
        "status": "Won",
        "company_id": "2",
        "is_active": True,
        "created_at": "2024-02-20T11:30:00+00:00",
        "updated_at": "2024-03-01T14:00:00+00:00",
    },
    {
        "id": "deal-003",
        "title": "Annual Support Contract",
        "amount": 18000.0,
        "status": "Closed",
        "company_id": "1",
        "is_active": True,
        "created_at": "2024-03-05T08:00:00+00:00",
        "updated_at": "2024-03-10T10:00:00+00:00",
    },
]

# ---------------------------------------------------------------------------
# Fake company data returned by the mocked company-service client
# ---------------------------------------------------------------------------
FAKE_COMPANIES: dict[str, Company] = {
    "1": Company(id="1", name="Acme Corp", industry="Software"),
    "2": Company(id="2", name="Globex Inc", industry="Cloud"),
    "3": Company(id="3", name="Initech", industry="Finance"),
    "10": Company(id="10", name="Umbrella Ltd", industry="Healthcare"),
}


def _fake_get_company(company_id: str):
    return FAKE_COMPANIES.get(company_id)


async def _async_fake_get_company(company_id: str):
    return _fake_get_company(company_id)


async def _async_fake_get_companies(company_ids: list[str]):
    return {cid: FAKE_COMPANIES[cid] for cid in company_ids if cid in FAKE_COMPANIES}


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _make_claims_header(groups: list[str]) -> str:
    """Return a x-cognito-claims header value for the given Cognito groups."""
    claims = {"cognito:groups": groups, "sub": "test-user"}
    return json.dumps(claims)


USER_HEADERS = {"x-cognito-claims": _make_claims_header(["READ_USER"])}
ADMIN_HEADERS = {"x-cognito-claims": _make_claims_header(["WRITE_USER"])}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Spin up a fresh temp storage file, point the app's settings at it,
    mock company-service calls, then yield an AsyncClient backed by the ASGI app.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(SEED_DEALS, f)

        from app.services import config  # noqa: PLC0415
        original_path = config.settings.STORAGE_FILE_PATH
        config.settings.STORAGE_FILE_PATH = tmp_path

        with patch(
            "app.services.company_service.get_company",
            side_effect=_async_fake_get_company,
        ), patch(
            "app.services.company_service.get_companies",
            side_effect=_async_fake_get_companies,
        ):
            from main import app  # noqa: PLC0415
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                yield ac

        config.settings.STORAGE_FILE_PATH = original_path
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Health check (no auth required)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "deal-service"


@pytest.mark.asyncio
async def test_health_deals_path(client: AsyncClient) -> None:
    r = await client.get("/deals/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_auth_returns_401(client: AsyncClient) -> None:
    r = await client.get("/deals")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_user_cannot_delete(client: AsyncClient) -> None:
    r = await client.delete("/deals/deal-001", headers=USER_HEADERS)
    assert r.status_code == 403
    assert r.json()["error"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_admin_can_delete(client: AsyncClient) -> None:
    r = await client.delete("/deals/deal-001", headers=ADMIN_HEADERS)
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_unknown_role_returns_403(client: AsyncClient) -> None:
    headers = {"x-cognito-claims": json.dumps({"cognito:groups": ["manager"], "sub": "x"})}
    r = await client.get("/deals", headers=headers)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /deals — list all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_deals_returns_all(client: AsyncClient) -> None:
    r = await client.get("/deals", headers=USER_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 3


@pytest.mark.asyncio
async def test_list_deals_schema(client: AsyncClient) -> None:
    r = await client.get("/deals", headers=USER_HEADERS)
    deal = r.json()[0]
    for field in ("id", "title", "amount", "status", "company_id", "is_active", "created_at", "updated_at"):
        assert field in deal, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_deals_company_enriched(client: AsyncClient) -> None:
    r = await client.get("/deals", headers=USER_HEADERS)
    assert r.status_code == 200
    for deal in r.json():
        assert "company" in deal
        if deal["company_id"] in FAKE_COMPANIES:
            assert deal["company"] is not None
            assert deal["company"]["id"] == deal["company_id"]


# ---------------------------------------------------------------------------
# GET /deals?company_id= — filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_filter_by_company_id(client: AsyncClient) -> None:
    r = await client.get("/deals?company_id=1", headers=USER_HEADERS)
    assert r.status_code == 200
    deals = r.json()
    assert len(deals) == 2
    assert all(d["company_id"] == "1" for d in deals)


@pytest.mark.asyncio
async def test_filter_by_company_id_single(client: AsyncClient) -> None:
    r = await client.get("/deals?company_id=2", headers=USER_HEADERS)
    assert r.status_code == 200
    deals = r.json()
    assert len(deals) == 1
    assert deals[0]["id"] == "deal-002"


@pytest.mark.asyncio
async def test_filter_by_nonexistent_company_returns_empty(client: AsyncClient) -> None:
    r = await client.get("/deals?company_id=999", headers=USER_HEADERS)
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# GET /deals/{deal_id} — single deal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_deal_by_id(client: AsyncClient) -> None:
    r = await client.get("/deals/deal-001", headers=USER_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "deal-001"
    assert data["title"] == "Enterprise Software License"
    assert data["amount"] == 50000.0
    assert data["status"] == "Open"
    assert data["company_id"] == "1"


@pytest.mark.asyncio
async def test_get_deal_by_id_company_enriched(client: AsyncClient) -> None:
    r = await client.get("/deals/deal-001", headers=USER_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["company"] is not None
    assert data["company"]["id"] == "1"
    assert data["company"]["name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_get_deal_not_found(client: AsyncClient) -> None:
    r = await client.get("/deals/nonexistent-id", headers=USER_HEADERS)
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "RESOURCE_NOT_FOUND"
    assert "nonexistent-id" in body["message"]


# ---------------------------------------------------------------------------
# POST /deals — create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_deal(client: AsyncClient) -> None:
    payload = {
        "title": "New Partnership Deal",
        "amount": 75000.0,
        "status": "Open",
        "company_id": "3",
    }
    r = await client.post("/deals", json=payload, headers=USER_HEADERS)
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == payload["title"]
    assert data["amount"] == payload["amount"]
    assert data["status"] == payload["status"]
    assert data["company_id"] == payload["company_id"]
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_deal_company_enriched(client: AsyncClient) -> None:
    payload = {"title": "Enriched Deal", "amount": 5000.0, "status": "Open", "company_id": "3"}
    r = await client.post("/deals", json=payload, headers=USER_HEADERS)
    assert r.status_code == 201
    data = r.json()
    assert data["company"] is not None
    assert data["company"]["name"] == "Initech"


@pytest.mark.asyncio
async def test_create_deal_persisted(client: AsyncClient) -> None:
    payload = {"title": "Persisted Deal", "amount": 1000.0, "status": "Open", "company_id": "1"}
    create_r = await client.post("/deals", json=payload, headers=USER_HEADERS)
    new_id = create_r.json()["id"]

    get_r = await client.get(f"/deals/{new_id}", headers=USER_HEADERS)
    assert get_r.status_code == 200
    assert get_r.json()["title"] == "Persisted Deal"


@pytest.mark.asyncio
async def test_create_deal_increments_list(client: AsyncClient) -> None:
    before = len((await client.get("/deals", headers=USER_HEADERS)).json())
    await client.post("/deals", json={"title": "X", "amount": 1.0, "status": "Open", "company_id": "1"}, headers=USER_HEADERS)
    after = len((await client.get("/deals", headers=USER_HEADERS)).json())
    assert after == before + 1


@pytest.mark.asyncio
async def test_create_deal_missing_field_returns_422(client: AsyncClient) -> None:
    r = await client.post("/deals", json={"title": "Bad Deal", "amount": 100.0, "status": "Open"}, headers=USER_HEADERS)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_deal_wrong_type_returns_422(client: AsyncClient) -> None:
    r = await client.post(
        "/deals",
        json={"title": "Bad", "amount": "not-a-number", "status": "Open", "company_id": "1"},
        headers=USER_HEADERS,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# PUT /deals/{deal_id} — update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_deal_status(client: AsyncClient) -> None:
    r = await client.put("/deals/deal-001", json={"status": "Won"}, headers=USER_HEADERS)
    assert r.status_code == 200
    assert r.json()["status"] == "Won"


@pytest.mark.asyncio
async def test_update_deal_partial(client: AsyncClient) -> None:
    r = await client.put("/deals/deal-002", json={"title": "Updated Title", "amount": 999.99}, headers=USER_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Updated Title"
    assert data["amount"] == 999.99
    assert data["company_id"] == "2"
    assert data["status"] == "Won"


@pytest.mark.asyncio
async def test_update_deal_persisted(client: AsyncClient) -> None:
    await client.put("/deals/deal-003", json={"status": "Lost"}, headers=USER_HEADERS)
    r = await client.get("/deals/deal-003", headers=USER_HEADERS)
    assert r.json()["status"] == "Lost"


@pytest.mark.asyncio
async def test_update_deal_not_found(client: AsyncClient) -> None:
    r = await client.put("/deals/ghost-deal", json={"status": "Lost"}, headers=USER_HEADERS)
    assert r.status_code == 404
    assert r.json()["error"] == "RESOURCE_NOT_FOUND"


# ---------------------------------------------------------------------------
# DELETE /deals/{deal_id} — soft-delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_deal(client: AsyncClient) -> None:
    r = await client.delete("/deals/deal-001", headers=ADMIN_HEADERS)
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_deal_soft_removes_from_list(client: AsyncClient) -> None:
    await client.delete("/deals/deal-001", headers=ADMIN_HEADERS)
    r = await client.get("/deals", headers=USER_HEADERS)
    ids = [d["id"] for d in r.json()]
    assert "deal-001" not in ids
    assert len(ids) == 2


@pytest.mark.asyncio
async def test_delete_deal_then_get_returns_404(client: AsyncClient) -> None:
    await client.delete("/deals/deal-002", headers=ADMIN_HEADERS)
    r = await client.get("/deals/deal-002", headers=USER_HEADERS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_deal_not_found(client: AsyncClient) -> None:
    r = await client.delete("/deals/does-not-exist", headers=ADMIN_HEADERS)
    assert r.status_code == 404
    assert r.json()["error"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_deal_is_active_false_in_storage(client: AsyncClient) -> None:
    """Verify the record is kept in storage but marked is_active=False (soft-delete)."""
    from app.services import config, storage_service  # noqa: PLC0415

    await client.delete("/deals/deal-001", headers=ADMIN_HEADERS)
    raw = await storage_service.read_deals()
    deal = next((d for d in raw if d["id"] == "deal-001"), None)
    assert deal is not None, "Record should still exist in storage after soft-delete"
    assert deal["is_active"] is False


# ---------------------------------------------------------------------------
# Full lifecycle: create → read → update → delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_lifecycle(client: AsyncClient) -> None:
    # Create (user)
    create_r = await client.post(
        "/deals",
        json={"title": "Lifecycle Deal", "amount": 5000.0, "status": "Open", "company_id": "10"},
        headers=USER_HEADERS,
    )
    assert create_r.status_code == 201
    deal_id = create_r.json()["id"]
    assert create_r.json()["is_active"] is True

    # Read (user)
    get_r = await client.get(f"/deals/{deal_id}", headers=USER_HEADERS)
    assert get_r.status_code == 200
    assert get_r.json()["title"] == "Lifecycle Deal"

    # Filter by company (user)
    filter_r = await client.get("/deals?company_id=10", headers=USER_HEADERS)
    assert any(d["id"] == deal_id for d in filter_r.json())

    # Update (user)
    update_r = await client.put(f"/deals/{deal_id}", json={"status": "Closed", "amount": 4500.0}, headers=USER_HEADERS)
    assert update_r.status_code == 200
    assert update_r.json()["status"] == "Closed"
    assert update_r.json()["amount"] == 4500.0

    # Delete (WRITE_USER only — soft-delete)
    del_r = await client.delete(f"/deals/{deal_id}", headers=ADMIN_HEADERS)
    assert del_r.status_code == 204

    # Confirm gone from active list (user)
    gone_r = await client.get(f"/deals/{deal_id}", headers=USER_HEADERS)
    assert gone_r.status_code == 404
