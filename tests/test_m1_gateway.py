import json
from pathlib import Path
import unittest

import httpx

from adaptive_gateway.contracts import parse_gateway_config
from adaptive_gateway.gateway.api import create_app
from adaptive_gateway.providers.mock import MockProvider
from adaptive_gateway.agent.controller import TOOLS


CONFIG = parse_gateway_config(json.loads((Path(__file__).parents[1] / "configs/offline.json").read_text()))


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = MockProvider()
        self.app = create_app(CONFIG, self.provider, keys={"GATEWAY_LOCAL_KEY": "test-secret"})
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://gateway.local")
        self.request = {"model": "economy", "messages": [{"role": "user", "content": "fix it"}], "tools": TOOLS}
        self.headers = {"Authorization": "Bearer test-secret"}

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_auth_and_validation_precede_dispatch(self):
        for headers, payload, status in [
            ({}, self.request, 401),
            (self.headers, {**self.request, "model": "secret"}, 403),
            (self.headers, {**self.request, "temperature": 1}, 400),
            ({**self.headers, "X-Task-ID": "bad value"}, self.request, 400),
        ]:
            response = await self.client.post("/v1/chat/completions", json=payload, headers=headers)
            self.assertEqual(response.status_code, status)
            self.assertIn("request_id", response.json()["error"])
        self.assertEqual(self.provider.dispatches, 0)

    async def test_tool_response_has_route_and_usage(self):
        response = await self.client.post("/v1/chat/completions", json=self.request, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(data["adaptive_gateway"]["route_reason"], "requested_alias")
        self.assertEqual(data["usage"]["total_tokens"], 20)

    async def test_streamed_tool_arguments_are_fragmented_and_complete(self):
        response = await self.client.post("/v1/chat/completions", json={**self.request, "stream": True},
                                          headers=self.headers)
        self.assertEqual(response.status_code, 200)
        frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(frames[-1], "[DONE]")
        chunks = [json.loads(frame) for frame in frames[:-1]]
        fragments = [chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
                     for chunk in chunks if chunk["choices"][0]["delta"].get("tool_calls")]
        self.assertEqual(json.loads("".join(fragments)), {"path": "calculator.py"})
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")

    async def test_missing_usage_is_not_fabricated(self):
        app = create_app(CONFIG, MockProvider("missing_usage"), keys={"GATEWAY_LOCAL_KEY": "test-secret"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.local") as client:
            response = await client.post("/v1/chat/completions", json=self.request, headers=self.headers)
        self.assertIsNone(response.json()["usage"])

    async def test_scripted_error_has_request_id(self):
        app = create_app(CONFIG, MockProvider("error"), keys={"GATEWAY_LOCAL_KEY": "test-secret"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.local") as client:
            response = await client.post("/v1/chat/completions", json=self.request, headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "mock_failure")
        self.assertEqual(response.headers["x-request-id"], response.json()["error"]["request_id"])
