import logging
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
RELATIONAL_DB_CHARM_NAME = "mysql-k8s"


class TestCharm:
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

        zenml_port = config["zenml_port"]["value"]

        portforward_subprocess = subprocess.Popen(
            [
                "kubectl",
                "-n",
                f"{ops_test.model_name}",
                "port-forward",
                f"svc/{CHARM_NAME}",
                f"{zenml_port}:{zenml_port}",
            ]
        )
        time.sleep(10)  # Must wait for port-forward

        zenml_url = f"http://localhost:{zenml_port}"
        zenml_subprocess = subprocess.run(
            ["zenml", "connect", "--url", zenml_url, "--username", "default", "--password", ""]
        )
        logger.info(f"ZenML command stdout: {zenml_subprocess.stdout}")
        if zenml_subprocess.stderr:
            logger.info(f"ZenML command stderr: {zenml_subprocess.stderr}")
        assert zenml_subprocess.returncode == 0

        portforward_subprocess.terminate()
