# SPDX-License-Identifier: Apache-2.0

from multillm.adapters.openai import (
    build_responses_payload,
    responses_to_anthropic,
    should_use_responses,
)


def test_gpt5_models_use_responses_while_legacy_models_stay_on_chat_completions():
    assert should_use_responses("gpt-5.5") is True
    assert should_use_responses("gpt-5.6-luna") is True
    assert should_use_responses("gpt-4o") is False


def test_responses_payload_carries_reasoning_verbosity_cache_and_state_controls():
    body = {
        "system": "Stable system prefix",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 200,
        "metadata": {
            "multillm_execution": {
                "reasoning_effort": "low",
                "verbosity": "concise",
                "prompt_cache_key": "tenant-safe-key",
                "previous_response_id": "resp_previous",
            }
        },
    }

    payload = build_responses_payload(body, "gpt-5.5")

    assert payload["reasoning"] == {"effort": "low"}
    assert payload["text"]["verbosity"] == "low"
    assert payload["prompt_cache_key"] == "tenant-safe-key"
    assert payload["previous_response_id"] == "resp_previous"
    assert payload["max_output_tokens"] == 200
    assert "metadata" not in payload


def test_responses_payload_uses_native_strict_structured_output():
    schema = {
        "type": "object",
        "properties": {"accepted": {"type": "boolean"}},
        "required": ["accepted"],
        "additionalProperties": False,
    }
    payload = build_responses_payload(
        {
            "messages": [{"role": "user", "content": "verify"}],
            "output_schema": {"name": "verdict", "schema": schema},
        },
        "gpt-5.5",
    )

    assert payload["text"]["format"] == {
        "type": "json_schema",
        "name": "verdict",
        "schema": schema,
        "strict": True,
    }


def test_responses_usage_is_normalized_without_losing_reasoning_or_cache_tokens():
    response = {
        "id": "resp_1",
        "model": "gpt-5.5-2026-01-01",
        "output_text": "Hello from Responses",
        "service_tier": "default",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 40,
            "input_tokens_details": {"cached_tokens": 70},
            "output_tokens_details": {"reasoning_tokens": 25},
        },
    }

    result = responses_to_anthropic(response, "openai/gpt-5-5")
    usage = result["usage"]

    assert result["content"][0]["text"] == "Hello from Responses"
    assert result["model"] == "openai/gpt-5-5"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 40
    assert usage["cache_read_input_tokens"] == 70
    assert usage["reasoning_tokens"] == 25
    assert usage["service_tier"] == "default"
    assert usage["provider_model"] == "gpt-5.5-2026-01-01"
