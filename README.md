# Agent Relay — Containerize and Deploy

Agent Relay is a small FastAPI messaging relay for software agents. Agents
register with the API, send tasks to another agent, claim work, execute it on
their own machines, and submit a result. The relay persists agents, tasks, and
delivery attempts. The included worker deterministically returns
`input.upper()`.

The application uses SQLite by default for a zero-configuration local run and
PostgreSQL when `RELAY_DATABASE_URL` or `DATABASE_URL` is set. The PostgreSQL
port uses row-level locking with `FOR UPDATE SKIP LOCKED`; SQLite keeps its
`BEGIN IMMEDIATE` writer transaction.

## Question 1 — Understand the project

The correct architecture answer is:

> **Agents claim tasks from a DB through an HTTP API.**

The relay does not execute submitted task text, use an external message
broker, or store tasks in the browser. Agent processes poll the API, claim one
leased task, execute it locally, and submit a result.

Run the SQLite starter locally:

```bash
uv sync
uv run uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/> and enter an agent token. The liveness and
readiness endpoints are:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

## Question 2 — Register agents and test the task flow

The first acceptance scenario is: register two agents, send a task, have the
recipient claim and complete it, and let the sender read the result. The
sender sees the following final status:

> **`completed`**

The same flow is automated in `integration_test.py`. It calls the real HTTP
API and then queries the configured database directly to verify persistence.
With the local SQLite server running in this repository directory:

```bash
RELAY_BASE_URL=http://127.0.0.1:8000 \
TEST_DATABASE_URL=sqlite:///./agent-relay.db \
uv run pytest -q integration_test.py
```

The deterministic worker can also be used for the dashboard demonstration:

```bash
uv run python main.py worker \
  --base-url http://127.0.0.1:8000 \
  --name uppercase \
  --credentials ./uppercase-credentials.json \
  --worker-id laptop-1 \
  --stop-after 1
```

Credentials are returned only during registration and are ignored by source
control. The credential file is mode `0600` and is excluded by `.gitignore`
and `.dockerignore`.

Run the starter/unit protocol tests with:

```bash
uv run pytest -q test_agent_relay.py
```

## Question 3 — Containerization

The service is containerized with `Dockerfile` and listens on all interfaces
inside the container, so published ports work:

```bash
docker build -t agent-relay:local .
docker run --rm --name agent-relay \
  -p 8000:8000 \
  agent-relay:local
```

The correct Docker option is:

> **`-p`**

Visit <http://127.0.0.1:8000/> and repeat the registration/task flow. Verify
the container endpoint with:

```bash
curl http://127.0.0.1:8000/health
```

## Question 4 — Docker Compose and PostgreSQL

`compose.yaml` starts the API and a PostgreSQL 16 service named `postgres`.
PostgreSQL data is stored in the named `postgres_data` volume. The API waits
for the PostgreSQL health check before starting.

```bash
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/ready
```

The correct hostname from the API container is:

> **`postgres`**

The host-side integration test uses the published PostgreSQL port:

```bash
RELAY_BASE_URL=http://127.0.0.1:8000 \
TEST_DATABASE_URL=postgresql+psycopg://relay:relay_local_password@127.0.0.1:5432/agent_relay \
uv run pytest -q integration_test.py
```

The final assertion in that test reads the completed task from PostgreSQL.
The same can be inspected manually:

```bash
docker compose exec postgres \
  psql -U relay -d agent_relay \
  -c 'select status, input, output from tasks order by created_at desc limit 5;'
```

Stop the stack when finished:

```bash
docker compose down
```

The committed Compose password is a local demo default only; use environment
overrides for any shared installation. No API key or personal credential is
stored in this repository.

## Question 5 — Deploy to Kubernetes with kind

The manifests in `k8s/` contain:

- a PostgreSQL `Deployment` and `Service` named `postgres`;
- a `PersistentVolume` and `PersistentVolumeClaim` for PostgreSQL data;
- an `agent-relay` `Deployment` and `ClusterIP` `Service`;
- HTTP readiness/liveness probes for Agent Relay and an `pg_isready` probe for
  PostgreSQL.

The resource that keeps the requested number of application replicas running
and manages updates is:

> **`Deployment`**

Install `kind` and `kubectl` using their official installation instructions,
then run:

```bash
kind create cluster --name agent-relay
kind load docker-image agent-relay:local --name agent-relay
kubectl apply -k k8s/
kubectl rollout status deployment/postgres --timeout=180s
kubectl rollout status deployment/agent-relay --timeout=180s
kubectl get pods,pvc,svc
```

Forward the dashboard/API service:

```bash
kubectl port-forward service/agent-relay 18000:8000
```

Then open <http://127.0.0.1:18000/>. The API and PostgreSQL can also be
verified through the live integration test. Keep the two port-forwards in
separate terminals:

```bash
kubectl port-forward service/postgres 15432:5432

RELAY_BASE_URL=http://127.0.0.1:18000 \
TEST_DATABASE_URL=postgresql+psycopg://relay:relay_local_password@127.0.0.1:15432/agent_relay \
uv run pytest -q integration_test.py
```

## Question 6 — CI/CD

`.github/workflows/ci.yml` has two jobs:

1. `test` starts PostgreSQL, runs the starter tests against it, starts the API,
   and runs the live HTTP/database integration test.
2. `build-and-deploy` depends on `test`, builds a unique image tag from
   `GITHUB_SHA`, loads that image into a kind cluster, applies the manifests,
   waits for both rollouts, and smoke-tests the deployed service.

The correct behavior when a test fails is:

> **Keep the existing version running and stop the deployment.**

This is enforced by `needs: test`; there is no delete or rollback step that
would disturb an already running version.

Run the workflow locally with `act` after confirming Docker is available:

```bash
docker info
act --version
act push -W .github/workflows/ci.yml \
  -P ubuntu-latest=catthehacker/ubuntu:act-latest \
  --container-options="-v /var/run/docker.sock:/var/run/docker.sock"
```

The Docker socket allows the workflow runner to build images and lets `kind`
create its node containers. The workflow installs `kind` and `kubectl` when
they are not already present.

To reproduce the version-update check, change the dashboard heading in
`dashboard.html` to `Agent Relay v2`, commit the change, and run the workflow
again. The new unique image is loaded into kind, the deployment rolls out, and
the heading can be confirmed with:

```bash
curl -fsS http://127.0.0.1:18000/ | grep -F '<h1>Agent Relay v2'
```

## Project checks

```bash
uv sync
uv run pytest -q
```

The live integration test is skipped unless both `RELAY_BASE_URL` and
`TEST_DATABASE_URL` are set. It is run explicitly in the Compose, Kubernetes,
and CI/CD commands above.
