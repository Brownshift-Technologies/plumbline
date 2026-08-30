import inspect
from types import SimpleNamespace

import pytest

from core.config import load_config
from core.gemini import (
    GeminiModel,
    GeminiResponseEmpty,
    UnrecognisedImageType,
    _sniff_mime_type,
)

# Header bytes below are not invented: each was copied from a real file of
# that format on this machine and re-checked byte-for-byte (see the fix report
# for task 7). WebP headers came from three libwebp-encoded files (lossy VP8 ,
# lossless VP8L, animated VP8X) so the check cannot accidentally key on the
# chunk fourcc at offset 12; the HEIC header is the literal first 24 bytes of an
# iPhone camera capture (all 16 such files on this machine carry major brand
# `heic`); the AVIF header is from a libaom-encoded still.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 8
WEBP_BYTES = b"RIFFL\x00\x00\x00WEBPVP8 " + b"\x00" * 8
WEBP_LOSSLESS_BYTES = b"RIFF\x1c\x00\x00\x00WEBPVP8L" + b"\x00" * 8
WEBP_ANIMATED_BYTES = b"RIFF\xea\x00\x00\x00WEBPVP8X" + b"\x00" * 8
HEIC_BYTES = b"\x00\x00\x00$ftypheic\x00\x00\x00\x00mif1MiPr"
HEIF_BYTES = b"\x00\x00\x00 ftypmif1\x00\x00\x00\x00mif1heic"
AVIF_BYTES = b"\x00\x00\x00 ftypavif\x00\x00\x00\x00avifmif1"
GIF_BYTES = b"GIF89a" + b"\x00" * 8
BMP_BYTES = b"BM6\x0c\x00\x00\x00\x00" + b"\x00" * 8
TIFF_LE_BYTES = b"II*\x00l\x04\x00\x00" + b"\x00" * 8
TIFF_BE_BYTES = b"MM\x00*\x00\x00\x04l" + b"\x00" * 8


# --- pin the assumptions the fake below is built on -----------------------
#
# This codebase has twice shipped a fake built to an assumed signature rather
# than the real one (run_turn/InMemoryRunner.run, enqueue_job/Operation), and
# both times the green test was the thing that certified the bug. These two
# tests read the real google-genai signatures directly, so a library upgrade
# that changes them fails here first rather than silently degrading FakeModels
# below into a fake of a shape that no longer exists.


