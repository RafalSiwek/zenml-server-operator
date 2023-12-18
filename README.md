# [ZenML](https://www.zenml.io/) on Juju with Microk8s

**PROJECT STATUS: WORK IN PROGRESS**

**NOTE:** This project was inspired by the [Charmed MLFlow Project](https://github.com/canonical/mlflow-operator) and implements some solutions found there.

## Get started

### Prerequisites

We are assuming that you are running this tutorial on a local machine or a EC2 instance with the following specs:

- Runs Ubuntu 22.04 or later

- Has at least 50GB free disk space

### Install dependencies

```bash
sudo snap install charmcraft --classic
```

### Create ZenML

```bash
charmcraft pack
```

This step will generate a charm file **zenml-server_ubuntu-22.04-amd64.charm**

### Install and prepare MicroK8s

Install MicroK8s from a snap package

```bash
sudo snap install microk8s --classic
```

Add the current user to the `microk8s` group and generate configuration directory for `kubectl`

```bash
sudo usermod -a -G microk8s $USER
newgrp microk8s
sudo chown -f -R $USER ~/.kube
cd $HOME
mkdir .kube
cd .kube
microk8s config > config
```

Enable the following MicroK8s addons to configure your Kubernetes cluster with extra services needed to run [Charmed Kubeflow](https://charmed-kubeflow.io/docs/get-started-with-charmed-kubeflow).

```bash
microk8s enable dns hostpath-storage ingress metallb:10.64.140.43-10.64.140.49
```

Wait untill the command

```bash
microk8s status --wait-ready
```

Returns

```bash
microk8s is running
```

**NOTE:** To use the MicroK8s built-in registry in the [ZenML stack](https://docs.zenml.io/stacks-and-components/component-guide/model-registries), please refer to the [guide](https://microk8s.io/docs/registry-built-in)

### Install and prepare MicroK8s

To install Juju from snap, run this command:

```bash
sudo snap install juju --classic
```

Deploy a Juju controller to the Kubernetes we set up with MicroK8s:

```bash
juju bootstrap microk8s
```

### Deploy Sandalone ZenML Server

Create a model

```bash
juju add-model <model name>
```

Deploy the ZenML server charm

```bash
juju deploy ./zenml-server_ubuntu-22.04-amd64.charm zenml-server \
    --resource oci-image=$(yq '.resources."oci-image"."upstream-source"' metadata.yaml)
```

Deploy [mysql-k8s](https://github.com/canonical/mysql-k8s-operator) to be used as ZenML Server backend and create relation

```bash
juju deploy mysql-k8s zenml-mysql --channel 8.0/stable

juju relate zenml-server zenml-mysql
```

Run `juju status --watch 2s` to observe the charm deployment and after all the apps reach the `Active` status, the ZenML Server dashboard should be accessible as a `NodePort` under

```
http://localhost:31375/
```

With the default settings for `username = default` and no password.

To connect the `zenml SDK` to it run

```bash
zenml connect --uri http://localhost:31375/ --username default --password ''
```

### Integrate ZenML Server with Charmed Kubeflow

To use Charmed Kubeflow as a orchestrator for a ZenML stack some configurations have to be done:

To install Charmed Kubeflow follow [this guide](https://charmed-kubeflow.io/docs/get-started-with-charmed-kubeflow)

If running Charmed Kubeflow on a EC2 instance, configure the `istio-ingress-gateway` service tyope to `NodePort`:

```bash
kubectl -n kubeflow patch svc istio-ingressgateway-workload \
 -p '{"spec":{"type":"NodePort"}}'
```

And reconfigure `dex` and `oidc-gatekeeper` to point on the instance public IP:

```bash
export NODE_IP=<the instance public IP>

export NODE_PORT=$(kubectl -n kubeflow get svc istio-ingressgateway-workload -o=json | \
 jq '(.spec.ports) | .[] | select(.name=="http2") | (.nodePort)')

export PUBLIC_URL="http://${NODE_IP}:${NODE_PORT}"

juju config dex-auth public-url=${PUBLIC_URL}
juju config oidc-gatekeeper public-url=${PUBLIC_URL}
```

Set authentication methods:

```bash
juju config dex-auth static-username="<your username>"
juju config dex-auth static-password="<your pasword>"
```

And make sure the security group allows ingress to the instance on CIDR of `echo "${NODE_IP}/32"` on the `echo $NODE_PORT` port

After this the Kubeflow Dashboard will be accessible under the value of `echo $PUBLIC_URL`

The pipeline API, required for configuring the Kubeflow Orchestrator for ZenML will have the value of:

```bash
echo "${PUBLIC_URL}/pipeline/"
```

Configuring Kubeflow Pipelines as a orchestrator for your ZenML workload also requires setting up an Artifact Store.

For this tutorial we will use already deployed `minio` service and expose it as `NodePort`

```bash
kubectl -n kubeflow patch svc minio \
 -p '{"spec":{"type":"NodePort"}}'
```

After running `kubectl get svc -n kubeflow`, we should find the `minio` service configuration:

```bash
NAME                                  TYPE           CLUSTER-IP       EXTERNAL-IP                        PORT(S)
.
.
.
minio                                 NodePort       10.152.183.227   <none>                             9000:<API port>/TCP,9001:<Dashboard port>/TCP
```

Now the MinIO dashboard should be accessible under:

```bash
http://localhost:<Dashboard port>
```

Or, when running on a EC2 with ingress configured to allow both `<Dashboard port>` and `<API port>` from your IP and the public IP of the machine:

```bash
echo "${PUBLIC_URL}:<Dashboard port>"
```

The `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` in **base64** can be recived form Kubernetes Secrets:

```bash
kubectl get secrets minio-secret -n kubeflow -o yaml
```

Before using them, remember to decode the.

ZenML will require a specified bucket so use the dashboard to create one and the artifact store can be configured with:

```bash
# Store the AWS access key in a ZenML secret
zenml secret create s3_secret \
    --aws_access_key_id='<DECODED_MINIO_ACCESS_KEY>' \
    --aws_secret_access_key='<DECODED_MINIO_SECRET_KEY>'

# Register the MinIO artifact-store and reference the ZenML secret
zenml artifact-store register minio_store -f s3 \
    --path='s3://<minio_bucket>' \
    --authentication_secret=s3_secret \
    --client_kwargs='{"endpoint_url": http://localhost:<API port>}' # or for EC2 <publicIP>:<API port>
```

When running a ZenML pipeline with Kubeflow Orchestrator, the client will either use the current machine docker client or preconfigured [image builder](https://docs.zenml.io/stacks-and-components/component-guide/image-builders) to build the pipeline image and publish it to a registry. By default it is the local registry that comes with installing ZenML, but to allow an remote Kubeflow to use it, a remote registry has to be available.

For this the mentioned MicroK8s built-in registry comes in handy and can be configured with:

```bash
zenml container-registry register <NAME> \
    --flavor=default \
    --uri=localhost:32000 # or for EC2 <publicIP>:32000
```
