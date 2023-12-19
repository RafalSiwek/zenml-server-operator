import logging
import subprocess

import pytest
from pytest_operator.plugin import OpsTest

BUNDLE_PATH = "./releases/latest/edge/zenml/bundle.yaml"
ZENML_APP_NAME = "zenml-server"

logger = logging.getLogger(__name__)


class TestCharm:
    @pytest.mark.abort_on_fail
    async def test_deploy_bundle_works_and_test_connection(self, ops_test: OpsTest):
        subprocess.Popen(["juju", "deploy", f"{BUNDLE_PATH}", "--trust"])
        await ops_test.model.wait_for_idle(
            apps=[ZENML_APP_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=1500,
        )
        assert ops_test.model.applications[ZENML_APP_NAME].units[0].workload_status == "active"

        config = await ops_test.model.applications[ZENML_APP_NAME].get_config()
        zenml_nodeport = config["zenml_nodeport"]["value"]
        zenml_url = f"http://localhost:{zenml_nodeport}"
        zenml_subprocess = subprocess.run(
            ["zenml", "connect", "--url", zenml_url, "--username", "default", "--password", ""]
        )
        logger.info(f"ZenML command stdout: {zenml_subprocess.stdout}")
        if zenml_subprocess.stderr:
            logger.info(f"ZenML command stderr: {zenml_subprocess.stderr}")
        assert zenml_subprocess.returncode == 0
