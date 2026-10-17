"""M0 request and configuration contracts, independent of HTTP and providers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urlparse


class ContractError(ValueError):
    def __init__(self, code: str, param: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.param = param

    def as_error(self) -> dict[str, object]:
        return {"error": {"type": "invalid_request_error", "code": self.code,
                          "param": self.param, "message": str(self)}}


def _object(value: Any, path: str, allowed: set[str], required: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("invalid_type", path, "Expected an object")
    unknown = set(value) - allowed
    if unknown:
        name = sorted(unknown)[0]
        raise ContractError("unsupported_parameter", f"{path}.{name}".lstrip("."),
                            f"Unsupported parameter: {name}")
    missing = required - set(value)
    if missing:
        name = sorted(missing)[0]
        raise ContractError("missing_parameter", f"{path}.{name}".lstrip("."),
                            f"Missing parameter: {name}")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError("invalid_type", path, "Expected a nonempty string")
    return value


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError("invalid_value", path, "Expected a positive integer")
    return value


def _string_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ContractError("invalid_type", path, "Expected a nonempty array")
    result = tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise ContractError("invalid_value", path, "Duplicate values are not allowed")
    return result


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[Mapping[str, Any], ...]
    stream: bool
    max_tokens: int | None
    tools: tuple[Mapping[str, Any], ...]
    tool_choice: str | None


def parse_chat_request(data: Any) -> ChatRequest:
    obj = _object(data, "", {"model", "messages", "stream", "max_tokens", "tools", "tool_choice"},
                  {"model", "messages"})
    model = _string(obj["model"], "model")
    raw_messages = obj["messages"]
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ContractError("invalid_type", "messages", "Expected a nonempty array")
    messages: list[Mapping[str, Any]] = []
    call_ids: set[str] = set()
    for index, raw in enumerate(raw_messages):
        path = f"messages[{index}]"
        message = _object(raw, path, {"role", "content", "tool_calls", "tool_call_id"}, {"role", "content"})
        role = message["role"]
        if role not in ("system", "developer", "user", "assistant", "tool"):
            raise ContractError("invalid_value", f"{path}.role", "Unsupported message role")
        content = message["content"]
        if content is not None and not isinstance(content, str):
            raise ContractError("unsupported_parameter", f"{path}.content", "Only text content is supported")
        if content is None and not (role == "assistant" and "tool_calls" in message):
            raise ContractError("invalid_value", f"{path}.content", "Null content requires assistant tool calls")
        if role == "tool":
            _string(message.get("tool_call_id"), f"{path}.tool_call_id")
            if "tool_calls" in message:
                raise ContractError("invalid_value", f"{path}.tool_calls", "Tool messages cannot call tools")
        elif "tool_call_id" in message:
            raise ContractError("invalid_value", f"{path}.tool_call_id", "Only tool messages have tool_call_id")
        if "tool_calls" in message:
            if role != "assistant":
                raise ContractError("invalid_value", f"{path}.tool_calls", "Only assistant messages can call tools")
            calls = message["tool_calls"]
            if not isinstance(calls, list) or not calls:
                raise ContractError("invalid_type", f"{path}.tool_calls", "Expected a nonempty array")
            for call_index, raw_call in enumerate(calls):
                call_path = f"{path}.tool_calls[{call_index}]"
                call = _object(raw_call, call_path, {"id", "type", "function"}, {"id", "type", "function"})
                call_id = _string(call["id"], f"{call_path}.id")
                if call_id in call_ids:
                    raise ContractError("invalid_value", f"{call_path}.id", "Duplicate tool call ID")
                call_ids.add(call_id)
                if call["type"] != "function":
                    raise ContractError("unsupported_parameter", f"{call_path}.type", "Only function calls are supported")
                function = _object(call["function"], f"{call_path}.function", {"name", "arguments"},
                                   {"name", "arguments"})
                _string(function["name"], f"{call_path}.function.name")
                if not isinstance(function["arguments"], str):
                    raise ContractError("invalid_type", f"{call_path}.function.arguments", "Expected a JSON string")
        messages.append(message)
    stream = obj.get("stream", False)
    if not isinstance(stream, bool):
        raise ContractError("invalid_type", "stream", "Expected a boolean")
    max_tokens = None
    if "max_tokens" in obj:
        max_tokens = _positive_int(obj["max_tokens"], "max_tokens")
    raw_tools = obj.get("tools", [])
    if not isinstance(raw_tools, list):
        raise ContractError("invalid_type", "tools", "Expected an array")
    tools: list[Mapping[str, Any]] = []
    names: set[str] = set()
    for index, raw_tool in enumerate(raw_tools):
        path = f"tools[{index}]"
        tool = _object(raw_tool, path, {"type", "function"}, {"type", "function"})
        if tool["type"] != "function":
            raise ContractError("unsupported_parameter", f"{path}.type", "Only function tools are supported")
        function = _object(tool["function"], f"{path}.function", {"name", "description", "parameters"},
                           {"name", "parameters"})
        name = _string(function["name"], f"{path}.function.name")
        if name in names:
            raise ContractError("invalid_value", f"{path}.function.name", "Duplicate tool name")
        names.add(name)
        if "description" in function:
            _string(function["description"], f"{path}.function.description")
        if not isinstance(function["parameters"], dict):
            raise ContractError("invalid_type", f"{path}.function.parameters", "Expected a JSON Schema object")
        tools.append(tool)
    tool_choice = obj.get("tool_choice")
    if tool_choice is not None and tool_choice not in ("auto", "none", "required"):
        raise ContractError("unsupported_parameter", "tool_choice", "Supported values: auto, none, required")
    if tool_choice == "required" and not tools:
        raise ContractError("invalid_value", "tool_choice", "Required tool choice needs tools")
    return ChatRequest(model, tuple(messages), stream, max_tokens, tuple(tools), tool_choice)


@dataclass(frozen=True)
class ModelAlias:
    model_id: str
    endpoint: str
    capabilities: tuple[str, ...]
    context_tokens: int
    max_output_tokens: int
    pricing_version: str
    currency: str
    input_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True)
class Principal:
    identity: str
    key_env: str
    allowed_aliases: tuple[str, ...]


@dataclass(frozen=True)
class GatewayConfig:
    schema_version: int
    aliases: Mapping[str, ModelAlias]
    principals: tuple[Principal, ...]


class PolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def required_capabilities(request: ChatRequest) -> frozenset[str]:
    capabilities = {"chat_text"}
    if request.stream:
        capabilities.add("streaming")
    if request.tools or any("tool_calls" in message for message in request.messages):
        capabilities.add("tool_calls")
    return frozenset(capabilities)


def check_eligibility(request: ChatRequest, config: GatewayConfig, principal: Principal) -> ModelAlias:
    alias = config.aliases.get(request.model)
    if alias is None or request.model not in principal.allowed_aliases:
        raise PolicyError("policy_denied", "Alias is unavailable to this principal")
    if not required_capabilities(request).issubset(alias.capabilities):
        raise PolicyError("capability_denied", "Alias lacks a required capability")
    if request.max_tokens is not None and request.max_tokens > alias.max_output_tokens:
        raise PolicyError("output_limit_exceeded", "Requested output exceeds alias limit")
    return alias


def _price(value: Any, path: str) -> Decimal:
    if not isinstance(value, str):
        raise ContractError("invalid_type", path, "Price must be a decimal string")
    try:
        price = Decimal(value)
    except InvalidOperation as exc:
        raise ContractError("invalid_value", path, "Invalid decimal price") from exc
    if not price.is_finite() or price < 0:
        raise ContractError("invalid_value", path, "Price must be finite and nonnegative")
    return price


def parse_gateway_config(data: Any) -> GatewayConfig:
    obj = _object(data, "", {"schema_version", "aliases", "principals"},
                  {"schema_version", "aliases", "principals"})
    if obj["schema_version"] != 1 or isinstance(obj["schema_version"], bool):
        raise ContractError("unsupported_version", "schema_version", "Expected schema version 1")
    raw_aliases = obj["aliases"]
    if not isinstance(raw_aliases, dict) or set(raw_aliases) != {"economy", "capable"}:
        raise ContractError("invalid_value", "aliases", "M0 requires economy and capable aliases")
    aliases: dict[str, ModelAlias] = {}
    for alias, raw in raw_aliases.items():
        path = f"aliases.{alias}"
        item = _object(raw, path, {"model_id", "endpoint", "capabilities", "context_tokens",
                                   "max_output_tokens", "pricing"},
                       {"model_id", "endpoint", "capabilities", "context_tokens", "max_output_tokens", "pricing"})
        endpoint = _string(item["endpoint"], f"{path}.endpoint")
        parsed_url = urlparse(endpoint)
        if parsed_url.scheme not in ("https", "mock") or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            raise ContractError("invalid_value", f"{path}.endpoint", "Endpoint must be https or mock, without credentials")
        caps = _string_list(item["capabilities"], f"{path}.capabilities")
        if set(caps) - {"chat_text", "streaming", "tool_calls"} or "chat_text" not in caps:
            raise ContractError("invalid_value", f"{path}.capabilities", "Unsupported or missing capability")
        context = _positive_int(item["context_tokens"], f"{path}.context_tokens")
        output = _positive_int(item["max_output_tokens"], f"{path}.max_output_tokens")
        if output > context:
            raise ContractError("invalid_value", f"{path}.max_output_tokens", "Output limit exceeds context limit")
        pricing = _object(item["pricing"], f"{path}.pricing",
                          {"version", "currency", "input_per_million", "output_per_million"},
                          {"version", "currency", "input_per_million", "output_per_million"})
        currency = _string(pricing["currency"], f"{path}.pricing.currency")
        if len(currency) != 3 or not currency.isupper() or not currency.isalpha():
            raise ContractError("invalid_value", f"{path}.pricing.currency", "Expected ISO 4217 currency code")
        aliases[alias] = ModelAlias(
            _string(item["model_id"], f"{path}.model_id"), endpoint, caps, context, output,
            _string(pricing["version"], f"{path}.pricing.version"), currency,
            _price(pricing["input_per_million"], f"{path}.pricing.input_per_million"),
            _price(pricing["output_per_million"], f"{path}.pricing.output_per_million"),
        )
    raw_principals = obj["principals"]
    if not isinstance(raw_principals, list) or not raw_principals:
        raise ContractError("invalid_type", "principals", "Expected a nonempty array")
    principals: list[Principal] = []
    ids: set[str] = set()
    envs: set[str] = set()
    for index, raw in enumerate(raw_principals):
        path = f"principals[{index}]"
        item = _object(raw, path, {"identity", "key_env", "allowed_aliases"},
                       {"identity", "key_env", "allowed_aliases"})
        identity = _string(item["identity"], f"{path}.identity")
        key_env = _string(item["key_env"], f"{path}.key_env")
        if identity in ids or key_env in envs:
            raise ContractError("invalid_value", path, "Identity and key environment variable must be unique")
        ids.add(identity)
        envs.add(key_env)
        allowed = _string_list(item["allowed_aliases"], f"{path}.allowed_aliases")
        if set(allowed) - set(aliases):
            raise ContractError("invalid_value", f"{path}.allowed_aliases", "Unknown alias")
        principals.append(Principal(identity, key_env, allowed))
    return GatewayConfig(1, aliases, tuple(principals))
