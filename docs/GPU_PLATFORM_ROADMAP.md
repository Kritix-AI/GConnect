# GConnect Shared GPU Platform Roadmap

## Product goal

Turn GConnect from a local remote-GPU IDE into a platform where GPU owners can offer compute capacity and developers can submit workloads to that capacity. A workload can target one GPU, several GPUs on one host, or a distributed group of hosts.

The first release should be an invite-only pilot. The design keeps provider and job APIs suitable for adding public discovery, billing, and broader trust controls later.

## Current state and pilot implementation

- The FastAPI daemon provides file APIs, Docker sandbox controls, telemetry, cluster status, and a WebSocket terminal.
- The Tauri app starts a local daemon sidecar and provides a desktop IDE.
- The cluster registry stores remote daemon URLs and bearer tokens. It polls telemetry and adds reported VRAM for display.
- Ray and torchrun examples show how a developer can manually start distributed work.
- A single coordinator daemon can schedule bounded Python jobs locally or to explicitly opted-in registered nodes.
- Providers run each job in a separate Docker container with a read-only script mount, writable output folder, GPU request, CPU/memory/PID limits, timeout, and opt-in network access.
- Admins can issue revocable developer tokens limited to job submit/read/cancel/artifact routes. Job lists and cancellation are scoped to the token; raw developer tokens are returned once and only hashes are stored.
- The jobs-only browser page is `/static/jobs.html`; the full IDE's GPU Jobs panel submits its active Python file.
- The pilot still has no multi-user account system, database-backed/distributed queue, billing, public discovery, outbound provider agent, or automatic multi-host distributed job launcher. Job state and queued source are stored in local JSON/files for this single-daemon pilot.

So current pooled VRAM is a dashboard total. It does not allocate that memory to a job.

## GPU model and cluster behavior

VRAM on separate machines does not become one ordinary CUDA memory pool. A model can span devices when its software supports model sharding or distributed training. GConnect should expose a cluster allocation to the runtime, then use a workload-specific backend such as torchrun/DDP, FSDP, DeepSpeed, or Ray.

Scheduling must consider host boundaries, GPU model and memory, CUDA/runtime compatibility, interconnect bandwidth, current load, provider limits, and whether a workload needs a single host. A distributed job should be rejected or warned about when its communication needs exceed the available network.

## Target architecture

```text
GConnect IDE / CLI
        |
        | HTTPS API: identity, submit, status, logs, cancel
        v
Control plane
  - identity and organization membership
  - provider/node registry and heartbeats
  - job queue, scheduler, reservations, audit events
  - policy and quota enforcement
        |
        | outbound authenticated agent connections
        v
Provider agents -> isolated job runtime -> GPU(s)
        |
        +---- telemetry, logs, exit status, lease renewals ---->
```

The provider agent should establish an outbound connection to the control plane. Provider machines should not need inbound public ports. Jobs receive short-lived credentials scoped to that job; long-lived provider credentials stay on the provider host.

For a pilot, the control plane can be one service backed by PostgreSQL and a durable queue. Keep scheduling and execution behind interfaces so a future multi-region control plane or alternate queue does not require replacing the desktop daemon.

## MVP scope: invite-only GPU sharing

### Provider flow

1. Admin registers a remote daemon with its bearer token over a trusted network, or enables the local provider.
2. Owner explicitly enables sharing for the selected provider node.
3. Admin creates a one-time developer job token and shares it with an invited developer.
4. Developer submits a bounded Python script from the IDE or `/static/jobs.html`.
5. Coordinator chooses one provider, dispatches the job, and tracks its state, logs, cancellation, and output archive.
6. On completion, timeout, or cancellation, the provider removes the container and releases the GPU reservation.

### Developer flow

1. Developer belongs to an approved organization or has an explicit grant from a provider.
2. Developer submits a container image or repository commit, command, resource requirements, input/output references, and requested runtime.
3. Scheduler reserves a matching GPU or a compatible group, queues when none are available, and dispatches the job.
4. IDE/CLI shows queued, starting, running, failed, completed, and cancelled states with logs and artifacts.
5. Developer can cancel a queued or running job; provider can revoke access and stop jobs.

### MVP exclusions

- Public anonymous provider discovery.
- Payments, crypto rewards, or automatic payouts.
- Promises that a distributed cluster behaves as a single CUDA device.
- Arbitrary multi-tenant execution without provider approval and hardened isolation.

## Core data model

