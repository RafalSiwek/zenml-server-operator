import logging
import subprocess
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
RELATIONAL_DB_CHARM_NAME = "mysql-k8s"


class TestCharm:
    @retry(stop=stop_after_delay(300), wait=wait_fixed(10))
    def _check_can_connect_with_zenml_client(self, zenml_url: str):
        zenml_subprocess = subprocess.run(
            ["zenml", "connect", "--url", zenml_url, "--username", "default", "--password", ""]
        )
        logger.info(f"ZenML command stdout: {zenml_subprocess.stdout}")
        if zenml_subprocess.stderr:
            logger.info(f"ZenML command stderr: {zenml_subprocess.stderr}")
        assert zenml_subprocess.returncode == 0

    @pytest.mark.abort_on_fail
    @pytest.mark.skip_if_deployed
    async def test_successfull_deploy_senario(self, ops_test: OpsTest):
        await ops_test.model.deploy(
            RELATIONAL_DB_CHARM_NAME,
            channel="8.0/stable",
            series="jammy",
            trust=True,
        )
        await ops_test.model.relate(CHARM_NAME, RELATIONAL_DB_CHARM_NAME)

        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )
        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

        config = await ops_test.model.applications[CHARM_NAME].get_config()
        zenml_nodeport = config["zenml_nodeport"]["value"]
        zenml_url = f"http://localhost:{zenml_nodeport}"

        self._check_can_connect_with_zenml_client(zenml_url=zenml_url)
