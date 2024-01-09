#!/usr/bin/env python3

import json
import logging
import typing
from pathlib import Path

import yaml
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.observability_libs.v0.kubernetes_compute_resources_patch import (
    K8sResourcePatchFailedEvent,
    KubernetesComputeResourcesPatch,
    ResourceRequirements,
    adjust_resource_requirements,
)
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from jinja2 import Template
from lightkube import ApiError
from lightkube.generic_resource import load_in_cluster_generic_resources
from lightkube.models.core_v1 import ServicePort
from lightkube.resources.batch_v1 import Job
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import ChangeError, Layer
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed, get_interfaces
from tenacity import retry, stop_after_attempt, wait_fixed

ZENML_JOB = [
    "src/jobs/zenml-db-job.yaml.j2",
]


class ZenMLCharm(CharmBase):
    """A Juju Charm for ZenML Server."""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)
        self._port = self.model.config["zenml_port"]
        self._container_name = "zenml-server"
        self._database_name = "zenml"
        self._container = self.unit.get_container(self._container_name)

        self.resources_patch = KubernetesComputeResourcesPatch(
            self,
            self._container_name,
            resource_reqs_func=self._resource_spec_from_config,
        )
        self.framework.observe(
            self.resources_patch.on.patch_failed, self._on_resource_patch_failed
        )

        self.database = DatabaseRequires(
            self, relation_name="relational-db", database_name=self._database_name
        )

        self.framework.observe(self.on.upgrade_charm, self._on_event)
        self.framework.observe(self.on.config_changed, self._on_event)
        self.framework.observe(self.on.zenml_server_pebble_ready, self._on_pebble_ready)

        for rel in self.model.relations.keys():
            self.framework.observe(self.on[rel].relation_changed, self._on_event)

        self._lightkube_field_manager = "lightkube"
        self._zenml_job_resource_handler: KubernetesResourceHandler = None

        self._create_service()

        self.framework.observe(self.on.update_status, self._on_event)
        self.framework.observe(self.database.on.database_created, self._on_database_created)
        self.framework.observe(self.database.on.endpoints_changed, self._on_event)
        self.framework.observe(
            self.on.relational_db_relation_broken, self._on_database_relation_removed
        )

    @property
    def container(self):
        """Return container."""
        return self._container

    def _create_service(self):
        """Create k8s service based on charm's config."""
        if self.config["enable_zenml_nodeport"]:
            service_type = "NodePort"
            self._node_port = self.model.config["zenml_nodeport"]
            port = ServicePort(
                int(self._port),
                name=f"{self.app.name}",
                targetPort=int(self._port),
                nodePort=int(self._node_port),
            )

        else:
            service_type = "ClusterIP"
            port = ServicePort(int(self._port), name=f"{self.app.name}")

        self.service_patcher = KubernetesServicePatch(
            self,
            [port],
            service_type=service_type,
            service_name=f"{self.model.app.name}",
            refresh_event=self.on.config_changed,
        )

    def _resource_spec_from_config(self) -> ResourceRequirements:
        resource_limit = {
            "cpu": self.model.config.get("cpu"),
            "memory": self.model.config.get("memory"),
        }

        return adjust_resource_requirements(
            limits=resource_limit,
            requests={},
            adhere_to_requests=False,
        )

    def _on_resource_patch_failed(self, event: K8sResourcePatchFailedEvent):
        self.unit.status = BlockedStatus(typing.cast(str, event.message))

    def _get_env_vars(self, relational_db_data):
        """Return environment variables based on model configuration."""

        ret_env_vars = {
            "ZENML_STORE_TYPE": "sql",
            "ZENML_STORE_URL": f"mysql://{relational_db_data['username']}:{relational_db_data['password']}@{relational_db_data['host']}:{relational_db_data['port']}/{self._database_name}",  # noqa: E501
            "DISABLE_DATABASE_MIGRATION": "True",  # To avoid migrations https://github.com/zenml-io/zenml/tree/9a69295e9aabaa90156b0cc6115f585a91d0f108/src/zenml/zen_stores/migrations # noqa: E501
            "ZENML_STORE_SSL_VERIFY_SERVER_CERT": "false",
            "ZENML_SERVER_DEPLOYMENT_TYPE": "kubernetes",
            "ZENML_DEFAULT_PROJECT_NAME": "default",
            "ZENML_DEFAULT_USER_NAME": "default",
            "ZENML_LOGGING_VERBOSITY": self.model.config.get("zenml_logging_verbosity", "INFO"),
            # See other possible variables:
            # https://github.com/zenml-io/zenml/blob/04fb3ca0ab94c8bbef31a7794f3f330b2b9b7cf5/src/zenml/zen_server/deploy/helm/templates/server-deployment.yaml # noqa: E501
        }
        return ret_env_vars

    def _charmed_zenml_layer(self, env_vars) -> Layer:
        """Create and return Pebble framework layer."""

        layer_config = {
            "summary": "zenml-server layer",
            "description": "Pebble config layer for zenml-server",
            "services": {
                self._container_name: {
                    "override": "replace",
                    "summary": "Entrypoint of zenml-server image",
                    "command": (
                        "uvicorn "
                        "zenml.zen_server.zen_server_api:app "
                        "--log-level "
                        "debug "
                        "--proxy-headers "
                        "--port "
                        f"{self._port} "
                        "--host "
                        "0.0.0.0"
                    ),
                    "startup": "enabled",
                    "environment": env_vars,
                }
            },
        }

        return Layer(layer_config)

    def _get_interfaces(self):
        """Retrieve interface object."""
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise ErrorWithStatus(err, WaitingStatus)
        except NoCompatibleVersions as err:
            raise ErrorWithStatus(err, BlockedStatus)
        return interfaces

    def _get_relational_db_data(self) -> dict:
        mysql_relation = self.model.get_relation("relational-db")

        # Raise exception and stop execution if the relational-db relation is not established
        if not mysql_relation:
            raise ErrorWithStatus("Please add relation to the database", BlockedStatus)

        data = self.database.fetch_relation_data()
        self.logger.debug("Got following database data: %s", data)
        for val in data.values():
            if not val:
                continue
            try:
                host, port = val["endpoints"].split(":")
                db_data = {
                    "host": host,
                    "port": port,
                    "username": val["username"],
                    "password": val["password"],
                }
            except KeyError:
                raise ErrorWithStatus(
                    "Incorrect data found in relation relational-db", WaitingStatus
                )
            return db_data
        raise ErrorWithStatus("Waiting for relational-db relation data", WaitingStatus)

    def _check_leader(self):
        """Check if this unit is a leader."""
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping setup")
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _update_layer(self, container, container_name, new_layer) -> None:
        current_layer = self.container.get_plan()
        if current_layer.services != new_layer.services:
            self.unit.status = MaintenanceStatus("Applying new pebble layer")
            container.add_layer(container_name, new_layer, combine=True)
            try:
                self.logger.info(
                    f"Pebble plan updated with new configuration, replaning for {container_name}"
                )
                container.replan()
            except ChangeError as err:
                raise ErrorWithStatus(f"Failed to replan with error: {str(err)}", BlockedStatus)

    def _on_pebble_ready(self, _):
        """Configure started container."""
        if not self.container.can_connect():
            # Pebble Ready event should indicate that container is available
            raise ErrorWithStatus("Pebble is ready and container is not ready", BlockedStatus)

        # proceed with other actions
        self._on_event(_)

    def _on_database_relation_removed(self, _) -> None:
        """Event is fired when relation with postgres is broken."""
        self.unit.status = BlockedStatus("Please add relation to the database")

    def _send_manifests(self, interfaces, context, manifest_files, relation):
        """Send manifests from folder to desired relation."""
        if relation in interfaces and interfaces[relation]:
            manifests = self._create_manifests(manifest_files, context)
            interfaces[relation].send_data({relation: manifests})

    def _create_manifests(self, manifest_files, context):
        """Create manifests string for given folder and context."""
        manifests = []
        for file in manifest_files:
            template = Template(Path(file).read_text())
            rendered_template = template.render(**context)
            manifest = yaml.safe_load(rendered_template)
            manifests.append(manifest)
        return json.dumps(manifests)

    def _check_and_report_k8s_conflict(self, error):
        """Return True if error status code is 409 (conflict), False otherwise."""
        if error.status.code == 409:
            self.logger.warning(f"Encountered a conflict: {error}")
            return True
        return False

    def _get_job_status(self) -> dict:
        return self._zenml_job_resource_handler.get_deployed_resources()[0].__dict__[
            "_lazy_values"
        ][
            "status"
        ]  # noqa: E501

    @retry(wait=wait_fixed(1), stop=stop_after_attempt(30))
    def _wait_for_job_completion(self, timeout=60, interval=1) -> str:
        status: dict = self._get_job_status()
        if status.get("failed"):
            return "failed"
        if status.get("succeeded"):
            return "succeeded"
        else:
            self.logger.info(
                f"ZenML Database migration job not completed, current status: {status}"
            )
            raise IOError("ZenML Database migration job not completed")

    def _on_database_created(self, event) -> None:
        """Perform ZenML Database migration job on database created event"""
        relational_db_data = self._get_relational_db_data()
        job_env_vars = self._get_env_vars(relational_db_data)

        """Check if initialized"""
        if self._zenml_job_resource_handler:
            """Check if job already run"""
            if self._zenml_job_resource_handler.get_deployed_resources():
                """Delete the current job resources"""
                self._zenml_job_resource_handler.delete()
        """The reason of initializing the KRH here is because of the relational_db_data being loaded on event"""  # noqa: E501
        self._zenml_job_resource_handler = KubernetesResourceHandler(
            field_manager="lightkube",
            template_files=ZENML_JOB,
            context={
                "logging_verbosity": job_env_vars["ZENML_LOGGING_VERBOSITY"],
                "app_name": self.app.name,
                "namespace": self.model.name,
                "database_url": job_env_vars["ZENML_STORE_URL"],
                "default_project_name": job_env_vars["ZENML_DEFAULT_PROJECT_NAME"],
                "default_user_name": job_env_vars["ZENML_DEFAULT_USER_NAME"],
                "store_type": job_env_vars["ZENML_STORE_TYPE"],
                "store_ssl_verify_server_cert": job_env_vars["ZENML_STORE_SSL_VERIFY_SERVER_CERT"],
            },
            resource_types={Job},
            labels={"application_name": "zenml-database-migration", "scope": "all-resources"},
        )
        load_in_cluster_generic_resources(self._zenml_job_resource_handler.lightkube_client)

        self.unit.status = MaintenanceStatus("Creating ZenML Database Migration Job resources")
        try:
            self._zenml_job_resource_handler.apply()
        except ApiError as err:
            self.model.unit.status = err.status
            self.logger.error(f"Failed to run ZenML Database Migration Job: {err}")
            self.unit.status = BlockedStatus(f"Failed to run ZenML Database Migration Job: {err}")
            return

        self.unit.status = MaintenanceStatus(
            "Waiting for ZenML Database Migration Job to complete"
        )
        try:
            status = self._wait_for_job_completion()
            """Check if job succeeded"""
            if status != "succeeded":
                self.unit.status = BlockedStatus(
                    "Failed to run ZenML Database Migration Job. Check zenml-database-migration job pod logs"  # noqa: E501
                )
        except IOError:
            """Failed to run the job"""
            self.unit.status = BlockedStatus(
                "Failed to run ZenML Database Migration Job. Check zenml-database-migration job logs"  # noqa: E501
            )

        self._on_event(event)

    def _on_event(self, event) -> None:
        """Perform all required actions for the Charm."""
        try:
            self._check_leader()
            relational_db_data = self._get_relational_db_data()
            envs = self._get_env_vars(relational_db_data)

            if not self.container.can_connect():
                raise ErrorWithStatus(
                    f"Container {self._container_name} is not ready", WaitingStatus
                )
            self._update_layer(
                self.container, self._container_name, self._charmed_zenml_layer(envs)
            )
        except ErrorWithStatus as err:
            self.model.unit.status = err.status
            self.logger.info(f"Event {event} stopped early with message: {str(err)}")
            return
        self.model.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(ZenMLCharm)
