from unittest.mock import MagicMock, patch

import pytest
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ChangeError, Service
from ops.testing import Harness
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed

from charm import ZenMLCharm

EXPECTED_SERVICE = {
    "zenml-server": Service(
        "zenmo-server",
        raw={
            "summary": "Entrypoint of zenml-server image",
            "startup": "enabled",
            "override": "replace",
            "command": "uvicorn zenml.zen_server.zen_server_api:app --log-level debug --proxy-headers --port 8080 --host 0.0.0.0",  # noqa: E501
            "environment": {"ZENML_STORE_TYPE": "test"},
        },
    )
}

CHARM_NAME = "zenml-server"

RELATIONAL_DB_DATA = {
    "database": "database",
    "host": "host",
    "username": "username",
    "password": "password",
    "port": "port",
}

EXPECTED_ENVIRONMENT = {
    "ZENML_STORE_TYPE": "sql",
    "ZENML_STORE_URL": "mysql://username:password@host:port/zenml",
    "DISABLE_DATABASE_MIGRATION": "True",
    "ZENML_STORE_SSL_VERIFY_SERVER_CERT": "false",
    "ZENML_SERVER_DEPLOYMENT_TYPE": "kubernetes",
    "ZENML_DEFAULT_PROJECT_NAME": "default",
    "ZENML_DEFAULT_USER_NAME": "default",
    "ZENML_LOGGING_VERBOSITY": "DEBUG",
}


class _FakeChangeError(ChangeError):
    """Used to simulate a ChangeError during testing."""

    def __init__(self, err, change):
        super().__init__(err, change)


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""

    harness = Harness(ZenMLCharm)

    # setup container networking simulation
    harness.set_can_connect("zenml-server", True)

    return harness


class TestCharm:
    """Test class for TrainingOperatorCharm."""

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_check_leader_failure(self, harness: Harness):
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_check_leader_success(self, harness: Harness):
        harness.set_leader(True)
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status != WaitingStatus("Waiting for leadership")

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def tests_on_pebble_ready_failure(self):
        harness = Harness(ZenMLCharm)
        harness.set_can_connect("zenml-server", False)
        harness.begin()
        with pytest.raises(ErrorWithStatus):
            harness.charm._on_pebble_ready(None)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def tests_on_pebble_ready_success(self, harness: Harness):
        harness.begin()
        harness.charm._on_event = MagicMock()
        harness.charm._on_pebble_ready(None)
        harness.charm._on_event.assert_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.get_interfaces")
    def test_get_interfaces_failure_no_versions_listed(
        self, get_interfaces: MagicMock, harness: Harness
    ):
        relation = MagicMock()
        relation.name = "A"
        relation.id = "1"
        get_interfaces.side_effect = NoVersionsListed(relation)
        harness.begin()
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_interfaces()

        assert e_info.value.status_type(WaitingStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.get_interfaces")
    def test_get_interfaces_failure_no_compatible_versions(
        self, get_interfaces: MagicMock, harness: Harness
    ):
        relation_error = MagicMock()
        relation_error.name = "A"
        relation_error.id = "1"
        get_interfaces.side_effect = NoCompatibleVersions(relation_error, [], [])
        harness.begin()
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_interfaces()

        assert e_info.value.status_type(BlockedStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_relational_db_data_success(self, harness: Harness):
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {
            "test-db-data": {
                "endpoints": "host:port",
                "username": "username",
                "password": "password",
            }
        }
        database.fetch_relation_data = fetch_relation_data
        harness.model.get_relation = MagicMock()
        harness.begin()
        harness.charm.database = database
        res = harness.charm._get_relational_db_data()
        assert res == {
            "host": "host",
            "password": "password",
            "port": "port",
            "username": "username",
        }

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_relational_db_data_failure_wrong_data(self, harness: Harness):
        """Test with missing username and password in databag"""
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {"test-db-data": {"endpoints": "host:port"}}
        database.fetch_relation_data = fetch_relation_data
        harness.model.get_relation = MagicMock()
        harness.begin()
        harness.charm.database = database
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_relational_db_data()
        assert e_info.value.status_type(WaitingStatus)
        assert "Incorrect data found in relation relational-db" in str(e_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_relational_db_data_failure_waiting(self, harness: Harness):
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {}
        database.fetch_relation_data = fetch_relation_data
        harness.begin()
        harness.charm.database = database
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_relational_db_data()

        assert e_info.value.status_type(BlockedStatus)
        assert "Please add relation to the database" in str(e_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.ZenMLCharm.container")
    def test_update_layer_failure_container_problem(
        self,
        container: MagicMock,
        harness: Harness,
    ):
        change = MagicMock()
        change.tasks = []
        container.replan.side_effect = _FakeChangeError("Fake problem during layer update", change)
        harness.begin()
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._update_layer(container, harness.charm._container_name, MagicMock())

        assert exc_info.value.status_type(BlockedStatus)
        assert "Failed to replan with error: " in str(exc_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_update_layer_success(
        self,
        harness: Harness,
    ):
        harness.begin()
        harness.charm._update_layer(
            harness.charm.container,
            harness.charm._container_name,
            harness.charm._charmed_zenml_layer({"ZENML_STORE_TYPE": "test"}),
        )
        assert harness.charm.container.get_plan().services == EXPECTED_SERVICE

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_env_vars(
        self,
        harness: Harness,
    ):
        harness.begin()
        envs = harness.charm._get_env_vars(RELATIONAL_DB_DATA)
        assert envs == EXPECTED_ENVIRONMENT

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.ZenMLCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    def test_on_event(
        self,
        _: MagicMock,
        harness: Harness,
    ):
        harness.set_leader(True)
        harness.begin()
        harness.charm._on_event(None)
        assert harness.charm.model.unit.status == ActiveStatus()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_database_relation_removed(
        self,
        harness: Harness,
    ):
        harness.begin()
        harness.charm._on_database_relation_removed(None)
        assert harness.charm.model.unit.status == BlockedStatus(
            "Please add relation to the database"
        )
