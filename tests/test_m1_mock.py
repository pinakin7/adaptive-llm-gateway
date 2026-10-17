import json
from pathlib import Path
import unittest

from adaptive_gateway.contracts import parse_chat_request, parse_gateway_config
from adaptive_gateway.providers.direct import ProviderError, TextDelta, ToolCallDelta
from adaptive_gateway.providers.mock import MockProvider
from adaptive_gateway.agent.controller import TOOLS


CONFIG = parse_gateway_config(json.loads((Path(__file__).parents[1] / "configs/offline.json").read_text()))


class MockProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_cancels_partial_stream(self):
        provider = MockProvider("text")
        request = parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "hello"}],
                                      "stream": True, "tools": TOOLS})
        stream = provider.stream(request, CONFIG.aliases["economy"])
        self.assertIsInstance(await anext(stream), TextDelta)
        await stream.aclose()
        self.assertTrue(provider.stream_closed)

    async def test_failure_after_content_has_no_end(self):
        provider = MockProvider("partial_stream")
        request = parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "hello"}],
                                      "stream": True, "tools": TOOLS})
        stream = provider.stream(request, CONFIG.aliases["economy"])
        self.assertIsInstance(await anext(stream), TextDelta)
        with self.assertRaises(ProviderError):
            await anext(stream)
        self.assertTrue(provider.stream_closed)

    async def test_bugfix_tool_fragments(self):
        provider = MockProvider()
        request = parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "fix"}],
                                      "stream": True, "tools": TOOLS})
        events = [event async for event in provider.stream(request, CONFIG.aliases["economy"])]
        self.assertEqual(len([event for event in events if isinstance(event, ToolCallDelta)]), 2)
        self.assertFalse(provider.stream_closed)
