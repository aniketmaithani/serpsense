"""Prompts and completions never reach the logs, even at DEBUG (AGENTS §7, §8).

The Anthropic SDK logs whole request bodies at DEBUG; a real call through it, answered by a mock
transport, must leave neither the mention text nor the completion in the output.
"""

import anthropic
import httpx2
import pytest

from serpsense.observability import configure_logging

pytestmark = pytest.mark.security

ANSWER = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [{"type": "text", "text": '{"labels": ["ZZ-SECRET-COMPLETION"]}'}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


@pytest.mark.usefixtures("restore_logging")
def test_the_sdk_never_logs_a_prompt_or_completion_at_debug(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="DEBUG", json=True)
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json=ANSWER))
    sdk = anthropic.Anthropic(
        api_key="sk-ant-test-not-a-key",
        base_url="https://api.anthropic.com",
        max_retries=0,
        http_client=httpx2.Client(transport=transport),
    )
    message = sdk.messages.create(
        model="claude-opus-5-5",
        max_tokens=100,
        system="Label mentions.",
        messages=[{"role": "user", "content": "<mention>ZZ-SECRET-MENTION rude driver</mention>"}],
    )
    assert message.content
    out = capsys.readouterr()
    assert "ZZ-SECRET" not in out.out + out.err
