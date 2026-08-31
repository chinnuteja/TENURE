# TENURE runtime identities

The Cloud Run control room uses:

```text
tenure-control-room@PROJECT_ID.iam.gserviceaccount.com
```

Required project roles:

- `roles/aiplatform.user`
- `roles/datastore.user`
- `roles/pubsub.publisher`
- `roles/modelarmor.user` and `roles/modelarmor.viewer`
- `roles/telemetry.writer`
- `roles/serviceusage.serviceUsageConsumer`

It receives `roles/secretmanager.secretAccessor` only on
`tenure-supervisor-envelope-key`, not across the project.

The separately deployed Supervisor support service account is:

```text
tenure-supervisor@PROJECT_ID.iam.gserviceaccount.com
```

It has `roles/aiplatform.user`, read-only Firestore access, and access only to the
same pinned secret. The managed Subject and Supervisor Agent Runtime resources use
`AGENT_IDENTITY`, producing a different SPIFFE-backed principal for each reasoning
engine. They do not share either service account.

Never grant these runtime identities Owner, Editor, IAM administrator, Secret
Manager administrator, Pub/Sub subscriber, promotion, credential-minting, scope
expansion, or ledger-deletion authority.

Firestore security rules deny browser access. Cloud Run authenticates through its
service identity and the server SDK; the public dashboard reads only through
TENURE's API.
