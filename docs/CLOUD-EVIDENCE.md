# Google Cloud evidence

The project was built and deployed in Google Cloud project
`project-ceca895d-33b0-44b9-b5a` (project number `585333584620`) in `us-central1`.

## Verified components

- **Cloud Run:** service `tenure-control-room`; prior revision
  `tenure-control-room-00011-d28` and repaired canary revision
  `tenure-control-room-repair-3bb34bf`.
- **Vertex AI Agent Engine / Registry:** separate Supervisor, Vendor, Invoice, and Treasury
  runtime identities and registry resources were created.
- **Memory Bank:** Supervisor memory resource
  `projects/585333584620/locations/us-central1/reasoningEngines/1415402967703486464/memories/8766834811634450432`.
- **Firestore:** durable authority, sandbox, and hash-chained ledger adapters.
- **Pub/Sub:** incident escalation adapter.
- **Model Armor:** model-boundary adapter and configured template integration.
- **Cloud Trace:** trace propagation and evidence identifiers.
- **Gemini + ADK:** constrained Supervisor reasoning with typed investigation and recovery
  tools.

The cloud runtime IDs and registry resource names are configuration, not secrets, and are
represented in the deployment tooling. Authenticated smoke tests previously verified the
deployed recovery path, durable reconstruction, replay, tenant isolation, ledger integrity,
and integration evidence.

## Current availability

The linked billing account was later closed/refunded, so the newest UI and native identity
allow/deny proof could not be redeployed. The repository therefore presents the latest UI
locally and preserves exact, reproducible cloud deployment scripts. It does not claim that
the service is currently public or that native Agent Gateway is active.