def test_real_generate_content_is_keyword_only():
    from google.genai.models import Models

    params = inspect.signature(Models.generate_content).parameters
    assert params["model"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["contents"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["config"].kind == inspect.Parameter.KEYWORD_ONLY


def test_real_part_from_bytes_is_keyword_only():
    from google.genai import types

    params = inspect.signature(types.Part.from_bytes).parameters
    assert params["data"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["mime_type"].kind == inspect.Parameter.KEYWORD_ONLY


class FakeModels:
    """Mirrors ``google.genai.models.Models.generate_content``: keyword-only
    ``model``/``contents``/``config``, returning an object exposing ``.text``.
    Signature verified via ``inspect.signature`` against installed
    google-genai==2.19.0 in the two tests above -- not assumed.
    """

    def __init__(self, response_text, response=None):
        self.calls: list[dict] = []
        # `candidates=None` is not padding: GenerateContentResponse.candidates
        # is Optional[list[Candidate]] and really is None on a response with no
        # candidates (checked against the installed types), so the default fake
        # carries the attribute the error path reads rather than making that
        # path AttributeError on the double.
        self._response = (
            response
            if response is not None
            else SimpleNamespace(text=response_text, candidates=None)
        )

    def generate_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response


class FakeGenaiClient:
    def __init__(self, response_text="ok", response=None):
        self.models = FakeModels(response_text, response=response)


def _model(response_text="ok", prefix="a11y", response=None):
    config = load_config(prefix=prefix)
    client = FakeGenaiClient(response_text, response=response)
    return GeminiModel(config, client=client), client


def test_generate_sends_prompt_as_sole_content_when_no_images():
    model, client = _model(response_text="hello")
    result = model.generate("describe this")
    assert result == "hello"
    assert len(client.models.calls) == 1
    assert client.models.calls[0]["contents"] == ["describe this"]


def test_generate_uses_configured_model_name(monkeypatch):
    """Deliberately NOT the DEFAULT_MODEL value.

    This test previously asserted on "gemini-3.5-flash", byte-identical to
    core.config.DEFAULT_MODEL -- so hardcoding `self._model =
    "gemini-3.5-flash"` in GeminiModel and ignoring config.model entirely left
    it green. A sentinel that no default can produce is the only version of
    this test that discriminates. monkeypatch (not a bare os.environ write)
    because a distinct value leaking into the process env would corrupt
    tests/test_config.py's own reads of GEMINI_MODEL.
    """
    from core.config import DEFAULT_MODEL

    sentinel = "sentinel-model-not-the-default"
    assert sentinel != DEFAULT_MODEL  # the whole point of this test
    monkeypatch.setenv("GEMINI_MODEL", sentinel)
    model, client = _model()
    model.generate("hi")
    assert client.models.calls[0]["model"] == sentinel


def test_generate_builds_parts_with_sniffed_mime_types_for_images():
    model, client = _model(response_text="described")
    result = model.generate("what's in these?", images=[PNG_BYTES, JPEG_BYTES])
    assert result == "described"
    contents = client.models.calls[0]["contents"]
    assert contents[0] == "what's in these?"
    assert len(contents) == 3
    assert contents[1].inline_data.mime_type == "image/png"
    assert contents[1].inline_data.data == PNG_BYTES
    assert contents[2].inline_data.mime_type == "image/jpeg"
    assert contents[2].inline_data.data == JPEG_BYTES


def test_generate_raises_when_response_text_is_none():
    model, _client = _model(response_text=None)
    with pytest.raises(GeminiResponseEmpty):
        model.generate("hi")


def test_generate_raises_loudly_for_unrecognised_image_type_without_calling_client():
    model, client = _model()
    garbage = b"not-an-image-at-all"
    with pytest.raises(UnrecognisedImageType):
        model.generate("what is this?", images=[garbage])
    assert client.models.calls == []  # fails fast: no partial API call


def test_sniff_mime_type_recognises_png_and_jpeg():
    assert _sniff_mime_type(PNG_BYTES) == "image/png"
    assert _sniff_mime_type(JPEG_BYTES) == "image/jpeg"


def test_client_construction_passes_vertex_location_not_location(monkeypatch):
    """The footgun this task exists to prevent: gemini-3.5-flash is only
    served from Vertex's `global` location. Passing config.location
    (us-central1, the infra location) instead of config.vertex_location
    would 404 in production while every fake-backed test above stays green.
    """
    from google import genai

    captured = {}

    class RecordingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.models = FakeModels("ok")

    monkeypatch.setattr(genai, "Client", RecordingClient)
    config = load_config(prefix="a11y")
    assert config.location != config.vertex_location  # sanity: must differ to be a real test

    GeminiModel(config)  # no client injected -> constructs for real (patched Client)

    assert captured["location"] == config.vertex_location
    assert captured["location"] != config.location
    assert captured["vertexai"] is True
    assert captured["project"] == config.project_id


# --- finding 1: the formats Gemini takes that the sniffer used to reject ----
#
# Every byte pattern below was checked against a real file, not against a spec
# quoted from memory. The layouts confirmed:
#   WebP   RIFF at 0:4, little-endian size at 4:8, "WEBP" at 8:12, then the
#          chunk fourcc (VP8 / VP8L / VP8X) at 12:16.
#   HEIC   box size at 0:4, "ftyp" at 4:8, major brand at 8:12, compatible
#          brands from 16 on. Real iPhone captures carry major brand `heic`.
# See the fix report for the raw hexdumps.


@pytest.mark.parametrize(
    "data,expected",
    [
        (PNG_BYTES, "image/png"),
        (JPEG_BYTES, "image/jpeg"),
        (WEBP_BYTES, "image/webp"),
        (WEBP_LOSSLESS_BYTES, "image/webp"),
        (WEBP_ANIMATED_BYTES, "image/webp"),
        (HEIC_BYTES, "image/heic"),
        (HEIF_BYTES, "image/heif"),
    ],
)
def test_sniff_mime_type_recognises_every_accepted_format(data, expected):
    assert _sniff_mime_type(data) == expected


def test_sniff_does_not_key_webp_on_the_riff_prefix_alone():
    """A RIFF container that is not WebP (e.g. a WAV) must not sniff as WebP."""
    wav = b"RIFF$\x00\x00\x00WAVEfmt "
    with pytest.raises(UnrecognisedImageType):
        _sniff_mime_type(wav)


def test_sniff_does_not_key_heif_on_the_ftyp_box_alone():
    """An ISO-BMFF file that is not a HEIF image (an MP4) must not sniff as one."""
    mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    with pytest.raises(UnrecognisedImageType) as exc:
        _sniff_mime_type(mp4)
    assert "mp42" in str(exc.value)  # the message names the brand it saw


@pytest.mark.parametrize(
    "data,name",
    [
        (GIF_BYTES, "GIF"),
        (BMP_BYTES, "BMP"),
        (TIFF_LE_BYTES, "TIFF"),
        (TIFF_BE_BYTES, "TIFF"),
        (AVIF_BYTES, "AVIF"),
    ],
)
def test_sniff_rejects_formats_outside_the_accept_list_but_names_them(data, name):
    """Deliberate rejection, with a message that says which format it was.

    These are recognised-then-rejected rather than falling through to the
    generic "unknown bytes" arm, so a caller who hands one over gets told what
    they handed over and what to convert it to -- not a hex dump.
    """
    with pytest.raises(UnrecognisedImageType) as exc:
        _sniff_mime_type(data)
    assert name in str(exc.value)


def test_generate_builds_parts_for_webp_and_heic_end_to_end():
    model, client = _model(response_text="described")
    model.generate("what's in these?", images=[WEBP_BYTES, HEIC_BYTES])
    contents = client.models.calls[0]["contents"]
    assert [p.inline_data.mime_type for p in contents[1:]] == ["image/webp", "image/heic"]
    assert contents[1].inline_data.data == WEBP_BYTES
    assert contents[2].inline_data.data == HEIC_BYTES


# --- finding 5: a str must not escape as a raw TypeError -------------------


def test_sniff_rejects_a_str_with_the_module_s_own_exception():
    """`bytes.startswith(str)` used to leak a bare TypeError from the sniffer.

    A base64 string is the plausible caller mistake given a `list[bytes]`
    contract, so the message names that specific fix.
    """
    with pytest.raises(UnrecognisedImageType) as exc:
        _sniff_mime_type("iVBORw0KGgoAAAANSUhEUgAAACA=")
    message = str(exc.value)
    assert "str" in message
    assert "b64decode" in message


def test_generate_rejects_a_str_image_without_calling_the_client():
    model, client = _model()
    with pytest.raises(UnrecognisedImageType):
        model.generate("what is this?", images=["iVBORw0KGgo="])
    assert client.models.calls == []


def test_sniff_accepts_a_bytearray():
    """`list[bytes]` is the contract, but a bytearray carries the same bytes
    and every check here is value-based, so refusing it would be arbitrary."""
    assert _sniff_mime_type(bytearray(PNG_BYTES)) == "image/png"


# --- findings 3 and 4: what the empty-response error says -------------------


def _response_with(finish_reason, text=None, parts=None):
    from google.genai import types

    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts or []),
                finish_reason=finish_reason,
            )
        ]
    )


