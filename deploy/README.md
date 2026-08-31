# TENURE Google Cloud deployment gate

## Verify the repaired execution boundary

This small probe runs local repair code against isolated synthetic collections in
the existing named Firestore database `tenure`. It makes zero Gemini calls, does
not deploy, does not change production records, and caps control transaction
calls at 100. Firestore usage may incur small charges; no exact cost is claimed.

```powershell
.\.venv\Scripts\python.exe deploy\verify_execution_boundary.py --project project-ceca895d-33b0-44b9-b5a --output data\gauntlet\firestore-repair-live.json
```

The result distinguishes live adapter proof from deployed revision proof. Follow
it with an authenticated zero-traffic Cloud Run canary before promoting any
repair revision. Do not run old and repaired revisions against the same test
tenant: old code does not enforce the new authority documents.

The repair-canary verifier is restricted to this service's observed `repair` tag.
It checks the served revision before mutations, invokes the Gemini Supervisor
once (without retrying that POST), tests new traffic while the durable freeze is
active, reconstructs the fleet, then verifies demotion, replay, and an independent
tenant. It never saves the identity token:

```powershell
.\.venv\Scripts\python.exe deploy\verify_repair_canary.py --url https://repair---tenure-control-room-f5uq25dprq-uc.a.run.app --output data\gauntlet\repair-canary-live.json
```

Run only while the tag exists and before promotion. Production traffic cutover
requires explicit participant approval even after the verifier passes.

The authority read and business mutation share a [Firestore transaction](https://docs.cloud.google.com/firestore/native/docs/manage-data/transactions).
Audit receipt creation remains a separate commit. A crash or partial write leaves
the case owned/failed for explicit reconciliation, not automatic reexecution.
Legacy log-only containment is conservatively imported as frozen `OBSERVE`;
deployment must not silently reauthorize those tenants. In-memory local mode is
not disk-durable; only shared sandbox reconstruction is claimed there.

## Verify the live control spine

After creating the resources, run one bounded verification against the exact
project. It writes one Firestore probe event, publishes one synthetic Pub/Sub
incident, sends one prompt-injection check through Model Armor, exports one
trace, and makes one short Gemini call. It never prints secret contents.

```powershell
python deploy/verify_cloud_spine.py --project project-ceca895d-33b0-44b9-b5a
```

The container and application contract are ready now. Do not claim the Cloud proof until the live verification below succeeds.

## Before credits

- Run all tests locally.
- Run the FastAPI dashboard and export `/api/evidence`.
- Build the container locally if Docker is available.
- Keep `cloud_claim: false` in evidence output.

## After billing or hackathon credits are attached

1. Set the target project and region.
2. Enable Cloud Run, Vertex AI, Firestore, Pub/Sub, Secret Manager, Cloud Trace, and Model Armor APIs required by the final design.
3. Create service identities with one-way Supervisor Agent permissions.
4. Replace the local ledger/reasoner/incident publisher through their existing ports.
5. Deploy the current container to Cloud Run.
6. Run the contract tests against Firestore and the live ADK/Gemini supervisor.
7. Run the complete dashboard scenario through the deployed URL.
8. Capture the Cloud Run revision, ADK/Gemini execution, Firestore ledger events, Model Armor denial, and Cloud Trace spans.
9. Only after those checks pass, change the evidence claim from local proof to Cloud proof.

Model Armor template management must use its regional endpoint. The installed gcloud
command can incorrectly reach the global endpoint and return a misleading permission
error. Create or verify TENURE's template with:

```powershell
.\.venv\Scripts\python.exe deploy\create_model_armor.py `
  --project project-ceca895d-33b0-44b9-b5a `
  --location us-central1
```

Example deployment shape after configuration:

```powershell
gcloud run deploy tenure `
  --source . `
  --region us-central1 `
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=true,TENURE_RUNTIME=cloud `
  --allow-unauthenticated
```

The final service should use Firestore rather than the container's ephemeral `/tmp` ledger. The `/tmp` default exists only so the same container can be smoke-tested before the Firestore adapter is selected.
