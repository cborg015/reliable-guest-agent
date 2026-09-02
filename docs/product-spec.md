# Product specification: evidence-backed guest-request triage

## Problem

Short-term-rental guests often combine several requests, explanations, and
alternatives in one unstructured message. A host must understand the message,
find the relevant reservation facts and policies, and identify missing
information before making a decision.

The product reduces the host's time-to-understanding. It converts an incoming
guest message into an evidence-backed structured case. It never makes the
refund, transfer, or cancellation decision.

## Primary actors

- **Guest:** sends the original message and may later provide clarification or
  withdraw a request item.
- **Host:** reviews the original message and structured case and makes the final
  decision for each request item.
- **Support:** receives cases that cannot be processed safely or whose normal
  clarification limit has been exceeded.
- **System:** validates, redacts, interprets, retrieves context, retries
  recoverable failures, and routes work.

## First vertical slice

The initial scenario is a same-day message containing two possible requests:

- refund
- reservation transfer

The message may contain a sensitive medical explanation. All data is synthetic;
the application has no integration with a booking or payment provider.

## Authority boundary

The AI may propose request items, claimed-reason categories, missing
information, and evidence. These are interpretations, not established facts.

The AI must never:

- approve or deny a request;
- execute or represent a refund, transfer, cancellation, or exception as
  approved;
- infer policy eligibility as a final decision;
- receive the unredacted guest message.

The host or, after escalation, support retains final decision authority.

## Intake and asynchronous processing

The frontend generates an idempotency key when a submission begins, retains it
across automatic retries, and sends it in the intake request header. The API
first authenticates the caller and synchronously verifies that the authenticated
guest is the account that booked the reservation. Only then does it atomically
persist the key and request-payload hash while creating three records:

1. an immutable inbound-message record;
2. an empty case shell;
3. a pending outbox event requesting case processing.

Only after the transaction commits does the API return `message_id`, `case_id`,
and the external status `PROCESSING`. Repeating the same guest-scoped key with
the same payload returns the existing identifiers rather than creating
duplicates. Reusing the key with a different payload returns `409 Conflict`.
The backend retains the idempotency record with the case; it does not rely on a
short expiry window that could allow a delayed retry to create a duplicate.

When the frontend cannot confirm the intake response, it preserves the form and
key and offers both a safe retry and a status check. Status lookup is scoped to
the authenticated guest. Unknown keys and keys associated with a different
guest both return `404`, preventing disclosure that another guest's submission
exists. Temporary lookup failures return `503`; malformed keys return `400`.

Missing or invalid authentication returns `401`. A nonexistent reservation and
a reservation owned by a different booking account return the same generic
`404`, create no case, and store no message text. A reservation-service outage
returns `503`, allowing the frontend to retain the form and retry safely. Host,
listing, policy, ended-stay, and cancelled-reservation conditions do not change
the guest's authority to submit; they are evaluated during background
processing and may route the case to support.

A background worker processes the case from durable checkpoints. The original
message is immediately visible to the host with an `AI PROCESSING` label, but
the host is not notified until the structured case is ready.

## Background workflow

1. **Refresh authoritative reservation context.** Recheck current reservation,
   listing, and host data after synchronous primary-booker authorization. A
   changed reservation refreshes affected downstream context. Invalid host or
   listing state can route the case to support but does not erase the intake.
2. **Redact sensitive information.** Replace personal names, phone numbers,
   email addresses, street addresses, account or payment identifiers,
   unnecessary reservation identifiers, medical-provider identities, and
   detailed medical information with typed placeholders. Preserve relevant
   dates, times, amounts, durations, and counts.
3. **Fail closed on redaction uncertainty.** If the system cannot establish
   that the message is safe for model processing, bypass AI and assign the case
   to support.
4. **Interpret the redacted message.** Propose every supported request item and
   all applicable claimed-reason categories. Multiple request items and reason
   categories may be returned.
5. **Validate evidence.** Associate each proposed interpretation with exact
   supporting text from the redacted message. Unsupported request items remain
   visible with `evidence_status=UNVERIFIED`. Unsupported concern categories
   become `UNKNOWN`.
6. **Retrieve request-specific context.** Using the verified reservation,
   listing, and proposed request types, retrieve current reservation facts,
   applicable policy versions, and relevant procedures.
7. **Assemble the structured case.** Preserve the original message separately
   from the AI-safe redacted message. Display proposed request items, reason
   categories, evidence, missing information, reservation facts, policies, and
   processing warnings.
8. **Make the case actionable.** Mark automated processing complete and notify
   the host. The host verifies the AI interpretation before deciding each
   request item.

## Redaction acceptance example

