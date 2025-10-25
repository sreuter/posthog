from datetime import timedelta

from posthog.test.base import APIBaseTest, ClickhouseTestMixin
from unittest.mock import patch

from rest_framework import status

from posthog.warehouse.models import DataWarehouseSavedQuery

from products.endpoints.backend.api import can_materialize_endpoint
from products.endpoints.backend.models import Endpoint


class TestEndpointMaterializationValidation(ClickhouseTestMixin, APIBaseTest):
    def setUp(self):
        super().setUp()
        self.sample_hogql_query = {
            "kind": "HogQLQuery",
            "query": "SELECT user_id, count(*) FROM events GROUP BY user_id",
        }

    def test_hogql_query_can_materialize(self):
        """HogQL query without variables should be materializable."""
        endpoint = Endpoint(query=self.sample_hogql_query)
        can_mat, reason = can_materialize_endpoint(endpoint)
        assert can_mat is True
        assert reason == ""

    def test_trends_query_cannot_materialize(self):
        """Trends query should not be materializable."""
        endpoint = Endpoint(query={"kind": "TrendsQuery", "series": [{"event": "pageview"}]})
        can_mat, reason = can_materialize_endpoint(endpoint)
        assert can_mat is False
        assert "Only HogQL queries" in reason

    def test_hogql_with_variables_cannot_materialize(self):
        """HogQL query with variables should not be materializable (MVP)."""
        endpoint = Endpoint(
            query={
                "kind": "HogQLQuery",
                "query": "SELECT * FROM events WHERE event = {event_name}",
                "variables": {"event_name": {"code_name": "event_name", "value": "pageview"}},
            }
        )
        can_mat, reason = can_materialize_endpoint(endpoint)
        assert can_mat is False
        assert "variables" in reason

    def test_empty_query_cannot_materialize(self):
        """Empty or missing query should not be materializable."""
        endpoint = Endpoint(query={"kind": "HogQLQuery", "query": ""})
        can_mat, reason = can_materialize_endpoint(endpoint)
        assert can_mat is False
        assert "empty" in reason.lower()

    def test_non_string_query_cannot_materialize(self):
        """Non-string query should not be materializable."""
        endpoint = Endpoint(query={"kind": "HogQLQuery", "query": None})
        can_mat, reason = can_materialize_endpoint(endpoint)
        assert can_mat is False