- **User:** identity, status, MFA/security settings.
- **Organization:** members and roles (`owner`, `provider`, `developer`, `admin`).
- **ProviderNode:** organization, agent version, health, heartbeat, policy, public inventory.
- **GPUResource:** node, device identifier, model, memory, runtime capabilities, share status.
- **Job:** owner, image/source, command, resource request, state, deadline, logs/artifacts references.
- **Allocation:** job, node/GPU set, lease expiry, reservation state.
- **AuditEvent:** actor, action, resource, timestamp, outcome; never store secrets in event payloads.

Keep credentials and secrets separate from public node inventory. API responses must never return provider enrollment secrets, node credentials, or job secrets.

## Scheduling and distributed execution

Start with single-host jobs and explicit multi-GPU requests. Scheduler eligibility checks should include:

- GPU count/model and available VRAM per device (not just a sum across devices).
- Host-level free memory, CPU, disk, and provider concurrency limit.
- Container image/runtime compatibility and required CUDA capabilities.
- Provider access policy, organization grant, geographic/region policy if enabled.
- Job deadline, priority, queue age, and fair-use quota.

For multi-host allocations, reserve all selected nodes before launching any worker. Use a job-scoped coordinator, authenticated worker rendezvous, health checks, and lease renewals. If a worker disappears, fail or restart the whole distributed job according to an explicit retry policy; do not silently report a partial cluster as healthy.

## Security and operations requirements

Container isolation alone is not a sufficient trust boundary for arbitrary public jobs. For the pilot, allow only approved organizations and provider-approved images/commands. Before public marketplace access, evaluate stronger isolation such as microVMs or dedicated host pools, kernel/GPU device exposure, image provenance, network egress controls, secret injection, storage cleanup, abuse reporting, incident response, and provider data protections.

Required operational controls include:

- Short-lived, scoped job and agent credentials; rotation and revocation.
- TLS for all control-plane and agent traffic.
- Resource caps and runtime deadlines enforced by the provider agent.
- Immutable audit events for enrollment, grants, job lifecycle, and administrative actions.
- Heartbeats, leases, queue metrics, per-job logs, and provider health indicators.
- Explicit workspace/artifact retention and deletion policy.
- No public raw daemon endpoints or reusable provider tokens.

## Suggested implementation sequence

1. **Pilot contract and state model:** job request bounds, provider opt-in, worker APIs, and developer-token scopes are in place.
2. **Single-control-plane job path:** submit, schedule, run, stream logs, cancel, collect exit status, and download output archives are integrated.
3. **Jobs UI:** active-file submission, provider controls, scoped token issuance/revocation, and a jobs-only browser portal are integrated.
4. **Durability and operations:** replace the pilot JSON/file queue with a database-backed queue, define output retention/cleanup, and add audit events.
5. **Provider agent:** replace inbound daemon-token dispatch with outbound enrollment, heartbeat, leases, and revocation.
6. **Single-host multi-GPU:** scripts can request multiple GPUs on one host; add notebook/cell workflows and launcher templates.
7. **Multi-host clusters:** atomic group reservation, worker rendezvous, Ray or torch distributed backend, failure handling, and network-aware eligibility.
8. **Public marketplace readiness:** stronger isolation review, provider reputation/trust, public policies, abuse controls, billing, and support operations.

## Acceptance criteria for the first usable pilot

- An approved provider can enroll a node without opening inbound ports.
- Provider can pause sharing and revoke a developer grant at any time.
- An authorized developer can submit one bounded GPU job and see its lifecycle and logs.
- The scheduler never allocates a GPU to overlapping jobs beyond the provider's declared policy.
- Cancel, timeout, agent disconnect, and control-plane restart release or expire allocations safely.
- A job cannot access another job's credentials, workspace, or artifacts.
- No API reveals reusable provider credentials.
- Existing local GConnect mode continues to work independently of the control plane.

## First code areas to evolve

- `backend/cluster_manager.py`: retain telemetry aggregation; move job allocation and orchestration into separate modules rather than turning this status poller into a scheduler.
- `backend/main.py`: currently single-daemon API wiring; the future control plane should have a separately deployable service boundary.
- `backend/docker_manager.py` and `backend/pty_bridge.py`: local sandbox and interactive terminal, not a public multi-tenant job runner. Add a separate job runtime/agent layer.
- `frontend/js/cluster.js`: current cluster panel is status-oriented; extend it with provider availability and job lifecycle only after backend contracts are stable.
- `workspace/cluster/`: useful distributed-training examples, but manual Ray setup is not a job orchestration service.