Input:

```text
Our son Larry had three seizures while driving from 142 Lakeview Drive. His
neurologist, Dr. Smith, told us to return home. Please call me at 407-555-0198.
Could we get a refund or move our reservation?
```

Expected AI-safe output:

```text
Our son [PERSON] had [MEDICAL_DETAIL] while driving from [ADDRESS]. His
[MEDICAL_PROVIDER] told us to return home. Please call me at [PHONE]. Could we
get a refund or move our reservation?
```

## Claimed-reason taxonomy

The first version uses one multi-label list:

- `MEDICAL_SITUATION`
- `TRAVEL_DISRUPTION`
- `WEATHER_EMERGENCY`
- `WORK_CONFLICT`
- `SAFETY_CONCERN`
- `LISTING_CONCERN`
- `UNKNOWN`
- `NONE`

`TRAVEL_DISRUPTION` requires an explicit transportation or logistics problem.
`SAFETY_CONCERN` requires the guest to express a safety concern explicitly.
`NONE` means no reason was stated; an empty or failed classifier result does not
automatically mean `NONE`.

## Request-item lifecycle

Each proposed request becomes an independently trackable item with one of these
statuses:

- `PENDING`
- `MORE_INFO_REQUESTED`
- `ACCEPTED`
- `DENIED`
- `WITHDRAWN`
- `EXPIRED`

`ACCEPTED`, `DENIED`, `WITHDRAWN`, and `EXPIRED` are terminal. The case closes
only when every item is terminal.

Each item tracks two separate routing properties:

- `assigned_to`: the host or support actor responsible for resolution;
- `waiting_on`: the actor whose next action is required.

Invariants:

- `MORE_INFO_REQUESTED` requires `waiting_on=GUEST`.
- After a guest responds, the item returns to `PENDING` and waits on its owner.
- A terminal item requires `waiting_on=NONE`.
- Clarification limits are counted per request item.
- If a host exceeds the clarification limit, the item is assigned to support.

## Case state dimensions

Case state is not represented by one overloaded status.

- `processing_status`: `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, or `FAILED`.
- `current_stage`: reservation validation, redaction, AI interpretation,
  evidence validation, context retrieval, or `NONE`.
- `lifecycle_status`: `OPEN` or `CLOSED`.
- `derived_resolution`: `UNRESOLVED`, `PARTIALLY_RESOLVED`, or `RESOLVED`,
  calculated from request-item statuses.

The case derives the set of actors it is waiting on from its unresolved request
items. It does not store one case-level assignee.

## Retry and escalation policy

Retry counts are maintained per failed stage. Successful AI interpretation is
checkpointed and is not rerun when a later stage fails.

- Timeouts, database connectivity errors, and rate limits are retried with
  backoff and jitter.
- Retried writes require stable idempotency keys.
- A missing policy may be checked once for stale cache state, then goes to
  support; rerunning AI cannot create a missing policy.
- Conflicting active policies go directly to support with both versions and
  effective dates.
- Changed reservation data is refreshed and only affected downstream context
  is revalidated.
- Malformed or nonexistent reservation references are not blindly retried.
- Exhausted recoverable retries assign the case or affected item to support.

Support receives the failed stage, failure category, attempt history, durable
checkpoint, relevant context, and last error—not only raw logs.

## First acceptance criteria

- Duplicate intake calls with the same idempotency key return the same message
  and case identifiers.
- Reusing an idempotency key with a different payload returns `409 Conflict`.
- A failure before intake commit leaves no message, case, outbox, or
  idempotency record; a retry can create the complete intake.
- Status lookup returns another guest's key exactly as it returns an unknown
  key.
- Only the authenticated booking account can create a case; failed reservation
  authorization stores no message text.
- Successful intake returns `202 Accepted` with durable identifiers and the
  external status `PROCESSING`.
- Message, case shell, and outbox event are created atomically.
- The original message never enters the model-provider boundary.
- Redaction uncertainty bypasses AI and routes to support.
- A compound message can produce multiple independently tracked request items.
- Every proposed request item includes verified evidence or an explicit
  `UNVERIFIED` marker.
- No AI-generated output can approve, deny, or execute a guest request.
- Later-stage retries resume from the last durable checkpoint without rerunning
  successful AI work.
- The host can see the original message during processing but is notified only
  when the structured case becomes actionable.
- Tests require no paid API key and are deterministic by default.

## Out of scope for version one

- Real booking-platform, payment, refund, or messaging integrations
- Automatic policy decisions or financial actions
- A production guest or host frontend
- Multi-property tenancy and billing
- AI-generated response drafting
- AI-generated urgency or medical-severity judgments
