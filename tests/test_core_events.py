import base64
import json
import sys

import pytest
from google.api_core import operation as api_core_operation
from google.longrunning import operations_pb2

from core.config import load_config
from core.events import InvalidPushError, enqueue_job, parse_pubsub_push


def _push(payload: dict) -> dict:
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"message": {"data": encoded, "messageId": "m1"}}


def _push_bytes(raw: bytes) -> dict:
    return {"message": {"data": base64.b64encode(raw).decode(), "messageId": "m1"}}


def test_parses_base64_json_payload():
    assert parse_pubsub_push(_push({"repo": "acme/site", "pr": 42})) == {
        "repo": "acme/site",
        "pr": 42,
    }


def test_rejects_body_without_message():
    with pytest.raises(InvalidPushError, match="missing message"):
        parse_pubsub_push({})


def test_rejects_undecodable_data():
    with pytest.raises(InvalidPushError, match="undecodable"):
        parse_pubsub_push({"message": {"data": "!!!not-base64!!!"}})


def test_rejects_non_json_payload():
    encoded = base64.b64encode(b"plain text").decode()
    with pytest.raises(InvalidPushError, match="not JSON"):
        parse_pubsub_push({"message": {"data": encoded}})


# Task 8's web layer acks on InvalidPushError and retries on any other exception,
# so an escaping TypeError, UnicodeDecodeError, AttributeError or RecursionError
# is an infinitely redelivered poison message. Each case below is one such escape
# that was found and closed -- collectively they are evidence for the invariant,
# not a proof of it, since no test can enumerate every body the network can send.


@pytest.mark.parametrize("data", [7, None, {}, [], True])
def test_rejects_data_that_is_not_a_string(data):
    # base64.b64decode raises TypeError, not ValueError, for these.
    with pytest.raises(InvalidPushError, match="undecodable"):
        parse_pubsub_push({"message": {"data": data}})


@pytest.mark.parametrize("message", [["data"], "data", [1, 2], 7, None, "", []])
def test_rejects_a_message_that_is_not_a_dict(message):
    # message["data"] on a list or a string raises TypeError.
    with pytest.raises(InvalidPushError, match="missing message"):
        parse_pubsub_push({"message": message})


@pytest.mark.parametrize("body", [["message"], "message", 7, None])
def test_rejects_a_body_that_is_not_a_dict(body):
    # body.get on a non-dict raises AttributeError.
    with pytest.raises(InvalidPushError, match="push body is not a JSON object"):
        parse_pubsub_push(body)


def test_rejects_valid_base64_that_is_not_utf8():
    # Pub/Sub message data is arbitrary bytes, so this needs no malicious
    # publisher. json.loads raises UnicodeDecodeError, which is a ValueError
    # but not a JSONDecodeError.
    with pytest.raises(InvalidPushError, match="not JSON"):
        parse_pubsub_push(_push_bytes(b"\x80\x81"))


@pytest.mark.parametrize("raw", [b"null", b"[1, 2]", b'"hi"', b"7", b"true"])
def test_rejects_json_that_is_not_an_object(raw):
    # These decode fine but are not dicts, so the declared `-> dict` return type
    # would be a lie and the caller's first payload["run_id"] would raise
    # TypeError or KeyError outside InvalidPushError.
    with pytest.raises(InvalidPushError, match="payload is not a JSON object"):
        parse_pubsub_push(_push_bytes(raw))


def test_rejects_json_nested_past_the_recursion_limit():
    # json.loads raises RecursionError on deeply nested JSON, and RecursionError's
    # MRO is RuntimeError -> Exception: `except ValueError` does not reach it.
    # Depth is derived from the live recursion limit rather than hardcoded --
    # parsing depth D from a stack already D_0 frames deep needs D_0 + D <= limit,
    # so limit + 100 overflows from any caller, on any interpreter, whatever the
    # limit has been set to. The resulting push body is a few KB, so this is not
    # an exotic payload: Pub/Sub's own cap is 10 MB.
    depth = sys.getrecursionlimit() + 100
    body = _push_bytes(b"[" * depth + b"]" * depth)
    assert len(json.dumps(body).encode()) < 10 * 1024
    with pytest.raises(InvalidPushError, match="not JSON"):
        parse_pubsub_push(body)


OPERATION_NAME = "projects/total-fiber-399801/locations/us-central1/operations/run-1"


class FakeJobsClient:
    """Stands in for google.cloud.run_v2.JobsClient.

    run_job returns a *real* google.api_core.operation.Operation rather than a
    hand-rolled stand-in. A hand-rolled one previously defined `operation` as a
    method, which is wrong -- it is a property on the real class -- and the
    green test certified the bug. Building the genuine object makes that class
    of divergence impossible here.
    """

    def __init__(self):
        self.requests = []

    def run_job(self, request):
        self.requests.append(request)
        from google.cloud.run_v2.types import execution as execution_types

        return api_core_operation.Operation(
            operations_pb2.Operation(name=OPERATION_NAME),
            refresh=lambda: None,
            cancel=lambda: None,
            result_type=execution_types.Execution,
            metadata_type=execution_types.Execution,
        )


def test_enqueue_job_targets_the_right_job_and_passes_args():
    client = FakeJobsClient()
    config = load_config(prefix="a11y")
    operation_name = enqueue_job(config, "a11y-worker", {"run_id": "r1"}, client=client)
    request = client.requests[0]
    assert request["name"] == (
        "projects/total-fiber-399801/locations/us-central1/jobs/a11y-worker"
    )
    overrides = request["overrides"]["container_overrides"][0]
    assert overrides["args"] == ['{"run_id": "r1"}']
    assert operation_name == OPERATION_NAME


def test_run_job_returns_an_operation_whose_operation_is_a_property():
    # The regression guard, stated outright: enqueue_job reads `.operation` as
    # an attribute. If anyone re-introduces `.operation()`, this pins down why
    # it fails against the real client rather than against a fake.
    assert isinstance(api_core_operation.Operation.operation, property)
    returned = FakeJobsClient().run_job({"name": "n"})
    assert isinstance(returned, api_core_operation.Operation)
    assert returned.operation.name == OPERATION_NAME
    with pytest.raises(TypeError, match="not callable"):
        returned.operation()
