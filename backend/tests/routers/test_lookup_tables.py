"""
CRUD tests for /api/lookup-tables.
"""
import pytest


class TestListLookupTables:
    @pytest.mark.asyncio
    async def test_empty_initially(self, async_client):
        response = await async_client.get("/api/lookup-tables")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_lists_created_tables_alphabetically(self, async_client):
        await async_client.post(
            "/api/lookup-tables",
            json={"name": "zulu", "entries": {}},
        )
        await async_client.post(
            "/api/lookup-tables",
            json={"name": "alpha", "entries": {"a": "1"}},
        )

        response = await async_client.get("/api/lookup-tables")
        body = response.json()
        assert [t["name"] for t in body] == ["alpha", "zulu"]
        # list endpoint returns entry_count without entries payload
        alpha = next(t for t in body if t["name"] == "alpha")
        assert alpha["entry_count"] == 1
        assert "entries" not in alpha


class TestCreateLookupTable:
    @pytest.mark.asyncio
    async def test_creates_with_entries(self, async_client):
        response = await async_client.post(
            "/api/lookup-tables",
            json={
                "name": "callsigns",
                "description": "Channel call signs",
                "entries": {"ESPN": "espn.com", "CNN": "cnn.com"},
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "callsigns"
        assert body["description"] == "Channel call signs"
        assert body["entries"] == {"ESPN": "espn.com", "CNN": "cnn.com"}
        assert body["entry_count"] == 2
        assert body["id"] > 0

    @pytest.mark.asyncio
    async def test_rejects_duplicate_name(self, async_client):
        await async_client.post("/api/lookup-tables", json={"name": "dupe", "entries": {}})
        response = await async_client.post(
            "/api/lookup-tables",
            json={"name": "dupe", "entries": {"a": "b"}},
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_rejects_empty_name(self, async_client):
        response = await async_client.post(
            "/api/lookup-tables",
            json={"name": "", "entries": {}},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_oversized_table(self, async_client):
        huge = {f"k{i}": f"v{i}" for i in range(10_001)}
        response = await async_client.post(
            "/api/lookup-tables",
            json={"name": "huge", "entries": huge},
        )
        assert response.status_code == 400
        assert "maximum" in response.json()["detail"].lower()


class TestGetLookupTable:
    @pytest.mark.asyncio
    async def test_returns_entries(self, async_client):
        created = (await async_client.post(
            "/api/lookup-tables",
            json={"name": "timezones", "entries": {"EST": "-5", "PST": "-8"}},
        )).json()

        response = await async_client.get(f"/api/lookup-tables/{created['id']}")
        assert response.status_code == 200
        assert response.json()["entries"] == {"EST": "-5", "PST": "-8"}

    @pytest.mark.asyncio
    async def test_missing_returns_404(self, async_client):
        response = await async_client.get("/api/lookup-tables/99999")
        assert response.status_code == 404


class TestUpdateLookupTable:
    @pytest.mark.asyncio
    async def test_patches_entries_in_place(self, async_client):
        created = (await async_client.post(
            "/api/lookup-tables",
            json={"name": "codes", "entries": {"a": "1"}},
        )).json()

        response = await async_client.patch(
            f"/api/lookup-tables/{created['id']}",
            json={"entries": {"a": "1", "b": "2"}},
        )
        assert response.status_code == 200
        assert response.json()["entries"] == {"a": "1", "b": "2"}

    @pytest.mark.asyncio
    async def test_renames_when_new_name_unique(self, async_client):
        created = (await async_client.post(
            "/api/lookup-tables",
            json={"name": "old", "entries": {}},
        )).json()

        response = await async_client.patch(
            f"/api/lookup-tables/{created['id']}",
            json={"name": "new"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "new"

    @pytest.mark.asyncio
    async def test_rename_clash_returns_409(self, async_client):
        await async_client.post("/api/lookup-tables", json={"name": "taken", "entries": {}})
        created = (await async_client.post(
            "/api/lookup-tables",
            json={"name": "other", "entries": {}},
        )).json()

        response = await async_client.patch(
            f"/api/lookup-tables/{created['id']}",
            json={"name": "taken"},
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_missing_returns_404(self, async_client):
        response = await async_client.patch(
            "/api/lookup-tables/99999",
            json={"description": "x"},
        )
        assert response.status_code == 404


class TestDeleteLookupTable:
    @pytest.mark.asyncio
    async def test_deletes_and_returns_204(self, async_client):
        created = (await async_client.post(
            "/api/lookup-tables",
            json={"name": "temp", "entries": {}},
        )).json()

        response = await async_client.delete(f"/api/lookup-tables/{created['id']}")
        assert response.status_code == 204

        followup = await async_client.get(f"/api/lookup-tables/{created['id']}")
        assert followup.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_returns_404(self, async_client):
        response = await async_client.delete("/api/lookup-tables/99999")
        assert response.status_code == 404