def test_empty_response_error_reports_finish_reason_not_just_prompt_feedback():
    """The realistic mid-demo None on a thinking model.

    Constructed against the real types and checked: a candidate whose only
    part is a thought, with finish_reason=MAX_TOKENS, yields `.text is None`
    and `prompt_feedback is None`. Reporting only prompt_feedback therefore
    points away from the cause.
    """
    from google.genai import types

    response = _response_with(
        types.FinishReason.MAX_TOKENS,
        parts=[types.Part(text="internal reasoning", thought=True)],
    )
    assert response.text is None  # the premise of this test, not assumed
    assert response.prompt_feedback is None

    model, _client = _model(response=response)
    with pytest.raises(GeminiResponseEmpty) as exc:
        model.generate("hi")
    assert "MAX_TOKENS" in str(exc.value)


def test_empty_response_error_survives_a_response_with_no_candidates():
    """finish_reason lookup must not IndexError when candidates is [] or None."""
    from google.genai import types

    for candidates in ([], None):
        response = types.GenerateContentResponse(candidates=candidates)
        assert response.text is None
        model, _client = _model(response=response)
        with pytest.raises(GeminiResponseEmpty) as exc:
            model.generate("hi")
        assert "finish_reason" in str(exc.value)


def test_generate_raises_when_the_response_text_is_an_empty_string():
    """A real text part containing '' yields .text == '' -- distinct from None.

    Verified against the real types: a Part(text='') gives `.text == ''`, which
    used to sail through the `is None` check and return '' to a caller that
    asked for a court-summons explanation. Treated as the same failure as None.
    """
    from google.genai import types

    response = _response_with(types.FinishReason.STOP, parts=[types.Part(text="")])
    assert response.text == ""  # the premise, checked against the real type

    model, _client = _model(response=response)
    with pytest.raises(GeminiResponseEmpty) as exc:
        model.generate("hi")
    assert "empty" in str(exc.value).lower()


