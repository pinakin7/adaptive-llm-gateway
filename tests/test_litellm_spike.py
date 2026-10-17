"""Offline LiteLLM SDK comparison using the same fake responses as direct tests.

Run with: PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --group transport-spike --offline
          python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import unittest

import httpx

from test_direct_transport import TrackingStream, model, request, response, sse

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

try:
    import litellm
    from openai import AsyncOpenAI
except ImportError:
    litellm = None
    AsyncOpenAI = None


@unittest.skipIf(litellm is None, "transport-spike dependency group is not installed")
class LiteLLMSpikeTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, http_client, *, stream=False):
        _, alias = model()
        openai_client = AsyncOpenAI(api_key="fixture-key", base_url="https://fixture.invalid/v1",
                                    http_client=http_client, max_retries=0)
        return await litellm.acompletion(model=f"openai/{alias.model_id}",
                                          messages=list(request(stream=stream).messages),
                                          api_key="fixture-key", api_base="https://fixture.invalid/v1",
                                          client=openai_client, max_retries=0, stream=stream,
                                          **({"stream_options": {"include_usage": True}} if stream else {}))

    async def test_text_response_preserves_usage(self):
        seen = []

        async def handler(req):
            seen.append(req)
            return httpx.Response(200, json=response())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            result = await self.call(http_client)
        self.assertEqual(len(seen), 1)
        self.assertEqual(result.choices[0].message.content, "Hello")
        self.assertEqual(result.usage.total_tokens, 5)

    async def test_tool_call_id_and_arguments(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response(tool=True)))) as client:
            result = await self.call(client)
        call = result.choices[0].message.tool_calls[0]
        self.assertEqual((call.id, call.function.arguments), ("call_7", '{"path":"a.py"}'))

    async def test_missing_usage_becomes_zero_in_sdk_result(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response(usage=False)))) as client:
            result = await self.call(client)
        # This is a comparison finding, not our gateway's desired behavior.
        self.assertEqual(result.usage.total_tokens, 0)

    async def test_streamed_tool_fragments(self):
        events = [
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_7", "type": "function",
                                                                  "function": {"name": "read_file", "arguments": '{"path":"'}}]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'a.py"}'}}]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}},
            "[DONE]",
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=sse(*events)))) as client:
            stream = await self.call(client, stream=True)
            chunks = [chunk async for chunk in stream]
        fragments = [call.function.arguments for chunk in chunks for choice in chunk.choices
                     for call in (choice.delta.tool_calls or [])]
        self.assertEqual("".join(fragments), '{"path":"a.py"}')
        self.assertTrue(any(getattr(chunk, "usage", None) and chunk.usage.total_tokens == 5 for chunk in chunks))

    async def test_explicit_close_releases_stream(self):
        body = TrackingStream([sse({"choices": [{"index": 0, "delta": {"content": "first"}, "finish_reason": None}]}),
                               sse({"choices": [{"index": 0, "delta": {"content": "second"}, "finish_reason": None}]})])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=body))) as client:
            stream = await self.call(client, stream=True)
            await anext(stream)
            await stream.aclose()
        self.assertTrue(body.closed)

    async def test_no_sdk_retry_on_transient_failure(self):
        calls = 0

        def handler(req):
            nonlocal calls
            calls += 1
            return httpx.Response(503, json={"error": {"message": "busy", "type": "server_error"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(Exception):
                await self.call(client)
        self.assertEqual(calls, 1)

    async def test_failure_after_content_is_reported_without_retry(self):
        body = TrackingStream([sse({"choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]})],
                              fail_after=True)
        calls = 0

        def handler(req):
            nonlocal calls
            calls += 1
            return httpx.Response(200, stream=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            stream = await self.call(client, stream=True)
            chunks = []
            with self.assertRaises(Exception):
                async for chunk in stream:
                    chunks.append(chunk)
        self.assertTrue(any(choice.delta.content == "partial" for chunk in chunks for choice in chunk.choices))
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
