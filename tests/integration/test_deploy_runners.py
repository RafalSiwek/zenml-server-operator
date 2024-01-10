from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]


class TestDeployRunners:
    @pytest.mark.abort_on_fail
    async def test_build_and_deploy(self, ops_test: OpsTest):
        """Build and deploy the charm.

        Assert on the unit status.
        """
        charm_under_test = await ops_test.build_charm(".")
        image_path = METADATA["resources"]["oci-image"]["upstream-source"]
        resources = {"oci-image": image_path}

        await ops_test.model.deploy(
            charm_under_test,
            resources=resources,
            application_name=CHARM_NAME,
            trust=True,
            config={"memory": "600Mi"},
        )

        await ops_test.model.wait_for_idle(apps=[CHARM_NAME], status="blocked", timeout=300)
        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "blocked"
        assert (
            ops_test.model.applications[CHARM_NAME].units[0].workload_status_message
            == "Please add relation to the database"
        )
