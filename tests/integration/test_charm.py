import logging
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
RELATIONAL_DB_CHARM_NAME = "mysql-k8s"


class TestCharm:
    @retry(stop=stop_after_attempt(5))
    async def _deploy_relational_db(self, ops_test: OpsTest):
        if RELATIONAL_DB_CHARM_NAME in ops_test.model.applications.keys():
            logger.info(f"Failed to deploy {RELATIONAL_DB_CHARM_NAME}, redeploying...")
            await ops_test.model.remove_application(
                app_name=RELATIONAL_DB_CHARM_NAME,
                force=True,
                destroy_storage=True,
                no_wait=True,
            )

        await ops_test.model.deploy(
            RELATIONAL_DB_CHARM_NAME,
            channel="8.0/stable",
            trust=True,
        )

        await ops_test.model.wait_for_idle(
            apps=[RELATIONAL_DB_CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=30,
        )

    @pytest.mark.abort_on_fail
    @pytest.mark.skip_if_deployed
    async def test_successfull_deploy_senario(self, ops_test: OpsTest):
        await self._deploy_relational_db(ops_test)

        await ops_test.model.relate(RELATIONAL_DB_CHARM_NAME, CHARM_NAME)

        time.sleep(10)  # Wait for relation to get active setup

        await ops_test.model.wait_for_idle(
            apps=[RELATIONAL_DB_CHARM_NAME, CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )

        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

        config = await ops_test.model.applications[CHARM_NAME].get_config()

        zenml_port = config["zenml_nodeport"]["value"]

        zenml_url = f"http://localhost:{zenml_port}"
        zenml_subprocess = subprocess.run(
            ["zenml", "connect", "--url", zenml_url, "--username", "default", "--password", ""]
        )
        logger.info(f"ZenML command stdout: {zenml_subprocess.stdout}")
        if zenml_subprocess.stderr:
            logger.info(f"ZenML command stderr: {zenml_subprocess.stderr}")
        assert zenml_subprocess.returncode == 0