class TestEndpointMaterializeAction(ClickhouseTestMixin, APIBaseTest):
    def setUp(self):
        super().setUp()
        self.sample_hogql_query = {
            "kind": "HogQLQuery",
            "query": "SELECT user_id, count(*) FROM events GROUP BY user_id",
        }

    @patch("posthog.warehouse.data_load.saved_query_service.trigger_saved_query_schedule")
    def test_materialize_hogql_endpoint_creates_saved_query(self, mock_trigger):
        """Materializing HogQL endpoint should create DataWarehouseSavedQuery."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="test_endpoint", query=self.sample_hogql_query, created_by=self.user
        )

        response = self.client.post(
            f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/materialize",
            data={"sync_frequency": "daily"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        endpoint.refresh_from_db()
        assert endpoint.is_materialized is True
        assert endpoint.materialized_query is not None
        assert endpoint.materialized_query.name == f"__endpoint_team_{endpoint.team.id}_{endpoint.name}"
        assert endpoint.materialized_query.query == endpoint.query
        assert endpoint.materialized_query.is_materialized is True
        assert mock_trigger.called

    def test_materialize_already_materialized_fails(self):
        """Materializing already-materialized endpoint should fail."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="test_endpoint", query=self.sample_hogql_query, created_by=self.user
        )
        saved_query = DataWarehouseSavedQuery.objects.create(
            team=self.team, name=f"endpoint_{endpoint.id}", query={"query": "SELECT 1"}
        )
        endpoint.materialized_query = saved_query
        endpoint.is_materialized = True
        endpoint.save()

        response = self.client.post(
            f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/materialize",
            data={"sync_frequency": "daily"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already materialized" in response.json()["detail"].lower()

    def test_materialize_trends_query_fails(self):
        """Materializing Trends query should fail with clear error."""
        endpoint = Endpoint.objects.create(
            team=self.team,
            name="test_endpoint",
            query={"kind": "TrendsQuery", "series": [{"event": "pageview"}]},
            created_by=self.user,
        )

        response = self.client.post(
            f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/materialize", format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Only HogQL queries" in response.json()["detail"]

    def test_materialize_with_variables_fails(self):
        """Materializing query with variables should fail."""
        endpoint = Endpoint.objects.create(
            team=self.team,
            name="test_endpoint",
            query={
                "kind": "HogQLQuery",
                "query": "SELECT * FROM events WHERE event = {evt}",
                "variables": {"evt": {"code_name": "evt", "value": "pageview"}},
            },
            created_by=self.user,
        )

        response = self.client.post(
            f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/materialize", format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "variables" in response.json()["detail"].lower()

    def test_materialize_sync_frequency_options(self):
        """Test different sync frequency options."""
        test_cases = [
            ("hourly", timedelta(hours=1)),
            ("daily", timedelta(days=1)),
            ("weekly", timedelta(weeks=1)),
        ]

        for freq_str, expected_delta in test_cases:
            endpoint = Endpoint.objects.create(
                team=self.team, name=f"test_{freq_str}", query=self.sample_hogql_query, created_by=self.user
            )

            with patch("posthog.warehouse.data_load.saved_query_service.trigger_saved_query_schedule"):
                response = self.client.post(
                    f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/materialize",
                    data={"sync_frequency": freq_str},
                    format="json",
                )

            assert response.status_code == status.HTTP_200_OK
            endpoint.refresh_from_db()
            assert endpoint.materialized_query.sync_frequency_interval == expected_delta

    def test_materialize_invalid_sync_frequency_fails(self):
        """Invalid sync frequency should fail."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="test_endpoint", query=self.sample_hogql_query, created_by=self.user
        )

        response = self.client.post(
            f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/materialize",
            data={"sync_frequency": "invalid"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("posthog.warehouse.data_load.saved_query_service.trigger_saved_query_schedule")
    def test_materialize_creates_sql_safe_table_name(self, mock_trigger):
        """Table name should only contain alphanumeric and underscores."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="my-endpoint-123", query=self.sample_hogql_query, created_by=self.user
        )

        response = self.client.post(
            f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/materialize",
            data={"sync_frequency": "daily"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        endpoint.refresh_from_db()

        # Verify table name is SQL-safe (hyphens replaced with underscores)
        expected_name = f"__endpoint_team_{self.team.id}_my_endpoint_123"
        assert endpoint.materialized_query.name == expected_name

        # Verify no hyphens in the name
        assert "-" not in endpoint.materialized_query.name

        # Verify only valid characters
        import re

        assert re.match(r"^[a-zA-Z0-9_]+$", endpoint.materialized_query.name)


class TestEndpointUnmaterializeAction(ClickhouseTestMixin, APIBaseTest):
    def setUp(self):
        super().setUp()
        self.sample_hogql_query = {
            "kind": "HogQLQuery",
            "query": "SELECT user_id, count(*) FROM events GROUP BY user_id",
        }

    def test_unmaterialize_removes_saved_query(self):
        """Unmaterializing should delete DataWarehouseSavedQuery."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="test_endpoint", query=self.sample_hogql_query, created_by=self.user
        )
        saved_query = DataWarehouseSavedQuery.objects.create(
            team=self.team, name=f"endpoint_{endpoint.id}", query={"query": "SELECT 1"}
        )
        endpoint.materialized_query = saved_query
        endpoint.is_materialized = True
        endpoint.save()

        saved_query_id = saved_query.id

        response = self.client.post(f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/unmaterialize")

        assert response.status_code == status.HTTP_200_OK
        endpoint.refresh_from_db()
        assert endpoint.is_materialized is False
        assert endpoint.materialized_query is None
        assert not DataWarehouseSavedQuery.objects.filter(id=saved_query_id).exists()

    def test_unmaterialize_not_materialized_fails(self):
        """Unmaterializing non-materialized endpoint should fail."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="test_endpoint", query=self.sample_hogql_query, created_by=self.user
        )

        response = self.client.post(f"/api/environments/{self.team.id}/endpoints/{endpoint.name}/unmaterialize")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not materialized" in response.json()["detail"].lower()


class TestEndpointDeletionCleanup(ClickhouseTestMixin, APIBaseTest):
    def setUp(self):
        super().setUp()
        self.sample_hogql_query = {
            "kind": "HogQLQuery",
            "query": "SELECT user_id, count(*) FROM events GROUP BY user_id",
        }

    def test_deleting_materialized_endpoint_deletes_saved_query(self):
        """Deleting materialized endpoint should delete saved query."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="test_endpoint", query=self.sample_hogql_query, created_by=self.user
        )
        saved_query = DataWarehouseSavedQuery.objects.create(
            team=self.team, name=f"endpoint_{endpoint.id}", query={"query": "SELECT 1"}
        )
        endpoint.materialized_query = saved_query
        endpoint.is_materialized = True
        endpoint.save()

        saved_query_id = saved_query.id

        response = self.client.delete(f"/api/environments/{self.team.id}/endpoints/{endpoint.name}")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Endpoint.objects.filter(id=endpoint.id).exists()
        assert not DataWarehouseSavedQuery.objects.filter(id=saved_query_id).exists()

    def test_deleting_non_materialized_endpoint_succeeds(self):
        """Deleting non-materialized endpoint should work normally."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="test_endpoint", query=self.sample_hogql_query, created_by=self.user
        )

        response = self.client.delete(f"/api/environments/{self.team.id}/endpoints/{endpoint.name}")

        assert response.status_code == status.HTTP_204_NO_CONTENT


class TestEndpointMaterializationInfo(ClickhouseTestMixin, APIBaseTest):
    def setUp(self):
        super().setUp()
        self.sample_hogql_query = {
            "kind": "HogQLQuery",
            "query": "SELECT user_id, count(*) FROM events GROUP BY user_id",
        }

    def test_list_includes_materialization_status(self):
        """List should include materialization info."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="mat_endpoint", query=self.sample_hogql_query, created_by=self.user
        )
        saved_query = DataWarehouseSavedQuery.objects.create(
            team=self.team, name=f"endpoint_{endpoint.id}", query={"query": "SELECT 1"}, status="Completed"
        )
        endpoint.materialized_query = saved_query
        endpoint.is_materialized = True
        endpoint.save()

        non_mat_endpoint = Endpoint.objects.create(
            team=self.team, name="non_mat", query=self.sample_hogql_query, created_by=self.user
        )

        response = self.client.get(f"/api/environments/{self.team.id}/endpoints")

        assert response.status_code == status.HTTP_200_OK
        results = response.json()["results"]

        mat_result = next(r for r in results if r["id"] == str(endpoint.id))
        non_mat_result = next(r for r in results if r["id"] == str(non_mat_endpoint.id))

        assert mat_result["is_materialized"] is True
        assert mat_result["materialization"]["status"] == "Completed"
        assert mat_result["materialization"]["can_materialize"] is True

        assert non_mat_result["is_materialized"] is False
        assert non_mat_result["materialization"]["can_materialize"] is True

    def test_list_shows_non_materializable_reason(self):
        """List should show reason for non-materializable endpoints."""
        trends_endpoint = Endpoint.objects.create(
            team=self.team,
            name="trends",
            query={"kind": "TrendsQuery", "series": []},
            created_by=self.user,
        )

        response = self.client.get(f"/api/environments/{self.team.id}/endpoints")

        results = response.json()["results"]
        trends_result = next(r for r in results if r["id"] == str(trends_endpoint.id))

        assert trends_result["materialization"]["can_materialize"] is False
        assert "Only HogQL" in trends_result["materialization"]["reason"]

    def test_retrieve_includes_full_materialization_details(self):
        """Retrieve should include complete materialization details."""
        endpoint = Endpoint.objects.create(
            team=self.team, name="test_endpoint", query=self.sample_hogql_query, created_by=self.user
        )
        saved_query = DataWarehouseSavedQuery.objects.create(
            team=self.team, name=f"endpoint_{endpoint.id}", query={"query": "SELECT 1"}, status="Completed"
        )
        endpoint.materialized_query = saved_query
        endpoint.is_materialized = True
        endpoint.save()

        response = self.client.get(f"/api/environments/{self.team.id}/endpoints/{endpoint.name}")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert data["is_materialized"] is True
        assert data["materialization"]["status"] == "Completed"
        assert data["materialization"]["error"] == ""
