[![ZenML Server](https://charmhub.io/zenml-server/badge.svg)](https://charmhub.io/zenml-server) [![Zenml](https://charmhub.io/zenml/badge.svg)](https://charmhub.io/zenml)

# [ZenML](https://www.zenml.io/) on Juju with Microk8s

**DISCLAIMER:** This project was inspired by the [Charmed MLFlow Project](https://github.com/canonical/mlflow-operator) and also implements solutions found there.

- [Get Started](#get-started)

  - [Prerequisites](#prerequisites)
  - [Install and prepare Juju](#install-and-prepare-juju)
  - [Deploy Sandalone ZenML Server Bundle](#build-and-deploy-the-charm-manually)
  - [Build and deploy the charm manually](#build-and-deploy-the-charm-manually)

- [Integrate ZenML Server with Charmed Kubeflow](#integrate-zenml-server-with-charmed-kubeflow)

- [Ingress](#ingress)

- [Examples](#examples)

## Get started

### Prerequisites

We are assuming that you are running this tutorial on a local machine or a EC2 instance with the following specs:

- Runs Ubuntu 22.04 or later

- Has at least 50GB free disk space

### Install and prepare MicroK8s

Install MicroK8s from a snap package

```bash
sudo snap install microk8s --classic --channel=1.28/stable
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

Wait until the command

```bash
microk8s status --wait-ready
```

Returns

```bash
microk8s is running
```

**NOTE:** To use the MicroK8s built-in registry in the [ZenML stack](https://docs.zenml.io/stacks-and-components/component-guide/model-registries), please refer to the [guide](https://microk8s.io/docs/registry-built-in)

### Install and prepare Juju

To install Juju from snap, run this command:

```bash
sudo snap install juju --classic --channel=3.1/stable
```

On some machines there might be a missing folder which is required for juju to run correctly. Because of this please make sure to create this folder with:

```bash
mkdir -p ~/.local/share
```

Deploy a Juju controller to the Kubernetes we set up with MicroK8s:

```bash
microk8s config | juju add-k8s my-k8s --client
juju bootstrap my-k8s uk8sx
```

Create a model

```bash
juju add-model <model name>
```

### Deploy Sandalone ZenML Server Bundle

To deploy the ZenML bundle run:

```bash
juju deploy zenml --trust
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

### Build and deploy the charm manually

Install dependencies

```bash
sudo snap install charmcraft --classic
```

Create ZenML Charm

```bash
charmcraft pack
```

This step will generate a charm file **zenml-server_ubuntu-20.04-amd64.charm**

Deploy the ZenML server charm

```bash
juju deploy ./zenml-server_ubuntu-20.04-amd64.charm zenml-server \
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

With the same credentials

## Integrate ZenML Server with Charmed Kubeflow

To use Charmed Kubeflow as a orchestrator for a ZenML stack some configurations have to be done:

To install Charmed Kubeflow follow [this guide](https://charmed-kubeflow.io/docs/get-started-with-charmed-kubeflow)

If running Charmed Kubeflow on a EC2 instance, configure the `istio-ingress-gateway` service type to `NodePort`:

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
juju config dex-auth static-password="<your password>"
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

The `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` in **base64** can be received form Kubernetes Secrets:

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

## Ingress

Currently the charmed `zenml-server-operator` supports ingress integration with [`Charmed Istio`](https://github.com/canonical/istio-operators)

**NOTE:** According to [ZenML Docs](https://docs.zenml.io/deploying-zenml/zenml-self-hosted/deploy-with-helm#use-a-dedicated-ingress-url-path-for-zenml):

```
This method has one current limitation: the ZenML UI does not support URL rewriting and will not work properly if you use a dedicated Ingress URL path. You can still connect your client to the ZenML server and use it to run pipelines as usual, but you will not be able to use the ZenML UI.
```

**Additionally, the ZenML client does not support DEX auth - this might require configuring the server to use DEX as an external auth provider - more info [here](https://github.com/zenml-io/zenml/blob/main/src/zenml/zen_server/deploy/helm/values.yaml)**

Deploy Istio Gateway and Istio Pilot charms and configure the relation

```bash
juju deploy istio-gateway istio-ingressgateway --channel 1.17/stable --config kind=ingress --trust

juju deploy istio-pilot --channel 1.17/stable --config default-gateway=test-gateway -trust

juju relate istio-pilot istio-ingressgateway
```

To integrate `zenml-server` with currently deployed `istio-operator` run the command:

```bash
juju relate zenml-server istio-pilot
```

After this done the ZenML Client can connect to the server over the ingress IP found by running:

```bash
microk8s kubectl -n <your namespace> get svc istio-ingressgateway-workload -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

Over the URL `http:/<ingress ip>/zenml/`

## Examples

Check out the [examples directory](/examples/)
