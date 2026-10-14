# AAP-96667: record the original namespace-change failure

**Purpose:** Show how the pre-fix server can lose a running activation Job
when only the workers' namespace setting changes. This intentionally leaves
an orphaned Job. Use **only** a disposable Kind cluster, never a shared or
production cluster.

Run from the repository root in Bash. You need Podman, Kind, `kubectl`,
Kustomize, Task, `ansible-playbook`, the `ansible.eda` collection, and access
to the sample project and rulebook image. Keep the setup and demo commands in
the same terminal.

## One-time setup

1. Create your own ignored `tools/deploy/environment.properties` from
   `tools/deploy/environment.properties.example` if it does not already
   exist. Set `EDA_WEBSOCKET_BASE_URL` to
   `ws://eda-ws.aap-eda.svc.cluster.local:8000` so a Pod in the second
   namespace can reach EDA. Leave `EDA_ACTIVATION_JOB_NAMESPACE` unset or
   commented out. Do not commit this local file or overwrite other local
   configuration. Check that `tools/deploy/eda-ws/deployment.yaml` contains
   `EDA_WORKER_KIND=websocket`.

2. Use an **unused** disposable cluster name. These commands stop rather
   than deleting an existing cluster. The namespace/RBAC manifest is applied
   separately because it stands in for externally provided resources.

```bash
export DEMO_CLUSTER=eda-original-demo
export KUBECONFIG=/tmp/eda-original-demo.kubeconfig
export CONTEXT=kind-eda-original-demo
export KIND_EXPERIMENTAL_PROVIDER=podman
unset DOCKER_HOST
kind get clusters  # STOP if eda-original-demo is listed
kind create cluster --name "$DEMO_CLUSTER" --kubeconfig "$KUBECONFIG"
kind export kubeconfig --name "$DEMO_CLUSTER" --kubeconfig "$KUBECONFIG"
kubectl --context "$CONTEXT" wait \
  node/eda-original-demo-control-plane --for=condition=Ready --timeout=180s
kubectl --context "$CONTEXT" apply \
  -f tools/deploy/activation-namespace-poc.yaml
```

3. Build the **pre-fix code** in this branch with a unique image tag and
   load it through an archive. This avoids Kind using a stale `:latest`
   image. Allow about 1.1 GB in `/tmp/opencode`.

```bash
DEMO_IMAGE="localhost/aap-eda:original-$(date +%Y%m%d%H%M%S)"
podman build -t "$DEMO_IMAGE" -f tools/docker/Dockerfile .
ARCHIVE_DIR=$(mktemp -d /tmp/opencode/eda-original-XXXXXXXX)
podman save --format docker-archive \
  -o "$ARCHIVE_DIR/image.tar" "$DEMO_IMAGE"
kind load image-archive "$ARCHIVE_DIR/image.tar" --name "$DEMO_CLUSTER"
podman exec "$DEMO_CLUSTER-control-plane" crictl inspecti "$DEMO_IMAGE" \
  | grep -F "$(podman image inspect "$DEMO_IMAGE" --format '{{.Id}}')"
```

4. Deploy EDA and switch **all EDA server Deployments** to that same image.
   `kind:deploy` may briefly start the `:latest` image; do not create an
   activation before the image rollouts finish.

```bash
KIND_CLUSTER_NAME="$DEMO_CLUSTER" task kind:deploy
for name in eda-api eda-activation-worker eda-default-worker \
  eda-scheduler eda-ws eda-webhook-api; do
  kubectl --context "$CONTEXT" set image "deployment/$name" \
    "$name=$DEMO_IMAGE" -n aap-eda
  kubectl --context "$CONTEXT" rollout status "deployment/$name" \
    -n aap-eda --timeout=300s
done
kubectl --context "$CONTEXT" rollout status \
  deployment/eda-ui -n aap-eda --timeout=300s
```

5. Leave the worker namespace setting **unset** before creating the
   activation. With the local setting commented out, the initial Job uses
   the worker's service-account namespace (`aap-eda`). Check that the workers
   are ready and the target namespace will be accessible after the change.

