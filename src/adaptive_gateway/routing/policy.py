"""M1 fixed-alias routing. Adaptive decisions are a later milestone."""

from adaptive_gateway.contracts import ChatRequest, GatewayConfig, ModelAlias, Principal, check_eligibility

POLICY_VERSION = "fixed-v1"


def route(request: ChatRequest, config: GatewayConfig, principal: Principal) -> tuple[ModelAlias, str]:
    return check_eligibility(request, config, principal), "requested_alias"
