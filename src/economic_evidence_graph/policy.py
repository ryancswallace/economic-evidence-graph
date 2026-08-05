"""Versioned, default-deny source registry and pure action evaluation."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from economic_evidence_graph.domain import PolicyOutcome
from economic_evidence_graph.exceptions import ConfigurationError

_ALLOWED_RULES = frozenset({"allow", "allow_internal", "allow_if_attributed", "deny"})
_INTERNAL_ACTIONS = frozenset(
    {
        "discover_metadata",
        "store_metadata",
        "fetch_content",
        "store_raw",
        "extract_text",
        "embed_content",
    }
)


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    """Validated policy for one configured source."""

    code: str
    display_name: str
    connector: str
    policy_version: int
    enabled: bool
    review_status: str
    hosts: tuple[str, ...]
    path_prefixes: tuple[str, ...]
    allow_external_content_hosts: bool
    requests_per_minute: int
    concurrency: int
    maximum_response_bytes: int
    actions: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Pure policy result before it receives an audit identity and timestamp."""

    source_code: str
    policy_version: int
    action: str
    url: str
    content_class: str
    outcome: PolicyOutcome
    reason: str


class SourceRegistry:
    """Immutable collection of validated source policies."""

    def __init__(self, *, schema_version: str, default_action: str, policies: tuple[SourcePolicy, ...]) -> None:
        if default_action != "deny":
            raise ConfigurationError("source registry default_action must be deny")
        if not schema_version.strip() or not policies:
            raise ConfigurationError("source registry requires a schema version and at least one source")
        by_code = {policy.code: policy for policy in policies}
        if len(by_code) != len(policies):
            raise ConfigurationError("source registry source codes must be unique")
        self.schema_version = schema_version
        self.default_action = default_action
        self._policies = MappingProxyType(by_code)

    @classmethod
    def load(cls, path: Path) -> SourceRegistry:
        """Load and validate a TOML source registry."""
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigurationError(f"cannot read source registry: {path}") from error
        raw_sources = document.get("sources")
        if not isinstance(raw_sources, list):
            raise ConfigurationError("source registry sources must be a list")
        policies = tuple(_source_policy(item) for item in raw_sources)
        return cls(
            schema_version=_text(document, "schema_version"),
            default_action=_text(document, "default_action"),
            policies=policies,
        )

    def get(self, source_code: str) -> SourcePolicy | None:
        """Return one policy, or ``None`` for an unknown source."""
        return self._policies.get(source_code)

    @property
    def policies(self) -> tuple[SourcePolicy, ...]:
        """Return policies in stable source-code order."""
        return tuple(self._policies[code] for code in sorted(self._policies))


class PolicyEngine:
    """Evaluate every action with a deterministic default-deny algorithm."""

    def __init__(self, registry: SourceRegistry) -> None:
        self._registry = registry

    def evaluate(
        self,
        *,
        source_code: str,
        action: str,
        url: str,
        content_class: str,
        attributed: bool = False,
    ) -> PolicyEvaluation:
        """Evaluate an action, host, path, and audience without side effects."""
        policy = self._registry.get(source_code)
        if policy is None:
            return _denied(source_code, 1, action, url, content_class, "source_not_registered")
        if not policy.enabled or policy.review_status != "approved_internal_pilot":
            return _denied(source_code, policy.policy_version, action, url, content_class, "source_not_approved")

        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        valid_scheme = parts.scheme in {"http", "https"} or (
            parts.scheme == "rsync" and action in {"discover_metadata", "store_metadata"}
        )
        if not valid_scheme or not host or parts.username is not None:
            return _denied(source_code, policy.policy_version, action, url, content_class, "invalid_url")
        is_configured_host = host in policy.hosts
        external_content_action = action in {
            "fetch_content",
            "store_raw",
            "extract_text",
            "embed_content",
            "display_snippet",
            "export_excerpt",
        }
        external_allowed = external_content_action and policy.allow_external_content_hosts
        if not is_configured_host and not external_allowed:
            return _denied(source_code, policy.policy_version, action, url, content_class, "host_not_allowed")
        path = parts.path or "/"
        if is_configured_host and not any(path.startswith(prefix) for prefix in policy.path_prefixes):
            return _denied(source_code, policy.policy_version, action, url, content_class, "path_not_allowed")

        rule = policy.actions.get(action, self._registry.default_action)
        if rule == "allow" or (rule == "allow_internal" and action in _INTERNAL_ACTIONS):
            outcome = PolicyOutcome.ALLOW
            reason = "action_allowed"
        elif rule == "allow_if_attributed" and attributed:
            outcome = PolicyOutcome.ALLOW
            reason = "attribution_requirement_met"
        else:
            outcome = PolicyOutcome.DENY
            reason = "action_denied" if rule == "deny" else "action_condition_not_met"
        return PolicyEvaluation(source_code, policy.policy_version, action, url, content_class, outcome, reason)


def _source_policy(value: object) -> SourcePolicy:
    """Validate one source table from TOML."""
    if not isinstance(value, dict):
        raise ConfigurationError("each source registry entry must be a table")
    actions = value.get("actions")
    if not isinstance(actions, dict) or not actions:
        raise ConfigurationError("each source requires an actions table")
    normalized_actions: dict[str, str] = {}
    for key, rule in actions.items():
        if not isinstance(key, str) or not isinstance(rule, str) or rule not in _ALLOWED_RULES:
            raise ConfigurationError("source action rules must be recognized strings")
        normalized_actions[key] = rule
    hosts = _string_tuple(value, "hosts")
    paths = _string_tuple(value, "path_prefixes")
    if any(host != host.lower().rstrip(".") for host in hosts) or any(not path.startswith("/") for path in paths):
        raise ConfigurationError("source hosts and path prefixes must be normalized")
    return SourcePolicy(
        code=_text(value, "code"),
        display_name=_text(value, "display_name"),
        connector=_text(value, "connector"),
        policy_version=_positive_int(value, "policy_version"),
        enabled=_bool(value, "enabled"),
        review_status=_text(value, "review_status"),
        hosts=hosts,
        path_prefixes=paths,
        allow_external_content_hosts=_bool(value, "allow_external_content_hosts"),
        requests_per_minute=_positive_int(value, "requests_per_minute"),
        concurrency=_positive_int(value, "concurrency"),
        maximum_response_bytes=_positive_int(value, "maximum_response_bytes"),
        actions=MappingProxyType(normalized_actions),
    )


def _denied(source: str, version: int, action: str, url: str, content: str, reason: str) -> PolicyEvaluation:
    """Build a denied evaluation."""
    return PolicyEvaluation(source, version, action, url, content, PolicyOutcome.DENY, reason)


def _text(value: Mapping[str, object], key: str) -> str:
    """Read a non-empty string field."""
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ConfigurationError(f"source registry {key} must be a non-empty string")
    return item.strip()


def _string_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Read a non-empty string-list field."""
    item = value.get(key)
    if not isinstance(item, list) or not item or any(not isinstance(entry, str) or not entry for entry in item):
        raise ConfigurationError(f"source registry {key} must be a non-empty string list")
    return tuple(item)


def _positive_int(value: Mapping[str, object], key: str) -> int:
    """Read a positive integer field."""
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise ConfigurationError(f"source registry {key} must be a positive integer")
    return item


def _bool(value: Mapping[str, object], key: str) -> bool:
    """Read a Boolean field."""
    item = value.get(key)
    if not isinstance(item, bool):
        raise ConfigurationError(f"source registry {key} must be Boolean")
    return item
