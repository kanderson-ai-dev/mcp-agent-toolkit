"""Result-envelope tests — round-trip through the discriminated model."""

from __future__ import annotations

from pydantic import TypeAdapter

from mcp_toolkit.result import (
    ErrEnvelope,
    OkEnvelope,
    ToolError,
    ToolResultEnvelope,
    envelope_json,
    err,
    error_result,
    ok,
)

envelope_adapter: TypeAdapter[OkEnvelope | ErrEnvelope] = TypeAdapter(ToolResultEnvelope)


class TestEnvelope:
    def test_ok_round_trip(self) -> None:
        env = ok({"rows": [1, 2], "row_count": 2})
        text = envelope_json(env)
        parsed = envelope_adapter.validate_json(text)
        assert isinstance(parsed, OkEnvelope)
        assert parsed.status == "ok"
        assert parsed.data == {"rows": [1, 2], "row_count": 2}

    def test_err_round_trip(self) -> None:
        env = err("forbidden", "Path escapes the sandbox root: ../x")
        parsed = envelope_adapter.validate_json(envelope_json(env))
        assert isinstance(parsed, ErrEnvelope)
        assert parsed.status == "error"
        assert parsed.code == "forbidden"
        assert "sandbox" in parsed.message

    def test_discriminator_never_confuses_variants(self) -> None:
        parsed = envelope_adapter.validate_json('{"status":"error","code":"x","message":"m"}')
        assert isinstance(parsed, ErrEnvelope)
        parsed_ok = envelope_adapter.validate_json('{"status":"ok","data":null}')
        assert isinstance(parsed_ok, OkEnvelope)


class TestToolError:
    def test_carries_code_and_message(self) -> None:
        e = ToolError("rate_limited", "Rate limit exceeded (3/min)")
        assert e.code == "rate_limited"
        assert "3/min" in str(e)


class TestErrorResult:
    def test_tool_error_maps_to_its_code(self) -> None:
        env = error_result(ToolError("too_large", "File too large"))
        assert env.code == "too_large"

    def test_unknown_exception_maps_to_internal(self) -> None:
        env = error_result(ValueError("boom"))
        assert env.code == "internal"
        assert "boom" in env.message