```bash
kubectl --context "$CONTEXT" rollout status \
  deployment/eda-activation-worker -n aap-eda --timeout=180s
kubectl --context "$CONTEXT" get deployment eda-activation-worker \
  -n aap-eda -o jsonpath='{.status.readyReplicas}{" ready; override="}{.spec.template.spec.containers[0].env[?(@.name=="EDA_ACTIVATION_JOB_NAMESPACE")].value}{"\n"}'
kubectl --context "$CONTEXT" auth can-i create jobs.batch \
  -n eda-activations --as=system:serviceaccount:aap-eda:eda-default-sa
```

Expect `2 ready; override=` and `yes`. In a **second terminal**, keep the UI
port-forward running:

```bash
KUBECONFIG=/tmp/eda-original-demo.kubeconfig \
  kubectl --context kind-eda-original-demo port-forward \
  -n aap-eda svc/eda-ui 8443:8443
```

In the first terminal, run the sample playbook with a Boolean extra-var.
It creates other demo resources too, and activation creation is asynchronous.
At <https://localhost:8443>, sign in as `admin` / `testpass` and wait until
**Long running test activation 1** is **Running**.

```bash
ansible-galaxy collection list ansible.eda
ansible-playbook tools/ansible/eda_init.yml \
  -e '{"create_activations": true}'
```

## 1. Establish the baseline

```bash
kubectl --context "$CONTEXT" get jobs -n aap-eda -l app=eda
kubectl --context "$CONTEXT" get pods -n aap-eda -l job-name
kubectl --context "$CONTEXT" get jobs -n eda-activations -l app=eda
```

Expect one Running Job and Pod in `aap-eda`, none in `eda-activations`.
Stop if this differs. Record the actual Job name and UID:

```bash
OLD_JOB=activation-job-1-1  # replace if the name above differs
OLD_UID=$(kubectl --context "$CONTEXT" get job "$OLD_JOB" \
  -n aap-eda -o jsonpath='{.metadata.uid}')
printf 'Original Job: %s (UID %s)\n' "$OLD_JOB" "$OLD_UID"
```

## 2. Change only the workers while the old Job is running

**Deliberately disruptive:** the live setting makes new engines use
`eda-activations`. It does not move the original Job in `aap-eda` or change
the local configuration file.

```bash
kubectl --context "$CONTEXT" set env deployment/eda-activation-worker \
  -n aap-eda EDA_ACTIVATION_JOB_NAMESPACE=eda-activations
kubectl --context "$CONTEXT" rollout status \
  deployment/eda-activation-worker -n aap-eda --timeout=180s
kubectl --context "$CONTEXT" get job "$OLD_JOB" \
  -n aap-eda -o jsonpath='{.metadata.uid}{"\n"}'
kubectl --context "$CONTEXT" get jobs -n aap-eda -l app=eda
kubectl --context "$CONTEXT" get pods -n aap-eda -l job-name
kubectl --context "$CONTEXT" get jobs -n eda-activations -l app=eda
kubectl --context "$CONTEXT" get pods -n eda-activations -l job-name
```

The original UID should be unchanged. After monitoring applies the on-failure
restart policy, run the four read-only `get` commands again. **Capture both
Jobs and Pods Running in different namespaces.** If the replacement does not
appear, inspect the activation status and worker logs; do not claim success.

## 3. Show the orphan after a normal disable

In the UI, **disable** the activation. Wait for EDA to report it stopped;
then check both namespaces:

```bash
kubectl --context "$CONTEXT" get jobs -n aap-eda -l app=eda
kubectl --context "$CONTEXT" get pods -n aap-eda -l job-name
kubectl --context "$CONTEXT" get jobs -n eda-activations -l app=eda
kubectl --context "$CONTEXT" get pods -n eda-activations -l job-name
kubectl --context "$CONTEXT" get job "$OLD_JOB" \
  -n aap-eda -o jsonpath='{.metadata.uid}{"\n"}'
```

The replacement should be gone, but the **same original Job and Pod** should
still run in `aap-eda`. A stopped UI status alone is not proof of
cleanup. This demonstrates overlapping rulebook execution, **not** duplicate
processing of an identical external event.

Leave the cluster for review or, after recording, delete **only this named
disposable cluster**. Do not re-enable the activation while the orphan runs.
Do not run `task kind:all` or `task kind:deploy` during the live demo; they
can overwrite the temporary setting. The namespace-aware branch contains
the separate successful-restart demonstration.
