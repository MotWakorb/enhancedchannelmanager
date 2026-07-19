"""E2E: the harness's journal rows carry the automation marker (bead uliyr).

The ``e2e_client`` fixture sends ``X-ECM-Automated-Client`` on every request,
so churn this suite creates (rule create/delete pairs) is self-declared
automated and eligible for the Journal Noise Purge task — while operator
rows (no header) are kept. This test proves the full path live: harness
header → actor-source middleware → journal row → GET /api/journal.
"""
PIPELINE = "/api/channel-pipeline"


class TestJournalAutomationMarker:
    def test_rule_churn_rows_are_marked_automated(self, e2e_client):
        """Create + delete a rule via the harness; both journal rows must
        read back automated_client=True through the journal API."""
        rule_name = "E2E automation marker probe (uliyr)"
        payload = {
            "name": rule_name,
            "description": (
                "Created by tests/e2e/test_journal_automation_marker.py — "
                "deleted in the same test"
            ),
            "enabled": False,
            "conditions": [{"type": "always"}],
            "actions": [{"type": "skip"}],
        }
        resp = e2e_client.post(f"{PIPELINE}/rules", json=payload)
        assert resp.status_code == 200, (
            f"rule create failed: {resp.status_code} {resp.text[:500]}"
        )
        rule = resp.json()
        try:
            create_marked = self._journal_rows_marked(
                e2e_client, rule_name, "create"
            )
        finally:
            del_resp = e2e_client.delete(f"{PIPELINE}/rules/{rule['id']}")
            assert del_resp.status_code == 200

        delete_marked = self._journal_rows_marked(e2e_client, rule_name, "delete")

        assert create_marked, (
            "rule-create journal row missing or not marked automated_client=True"
        )
        assert delete_marked, (
            "rule-delete journal row missing or not marked automated_client=True"
        )

    @staticmethod
    def _journal_rows_marked(e2e_client, entity_name: str, action_type: str) -> bool:
        """True when the newest matching journal row is marked automated."""
        resp = e2e_client.get(
            "/api/journal",
            params={
                "category": "auto_creation",
                "action_type": action_type,
                "search": entity_name,
                "page_size": 5,
            },
        )
        assert resp.status_code == 200
        results = resp.json().get("results", [])
        rows = [r for r in results if r.get("entity_name") == entity_name]
        return bool(rows) and rows[0].get("automated_client") is True
