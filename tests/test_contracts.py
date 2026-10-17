import json
import unittest
from copy import deepcopy
from pathlib import Path

from adaptive_gateway.contracts import (
    ContractError,
    PolicyError,
    Principal,
    check_eligibility,
    parse_chat_request,
    parse_gateway_config,
    required_capabilities,
)


ROOT = Path(__file__).resolve().parents[1]


def offline_config():
    return json.loads((ROOT / "configs/offline.json").read_text())


class RequestContractTests(unittest.TestCase):
    def test_minimal_text_request(self):
        request = parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "Hi"}]})
        self.assertFalse(request.stream)
        self.assertEqual(required_capabilities(request), {"chat_text"})

    def test_unknown_field_is_explicit_error(self):
        with self.assertRaises(ContractError) as caught:
            parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "Hi"}],
                                "response_format": {"type": "json_object"}})
        self.assertEqual(caught.exception.code, "unsupported_parameter")
        self.assertEqual(caught.exception.param, "response_format")

    def test_client_cannot_set_identity_or_budget(self):
        for field in ("identity", "budget", "principal"):
            with self.subTest(field=field), self.assertRaises(ContractError):
                parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "Hi"}], field: "admin"})

    def test_multimodal_content_is_rejected(self):
        with self.assertRaises(ContractError) as caught:
            parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}]})
        self.assertEqual(caught.exception.param, "messages[0].content")

    def test_tool_call_ids_and_arguments_survive(self):
        call = {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}
        request = parse_chat_request({"model": "economy", "stream": True,
                                      "messages": [{"role": "assistant", "content": None, "tool_calls": [call]},
                                                   {"role": "tool", "content": "ok", "tool_call_id": "call_1"}]})
        self.assertEqual(request.messages[0]["tool_calls"][0], call)
        self.assertEqual(required_capabilities(request), {"chat_text", "streaming", "tool_calls"})

    def test_duplicate_tool_call_id_is_rejected(self):
        call = {"id": "same", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        with self.assertRaises(ContractError) as caught:
            parse_chat_request({"model": "economy", "messages": [
                {"role": "assistant", "content": None, "tool_calls": [call, call]}]})
        self.assertEqual(caught.exception.code, "invalid_value")

    def test_boolean_is_not_output_token_limit(self):
        with self.assertRaises(ContractError):
            parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": True})


class ConfigContractTests(unittest.TestCase):
    def test_offline_config_has_two_scoped_aliases(self):
        config = parse_gateway_config(offline_config())
        self.assertEqual(set(config.aliases), {"economy", "capable"})
        self.assertEqual(config.principals[0].identity, "local-operator")
        self.assertEqual(config.aliases["economy"].pricing_version, "fixture-2026-10-15")

    def test_unknown_price_is_not_zero(self):
        raw = offline_config()
        del raw["aliases"]["economy"]["pricing"]["input_per_million"]
        with self.assertRaises(ContractError) as caught:
            parse_gateway_config(raw)
        self.assertEqual(caught.exception.param, "aliases.economy.pricing.input_per_million")

    def test_floating_point_price_is_rejected(self):
        raw = offline_config()
        raw["aliases"]["economy"]["pricing"]["input_per_million"] = 0.25
        with self.assertRaises(ContractError):
            parse_gateway_config(raw)

    def test_endpoint_cannot_embed_credentials(self):
        raw = offline_config()
        raw["aliases"]["economy"]["endpoint"] = "https://secret@example.com"
        with self.assertRaises(ContractError):
            parse_gateway_config(raw)

    def test_denied_alias_does_not_become_eligible(self):
        config = parse_gateway_config(offline_config())
        limited = Principal("limited", "LIMITED_KEY", ("economy",))
        request = parse_chat_request({"model": "capable", "messages": [{"role": "user", "content": "Hi"}]})
        with self.assertRaises(PolicyError) as caught:
            check_eligibility(request, config, limited)
        self.assertEqual(caught.exception.code, "policy_denied")

    def test_capability_and_output_limit_gate(self):
        raw = offline_config()
        raw["aliases"]["economy"]["capabilities"] = ["chat_text"]
        config = parse_gateway_config(raw)
        principal = config.principals[0]
        request = parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "Hi"}], "stream": True})
        with self.assertRaises(PolicyError) as caught:
            check_eligibility(request, config, principal)
        self.assertEqual(caught.exception.code, "capability_denied")
        request = parse_chat_request({"model": "economy", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1025})
        with self.assertRaises(PolicyError) as caught:
            check_eligibility(request, config, principal)
        self.assertEqual(caught.exception.code, "output_limit_exceeded")

    def test_fixture_manifest_ids_are_unique(self):
        manifest = json.loads((ROOT / "evals/fixtures/manifest.json").read_text())
        ids = [case["id"] for case in manifest["cases"]]
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 10)


if __name__ == "__main__":
    unittest.main()
