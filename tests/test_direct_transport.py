import json
import unittest
from pathlib import Path

import httpx

from adaptive_gateway.contracts import check_eligibility, parse_chat_request, parse_gateway_config
from adaptive_gateway.providers.direct import (
    DirectTransport,
    ProviderError,
    StreamEnd,
    TextDelta,
    ToolCallDelta,
)


ENDPOINT = "https://fixture.invalid/v1/chat/completions"


def model():
    raw = json.loads((Path(__file__).resolve().parents[1] / "configs/offline.json").read_text())
    raw["aliases"]["economy"]["endpoint"] = ENDPOINT
    config = parse_gateway_config(raw)
    return config, config.aliases["economy"]


def request(*, stream=False):
    return parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "Hi"}], "stream": stream})


def response(*, usage=True, tool=False):
    call = {"id": "call_7", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}
    return {"id": "chatcmpl-fixture", "object": "chat.completion", "created": 0,
            "model": "fixture/economy-v1", "choices": [{"index": 0, "message": {"role": "assistant", "content": None if tool else "Hello",
                                                            **({"tool_calls": [call]} if tool else {})},
                                                       "finish_reason": "tool_calls" if tool else "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5} if usage else None}


def sse(*events):
    return "".join("data: " + (json.dumps({"id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 0,
                                              "model": "fixture/economy-v1", **event}) if isinstance(event, dict) else event)
                   + "\n\n" for event in events).encode()


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, fail_after=False):
        self.chunks = chunks
        self.fail_after = fail_after
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.fail_after:
            raise httpx.ReadError("injected read failure")

    async def aclose(self):
        self.closed = True


class DirectTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_and_missing_usage(self):
        _, alias = model()
        seen = []

        async def handler(req):
            seen.append(json.loads(req.content))
            self.assertEqual(req.headers["authorization"], "Bearer fixture-key")
            return httpx.Response(200, json=response(usage=len(seen) == 1))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = DirectTransport(client, "fixture-key")
            first = await transport.complete(request(), alias)
            second = await transport.complete(request(), alias)
        self.assertEqual(first.message["content"], "Hello")
        self.assertEqual(first.usage.total_tokens, 5)
        self.assertIsNone(second.usage)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["model"], alias.model_id)

    async def test_tool_call_id_and_arguments(self):
        _, alias = model()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response(tool=True)))) as client:
            result = await DirectTransport(client, "fixture-key").complete(request(), alias)
        call = result.message["tool_calls"][0]
        self.assertEqual((call["id"], call["function"]["arguments"]), ("call_7", '{"path":"a.py"}'))
        self.assertEqual(result.finish_reason, "tool_calls")

    async def test_usage_details_survive_for_later_accounting(self):
        _, alias = model()
        payload = response()
        payload["usage"]["prompt_tokens_details"] = {"cached_tokens": 1}
        payload["usage"]["completion_tokens_details"] = {"reasoning_tokens": 2}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
            result = await DirectTransport(client, "fixture-key").complete(request(), alias)
        self.assertEqual(result.usage.raw["prompt_tokens_details"]["cached_tokens"], 1)
        self.assertEqual(result.usage.raw["completion_tokens_details"]["reasoning_tokens"], 2)

    async def test_fragmented_tool_call_and_final_usage(self):
        _, alias = model()
        fragments = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_7", "type": "function",
                                                     "function": {"name": "read_file", "arguments": '{"path":"'}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'a.py"}'}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}},
            "[DONE]",
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=sse(*fragments)))) as client:
            events = [event async for event in DirectTransport(client, "fixture-key").stream(request(stream=True), alias)]
        calls = [event for event in events if isinstance(event, ToolCallDelta)]
        self.assertEqual("".join(event.arguments for event in calls), '{"path":"a.py"}')
        self.assertEqual(calls[0].call_id, "call_7")
        self.assertIsInstance(events[-1], StreamEnd)
        self.assertEqual(events[-1].finish_reason, "tool_calls")
        self.assertEqual(events[-1].usage.total_tokens, 5)

    async def test_incomplete_stream_is_not_success(self):
        _, alias = model()
        partial = sse({"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=partial))) as client:
            events = []
            with self.assertRaises(ProviderError) as caught:
                async for event in DirectTransport(client, "fixture-key").stream(request(stream=True), alias):
                    events.append(event)
        self.assertEqual(events, [TextDelta("partial")])
        self.assertEqual(caught.exception.code, "incomplete_stream")
        self.assertTrue(caught.exception.outcome_unknown)

    async def test_cancel_closes_upstream_stream(self):
        _, alias = model()
        body = TrackingStream([sse({"choices": [{"delta": {"content": "first"}, "finish_reason": None}]}),
                               sse({"choices": [{"delta": {"content": "second"}, "finish_reason": None}]})])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=body))) as client:
            stream = DirectTransport(client, "fixture-key").stream(request(stream=True), alias)
            self.assertEqual(await anext(stream), TextDelta("first"))
            await stream.aclose()
        self.assertTrue(body.closed)

    async def test_failure_after_content_is_not_retryable(self):
        _, alias = model()
        body = TrackingStream([sse({"choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]})],
                              fail_after=True)
        calls = 0

        def handler(req):
            nonlocal calls
            calls += 1
            return httpx.Response(200, stream=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            received = []
            with self.assertRaises(ProviderError) as caught:
                async for event in DirectTransport(client, "fixture-key").stream(request(stream=True), alias):
                    received.append(event)
        self.assertEqual(received, [TextDelta("partial")])
        self.assertFalse(caught.exception.retryable)
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(calls, 1)

    async def test_transient_failure_is_one_attempt(self):
        _, alias = model()
        calls = 0

        def handler(req):
            nonlocal calls
            calls += 1
            return httpx.Response(503, json={"error": "busy"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as caught:
                await DirectTransport(client, "fixture-key").complete(request(), alias)
        self.assertEqual(calls, 1)
        self.assertTrue(caught.exception.retryable)

    async def test_policy_denial_happens_before_transport(self):
        config, _ = model()
        restricted = config.principals[0]
        denied = parse_chat_request({"model": "other", "messages": [{"role": "user", "content": "Hi"}]})
        with self.assertRaises(ValueError):
            check_eligibility(denied, config, restricted)


if __name__ == "__main__":
    unittest.main()