# --- finding 6: a bounded wait ---------------------------------------------


def test_generate_passes_an_http_timeout():
    from google.genai import types

    from core.gemini import DEFAULT_TIMEOUT_MS

    model, client = _model()
    model.generate("hi")
    config = client.models.calls[0]["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.http_options.timeout == DEFAULT_TIMEOUT_MS

    # Asserting only the line above would be tautological in exactly the way
    # the model-name test above used to be: it echoes the constant, so setting
    # DEFAULT_TIMEOUT_MS to 120 (0.12 seconds -- the unit slip this value is
    # most likely to suffer) leaves it green. Pin the magnitude too, against
    # the two facts that actually bound it: long enough for a thinking model
    # over several images, short enough to fire before Cloud Run's 300s
    # request ceiling kills the request and hides the cause.
    seconds = DEFAULT_TIMEOUT_MS / 1000
    assert 30 <= seconds < 300, f"implausible timeout budget: {seconds}s"


def test_generate_timeout_is_overridable_per_model():
    config_obj = load_config(prefix="a11y")
    client = FakeGenaiClient("ok")
    model = GeminiModel(config_obj, client=client, timeout_ms=1234)
    model.generate("hi")
    assert client.models.calls[0]["config"].http_options.timeout == 1234


def test_http_options_timeout_is_milliseconds_in_the_real_library():
    """Pins the unit, so a library that switched to seconds fails here rather
    than turning a 120s budget into a 120000s one in production."""
    from google.genai import types

    description = types.HttpOptions.model_fields["timeout"].description
    assert "millisecond" in description.lower()


# --- finding 7: the last place a permissive fake could hide a rejection -----


def test_contents_survive_the_real_google_genai_transformer():
    """Every other test here asserts on the pre-transformer list.

    The real client runs `contents` through `_transformers.t_contents` before
    it ever hits the wire, so a list this module builds could be rejected there
    while every fake-backed test above stayed green. This runs the real
    transformer over the real constructed contents.
    """
    from google.genai import types
    from google.genai._transformers import t_contents

    model, client = _model()
    model.generate("describe", images=[PNG_BYTES, JPEG_BYTES, WEBP_BYTES, HEIC_BYTES])
    contents = client.models.calls[0]["contents"]

    transformed = t_contents(contents)

    assert len(transformed) == 1
    content = transformed[0]
    assert isinstance(content, types.Content)
    assert content.role == "user"
    assert len(content.parts) == 5
    assert content.parts[0].text == "describe"
    assert [p.inline_data.mime_type for p in content.parts[1:]] == [
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/heic",
    ]
    assert [p.inline_data.data for p in content.parts[1:]] == [
        PNG_BYTES,
        JPEG_BYTES,
        WEBP_BYTES,
        HEIC_BYTES,
    ]
