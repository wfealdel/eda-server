# AAP-96667: Kind namespace-change demo

**What this shows:** An activation Job starts in `aap-eda`. Changing the
activation-worker setting to `eda-activations` does **not** move that Job.
Restarting the *activation* removes its old Job and Pod and creates a new
process and Job in `eda-activations`.

| Point in the demo | `aap-eda` | `eda-activations` |
| --- | --- | --- |
| Before the worker change | Original Job and Pod | Empty |
| After the worker change | **Same** original Job and Pod | Empty — expected |
| After the activation restart | No activation Job or Pod | New Running Job and Pod |

This is a disposable-cluster PoC, **not** a production migration guide. Work
on `eda-migration-demo`, not another Kind cluster. Do not run `task kind:all`
or `task kind:deploy` during the live demo: they can remove resources or
replace the live-only setting. Do not edit the existing demo YAML.

## Follow the demo (existing cluster)

Run these commands from the repository root in the **same terminal**. If you
already restarted the activation successfully, skip to checkpoint 3; **do
not restart it a second time** just to follow the document.

```bash
export KUBECONFIG=/tmp/eda-migration-demo.kubeconfig
export CONTEXT=kind-eda-migration-demo
kubectl --context "$CONTEXT" config current-context
```

The last command must print `kind-eda-migration-demo`. Stop on errors or
unexpected output. If you have not created the cluster or deployed the PoC
image yet, do the [one-time setup](#one-time-setup-fresh-cluster) first.

### 1. See where the running activation is

In the UI, **Long running test activation 1** should be **Running**. Check
both namespaces (these commands do not change anything):

```bash
kubectl --context "$CONTEXT" get jobs -n aap-eda -l app=eda
kubectl --context "$CONTEXT" get pods -n aap-eda -l job-name
kubectl --context "$CONTEXT" get jobs -n eda-activations -l app=eda
```

Expect one activation Job and Pod in `aap-eda` and no Job in
`eda-activations`. If the new namespace already has a Job, stop and identify
it before proceeding. With the setting initially unset, new Jobs use the
worker's service-account namespace (`aap-eda`). The existing process must
also have been created by the new code: check its recorded namespace before
rolling workers. Stop if it is missing or not `aap-eda`.

```bash
kubectl --context "$CONTEXT" exec -n aap-eda deployment/eda-api -- \
  aap-eda-manage shell -c '
from aap_eda.core.models import Activation
p = Activation.objects.get(name="Long running test activation 1").latest_instance
if p.activation_job_namespace != "aap-eda":
    raise SystemExit("STOP: old Job namespace is not recorded as aap-eda")
print("Ready: old process is recorded in aap-eda")
'
```

Before restarting, make sure the worker's service account can access Jobs
in the target namespace. All three answers must be `yes`:

```bash
kubectl --context "$CONTEXT" auth can-i create jobs.batch \
  -n eda-activations --as=system:serviceaccount:aap-eda:eda-default-sa
kubectl --context "$CONTEXT" auth can-i list jobs.batch \
  -n eda-activations --as=system:serviceaccount:aap-eda:eda-default-sa
kubectl --context "$CONTEXT" auth can-i delete jobs.batch \
  -n eda-activations --as=system:serviceaccount:aap-eda:eda-default-sa
```

### 2. Change the setting, then restart the activation

**Only once:** change the *activation-worker Deployment* setting. This
restarts worker Pods, **not the activation Job**.

```bash
kubectl --context "$CONTEXT" set env deployment/eda-activation-worker \
  -n aap-eda EDA_ACTIVATION_JOB_NAMESPACE=eda-activations
kubectl --context "$CONTEXT" rollout status \
  deployment/eda-activation-worker -n aap-eda --timeout=180s
kubectl --context "$CONTEXT" get deployment eda-activation-worker \
  -n aap-eda -o jsonpath='{.status.readyReplicas}{" ready; new-Job namespace="}{.spec.template.spec.containers[0].env[?(@.name=="EDA_ACTIVATION_JOB_NAMESPACE")].value}{"\n"}'
```

Expect `2 ready; new-Job namespace=eda-activations`. Re-run the three
read-only `get` commands from checkpoint 1. **The old Job and Pod must still
be in `aap-eda`; the new namespace must still be empty.** If either changes
unexpectedly, stop. This is the pause point that previously looked like a
failed move, but it is the intended behavior. During the worker rollout, the
UI may briefly report **workers offline** even while the Job keeps running;
wait for the activation to show **Running** again before restarting it. If it
does not recover, stop and investigate rather than restarting blindly.

Now open **Long running test activation 1** in the UI, choose **Restart**
*once*, and leave it enabled. Wait for the UI to show **Running** again.
Do **not** restart the worker Deployment again or create a second activation.

### 3. Prove the old Job was cleaned up and the new one started

```bash
kubectl --context "$CONTEXT" get jobs -n aap-eda -l app=eda
kubectl --context "$CONTEXT" get pods -n aap-eda -l job-name
kubectl --context "$CONTEXT" get jobs,pods -n eda-activations
```

Expect **no activation Job or Pod** in `aap-eda`, and one **Running** Job and
Pod in `eda-activations`. A new Job can take time to start; if it is not
Running yet, wait and check again. **If an old Job or Pod remains, stop; do
not restart again or claim the demo succeeded.**

Check the two database process records as a final confirmation:

```bash
kubectl --context "$CONTEXT" exec -n aap-eda deployment/eda-api -- \
  aap-eda-manage shell -c '
from aap_eda.core.models import Activation
a = Activation.objects.get(name="Long running test activation 1")
print("Activation:", a.status)
for p in a.activation_processes.order_by("id"):
    print("Process:", p.id, p.status, p.activation_job_namespace)
'
```

Expect `Activation: running`; the old process is `stopped` in `aap-eda`,
and the latest process is `running` in `eda-activations`. Startup `DEBUG`
messages are unrelated. This verifies *placement and cleanup*, not the exact
order of Kubernetes events or uninterrupted event delivery.

If you want to finish the demo, disable the activation in the UI and repeat
the three `get` commands above. Both namespaces should then have no
activation Job or Pod. Leave the disposable cluster in place for review.

## One-time setup (fresh cluster)

**Skip this section on an already-running cluster.** It explains why the
server image needs an explicit PoC tag; Kind previously kept an older image
under `localhost/aap-eda:latest` even after `task kind:build` succeeded.
Prerequisites: Podman, Kind, `kubectl`, Kustomize, Task, and access to the
rulebook image/sample project. The playbook option below also requires
`ansible-playbook` and the `ansible.eda` collection. Use a Bash terminal from
the repository root.

1. If `tools/deploy/environment.properties` does not exist in this checkout,
   create your **ignored local copy** from
   `tools/deploy/environment.properties.example`. Set
   `EDA_WEBSOCKET_BASE_URL` to
   `ws://eda-ws.aap-eda.svc.cluster.local:8000` and add
   `#EDA_ACTIVATION_JOB_NAMESPACE=eda-activations` as a **comment** (the
   example does not define it). Do not commit this local file or overwrite
   existing local settings.
   Check that the resulting `tools/deploy/environment.properties` has
   `#EDA_ACTIVATION_JOB_NAMESPACE=eda-activations` (commented out) and
   `EDA_WEBSOCKET_BASE_URL=ws://eda-ws.aap-eda.svc.cluster.local:8000`.
   `tools/deploy/eda-ws/deployment.yaml` must set
   `EDA_WORKER_KIND=websocket`. Stop if these differ; don't edit the demo
   Deployment YAML just to follow these steps.
2. Choose an **unused**, disposable cluster name. The commands below use
   `eda-migration-demo`; if it already exists, **do not recreate or clean it**.

```bash
export DEMO_CLUSTER=eda-migration-demo
export KUBECONFIG=/tmp/eda-migration-demo.kubeconfig
export CONTEXT=kind-eda-migration-demo
export KIND_EXPERIMENTAL_PROVIDER=podman
unset DOCKER_HOST
kind get clusters  # STOP if eda-migration-demo is listed
kind create cluster --name "$DEMO_CLUSTER" --kubeconfig "$KUBECONFIG"
kind export kubeconfig --name "$DEMO_CLUSTER" --kubeconfig "$KUBECONFIG"
kubectl --context "$CONTEXT" wait \
  node/eda-migration-demo-control-plane --for=condition=Ready --timeout=180s
kubectl --context "$CONTEXT" apply \
  -f tools/deploy/activation-namespace-poc.yaml
```

3. Build once under a **unique tag** and load it through an archive. This
   avoids Kind's observed stale-`:latest` behavior. Allow about 1.1 GB in
   `/tmp/opencode`. Stop if any command fails.

```bash
POC_IMAGE="localhost/aap-eda:poc-$(date +%Y%m%d%H%M%S)"
podman build -t "$POC_IMAGE" -f tools/docker/Dockerfile .
ARCHIVE_DIR=$(mktemp -d /tmp/opencode/eda-poc-XXXXXXXX)
podman save --format docker-archive \
  -o "$ARCHIVE_DIR/image.tar" "$POC_IMAGE"
kind load image-archive "$ARCHIVE_DIR/image.tar" --name "$DEMO_CLUSTER"
podman exec "$DEMO_CLUSTER-control-plane" crictl inspecti "$POC_IMAGE" \
  | grep -F "$(podman image inspect "$POC_IMAGE" --format '{{.Id}}')"
```

4. Deploy EDA, switch the **API first** to the PoC image, and confirm the
   model and migration before switching any workers. `kind:deploy` may briefly
   start Pods using `:latest`; **do not create an activation yet**.

```bash
KIND_CLUSTER_NAME="$DEMO_CLUSTER" task kind:deploy
kubectl --context "$CONTEXT" set image deployment/eda-api \
  "eda-api=$POC_IMAGE" -n aap-eda
kubectl --context "$CONTEXT" rollout status deployment/eda-api \
  -n aap-eda --timeout=300s
kubectl --context "$CONTEXT" exec -n aap-eda deployment/eda-api -- \
  aap-eda-manage shell -c '
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from aap_eda.core.models import RulebookProcess
name = "0077_rulebookprocess_activation_job_namespace"
if "activation_job_namespace" not in {f.name for f in RulebookProcess._meta.fields}:
    raise SystemExit("FAIL: API has old code")
if ("core", name) not in MigrationRecorder(connection).applied_migrations():
    raise SystemExit("FAIL: migration 0077 is not applied")
print("SUCCESS: API code and migration 0077 are ready")
'
```

**Stop unless the success line prints.** Never roll new workers over an
activation started by old code: its process has no recorded Job namespace.

5. Use the *same image* for the other EDA server Deployments; the UI uses a
   separate image and is not changed. This is safe **only before the first
   activation is created**.

```bash
for name in eda-activation-worker eda-default-worker eda-scheduler \
  eda-ws eda-webhook-api; do
  kubectl --context "$CONTEXT" set image "deployment/$name" \
    "$name=$POC_IMAGE" -n aap-eda
  kubectl --context "$CONTEXT" rollout status "deployment/$name" \
    -n aap-eda --timeout=300s
done
kubectl --context "$CONTEXT" get deployments eda-api \
  eda-activation-worker eda-default-worker eda-scheduler \
  -n aap-eda -o custom-columns='NAME:.metadata.name,READY:.status.readyReplicas,IMAGE:.spec.template.spec.containers[0].image'
```

Expect the API and workers to use the same `poc-...` tag and two ready
activation workers. If any image or rollout differs, stop before enabling.
For the sample activation, open the UI in another terminal:

```bash
KUBECONFIG=/tmp/eda-migration-demo.kubeconfig \
  kubectl --context kind-eda-migration-demo rollout status \
  deployment/eda-ui -n aap-eda --timeout=300s
KUBECONFIG=/tmp/eda-migration-demo.kubeconfig \
  kubectl --context kind-eda-migration-demo port-forward \
  -n aap-eda svc/eda-ui 8443:8443
```

Visit <https://localhost:8443> (local certificate warning), sign in as
`admin` / `testpass`, and choose **one** way to create the sample resources:

- **UI:** Create/sync **Eda sample project** from
  <https://github.com/ansible/eda-sample-project>, then create **Upstream
  decision environment** using `quay.io/ansible/ansible-rulebook:main`.
  Create and enable **Long running test activation 1** with
  `range_long_running.yml`. Leave source mappings empty; no action credential
  is needed.
- **Ansible playbook:** Leave the UI port-forward running and, in your first
  terminal, run:

  ```bash
  ansible-galaxy collection list ansible.eda
  ansible-playbook tools/ansible/eda_init.yml \
    -e '{"create_activations": true}'
  ```

  The file is `eda_init.yml` (not `.yaml`). The JSON extra-vars supply a real
  boolean; `-e create_activations=true` may not. The playbook also creates an
  unrelated sample event stream and submits activation creation
  asynchronously, so completion of the command does **not** mean the Job is
  Running.

In either case, wait until the UI shows **Long running test activation 1** as
**Running**, then start at [checkpoint 1](#1-see-where-the-running-activation-is).
Do not run both creation options for this one-activation demo.

**If something goes wrong:** Do not run `task kind:all` on this cluster.
Check the UI status, both namespaces, and the activation-worker logs. If a
process was created by old code, disable it *while old workers are still
running* and verify its Job and Pod are gone before upgrading workers.
The optional invalid-namespace fallback and other negative-path experiments
are outside this recording guide.
