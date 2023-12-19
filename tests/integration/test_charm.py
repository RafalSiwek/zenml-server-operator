import logging
import subprocess
from pathlib import Path

import aiohttp
import lightkube
import pytest
import yaml
from lightkube.resources.core_v1 import Service
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
RELATIONAL_DB_CHARM_NAME = "mysql-k8s"
ISTIO_GATEWAY_CHARM_NAME = "istio-ingressgateway"
ISTIO_PILOT_CHARM_NAME = "istio-pilot"
METACONTROLLER_CHARM_NAME = "metacontroller-operator"


@pytest.fixture(scope="session")
def lightkube_client() -> lightkube.Client:
    client = lightkube.Client(field_manager=CHARM_NAME)
    return client


async def fetch_url(url):
    """Fetch provided URL and return JSON."""
    result = None
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            result = await response.json()
    return result


async def setup_istio(ops_test: OpsTest, istio_gateway: str, istio_pilot: str):
    """Deploy Istio Ingress Gateway and Istio Pilot."""
    await ops_test.model.deploy(
        entity_url="istio-gateway",
        application_name=istio_gateway,
        channel="1.16/stable",
        config={"kind": "ingress"},
        trust=True,
    )
    await ops_test.model.deploy(
        istio_pilot,
        channel="1.16/stable",
        config={"default-gateway": "test-gateway"},
        trust=True,
    )
    await ops_test.model.add_relation(istio_pilot, istio_gateway)

    await ops_test.model.wait_for_idle(
        apps=[istio_pilot, istio_gateway],
        status="active",
        timeout=60 * 5,
        raise_on_blocked=False,
        raise_on_error=False,
    )


def get_ingress_url(lightkube_client: lightkube.Client, model_name: str):
    gateway_svc = lightkube_client.get(
        Service, "istio-ingressgateway-workload", namespace=model_name
    )
    ingress_record = gateway_svc.status.loadBalancer.ingress[0]
    if ingress_record.ip:
        public_url = f"http://{ingress_record.ip}.nip.io"
    if ingress_record.hostname:
        public_url = f"http://{ingress_record.hostname}"  # Use hostname (e.g. EKS)
    return public_url


async def fetch_response(url, headers):
    """Fetch provided URL and return pair - status and text (int, string)."""
    result_status = 0
    result_text = ""
    async with aiohttp.ClientSession() as session:
        async with session.get(url=url, headers=headers) as response:
            result_status = response.status
            result_text = await response.text()
    return result_status, str(result_text)


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
        zenml_nodeport = config["zenml_nodeport"]["value"]
        zenml_url = f"http://localhost:{zenml_nodeport}"
        zenml_subprocess = subprocess.run(
            ["zenml", "connect", "--url", zenml_url, "--username", "default", "--password", ""]
        )
        logger.info(f"ZenML command stdout: {zenml_subprocess.stdout}")
        if zenml_subprocess.stderr:
            logger.info(f"ZenML command stderr: {zenml_subprocess.stderr}")
        assert zenml_subprocess.returncode == 0

    async def test_ingress_relation(self, ops_test: OpsTest):
        """Setup Istio and relate it to the ZenML Server."""
        await setup_istio(ops_test, ISTIO_GATEWAY_CHARM_NAME, ISTIO_PILOT_CHARM_NAME)

        await ops_test.model.add_relation(
            f"{ISTIO_PILOT_CHARM_NAME}:ingress", f"{CHARM_NAME}:ingress"
        )

        await ops_test.model.wait_for_idle(apps=[CHARM_NAME], status="active", timeout=60 * 5)

    @pytest.mark.abort_on_fail
    async def test_ingress_url(self, lightkube_client, ops_test: OpsTest):
        ingress_url = get_ingress_url(lightkube_client, ops_test.model_name)
        result_status, result_text = await fetch_response(f"{ingress_url}/zenml/", {})

        # verify that UI is accessible
        assert result_status == 200
        assert len(result_text) > 0
