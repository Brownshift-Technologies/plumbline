import base64
import json

from core.config import Config


class InvalidPushError(ValueError):
    """A Pub/Sub push body that cannot be turned into a payload."""


def parse_pubsub_push(body: object) -> dict:
    """Decode a Pub/Sub push envelope into the JSON object it carries.

    The web layer that calls this acks 204 on InvalidPushError, which is the
    documented outcome for a poison message: a non-2xx would make Pub/Sub
    redeliver forever. Each check below exists because some plausible body
    reaches that line raising something that is *not* an InvalidPushError. The
    list is what has been found and covered, not a proof that nothing else can
    escape -- the argument is annotated `object` precisely because callers hand
    this whatever the network produced. Because this function does not promise
    that totality, the web layer does not depend on it: it acks anything else
    escaping here too, under its own `event.parse_failed` name at ERROR
    severity. So an escape is a loud core bug rather than a silent
    infinite retry -- which is a reason to keep adding checks here, not a
    reason to stop.

    - `body` not being a dict would raise AttributeError on `.get`.
    - `message` not being a dict, or having no `data`, would raise TypeError or
      KeyError on the subscript.
    - `message["data"]` not being str/bytes makes base64.b64decode raise
      TypeError, which is not a ValueError and so is caught alongside it
      (binascii.Error, the malformed-base64 error, *is* a ValueError).
    - Deeply nested JSON makes json.loads raise RecursionError, whose MRO is
      RuntimeError -> Exception and so is *not* reached by `except ValueError`.
      Roughly a thousand nested brackets is enough -- a couple of KB, four
      orders of magnitude under Pub/Sub's 10 MB cap. The exact depth depends on
      how much stack the caller has already spent, so no fixed number is quoted
      here or asserted in the tests.
    - Valid base64 of non-UTF-8 bytes makes json.loads raise UnicodeDecodeError,
      which is a ValueError but not a JSONDecodeError.
    - Valid JSON that is not an object (null, a list, a string, a number)
      decodes fine but breaks this function's `-> dict` contract and every
      `payload["..."]` a caller writes.
    """
    if not isinstance(body, dict):
        raise InvalidPushError(f"push body is not a JSON object: {type(body).__name__}")
    message = body.get("message")
    if not isinstance(message, dict) or "data" not in message:
        raise InvalidPushError("missing message or message.data")
    try:
        raw = base64.b64decode(message["data"], validate=True)
    except (TypeError, ValueError) as exc:
        raise InvalidPushError(f"undecodable base64 payload: {exc}") from exc
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise InvalidPushError(f"payload is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidPushError(
            f"payload is not a JSON object: {type(payload).__name__}"
        )
    return payload


def enqueue_job(config: Config, job_name: str, args: dict, client=None) -> str:
    """Start a Cloud Run Job execution, passing `args` as a single JSON argument.

    Cloud Run Jobs are regional, so the job path uses `config.location` (the
    infra location, e.g. us-central1) -- not `config.vertex_location` (the
    Gemini model-access location, e.g. global). Those two fields are
    deliberately distinct; swapping them here would build a job path that
    does not exist.

    Returns the *long-running operation's* resource name
    (`projects/<p>/locations/<l>/operations/<id>`), not the Execution resource
    name. `JobsClient.run_job` returns a `google.api_core.operation.Operation`
    whose `.operation` is a **property** holding the underlying
    `google.longrunning.Operation` message -- calling it raises
    `TypeError: 'Operation' object is not callable`.

    The Execution is reachable as `Operation.metadata` (run_job passes
    `metadata_type=run_v2.Execution`), but that property returns None whenever
    the LRO carries no metadata field, which would silently break this
    function's `-> str` contract. Callers only log this identifier, and the
    operation name is always present, so that is what is returned.
    """
    if client is None:
        from google.cloud import run_v2

        client = run_v2.JobsClient()
    request = {
        "name": (
            f"projects/{config.project_id}/locations/{config.location}/jobs/{job_name}"
        ),
        "overrides": {"container_overrides": [{"args": [json.dumps(args)]}]},
    }
    lro = client.run_job(request=request)
    return lro.operation.name


def publish_event(config: Config, event_type: str, payload: dict, client=None) -> str:
    """Publish one Plumbline event to the single Pub/Sub topic
    `core.web.create_app`'s `/events` receiver (`app/main.py`'s `_on_event`)
    is subscribed to -- `run.requested`/`run.step`/`run.finished`, per that
    module's own docstring, all ride this one topic rather than one topic
    each, matched by `event_type` inside the payload rather than by which
    topic delivered it. `job/worker.py` is this function's first real
    caller, publishing `run.finished` once a run reaches a terminal state
    (see that module's own docstring for why `run.step` is deliberately NOT
    published here -- Task 14a's SSE stream reads Firestore directly, on a
    poll, specifically so it does not depend on Pub/Sub fan-out landing on
    the right instance; publishing `run.step` too is real, but out-of-scope,
    work for a later task to pick up, not something this function's shape
    forecloses).

    `client=None` builds the real `google.cloud.pubsub_v1.PublisherClient`
    lazily, deferred to first use for the exact reason `core.store.Store`'s
    own `_client` property defers building a Firestore client (see that
    property's docstring): constructing the real client resolves
    Application Default Credentials immediately, and this module has to
    stay importable -- and every test in this suite has to stay collectable
    -- in an environment with none configured. Every caller in this
    codebase's own tests passes a fake instead, the same discipline
    `enqueue_job`'s own `client=` parameter above already established.

    The topic name is derived from `config.project_id` alone, with no new
    config field: `enqueue_job` above already builds its own resource path
    the same way, and a single, fixed topic name
    (`plumbline-events`, matching `app/main.py`'s comment about the topic
    Task 14a+ wires up) needs nothing more than the project it lives in.

    Returns the published message's id (Pub/Sub's own ack of "this was
    received"), which no caller in this codebase currently inspects but
    which is the honest, real return value `Publisher.publish(...).result()`
    hands back -- swallowing it would throw away the one piece of evidence
    "the publish actually happened" if a caller ever needs it.
    """
    if client is None:
        from google.cloud import pubsub_v1

        client = pubsub_v1.PublisherClient()
    topic_path = f"projects/{config.project_id}/topics/plumbline-events"
    body = json.dumps({"type": event_type, **payload}).encode("utf-8")
    future = client.publish(topic_path, body)
    return future.result()
