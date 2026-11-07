"""Deterministic detectors used by the cheap guardrail layers.

Regex is the wrong tool for judging intent, but the right tool for finding a
literal AWS key in an answer. The LLM-backed layers handle intent; these handle
shapes, run in microseconds, and are the only layers that see raw input.
"""

from __future__ import annotations

import re

#: (label, pattern) pairs for values that must never leave the system.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_access_key", re.compile(r"\baws_secret_access_key\s*[=:]\s*\S{20,}", re.I)),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}={0,2}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("k8s_service_account_token", re.compile(r"\bkubernetes\.io/service-account-token\b")),
    ("generic_assignment", re.compile(
        r"\b(?:password|passwd|secret|api[_-]?key|token|credential)\s*[=:]\s*"
        r"[\"']?[A-Za-z0-9\-._/+]{12,}[\"']?", re.I,
    )),
]

#: PII shapes worth redacting from questions before they reach the model or logs.
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("national_id", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]

#: Requests for secret material. Distinct from SECRET_PATTERNS, which find values.
SECRET_REQUEST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:show|print|dump|reveal|give|get|echo|cat|decode|read)\b[^.?!]{0,40}"
               r"\b(?:secret|password|token|credential|private\s+key|api\s*key)s?\b", re.I),
    re.compile(r"\bkubectl\s+get\s+secret\b[^.?!]{0,60}\b(?:-o|--output)\s*[=]?\s*(?:yaml|json)",
               re.I),
    re.compile(r"\bbase64\s+(?:-d|--decode)\b[^.?!]{0,40}\bsecret\b", re.I),
    re.compile(r"\b(?:contents?|value)s?\s+of\s+the\s+\S{0,20}secret\b", re.I),
]

#: Attempts to override the assistant's instructions or extract its prompt.
INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above|the)\s+"
               r"(?:instructions?|rules?|prompts?)\b", re.I),
    re.compile(r"\bdisregard\s+(?:your|all|the)\s+(?:instructions?|guidelines?|rules?)\b", re.I),
    re.compile(r"\b(?:reveal|show|print|repeat|output)\s+(?:me\s+)?(?:your|the)\s+"
               r"(?:system\s+)?(?:prompt|instructions?|rules)\b", re.I),
    re.compile(r"\byou\s+are\s+now\b[^.?!]{0,40}\b(?:unrestricted|jailbroken|DAN|developer\s+mode)",
               re.I),
    re.compile(r"\b(?:pretend|act\s+as\s+if)\b[^.?!]{0,40}\bno\s+(?:rules|restrictions|policy)\b",
               re.I),
    re.compile(r"</?(?:system|instructions?)>", re.I),
]

#: Cluster-mutating operations. Allowed to be *discussed*, gated when *emitted*.
DESTRUCTIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("delete", re.compile(r"\bkubectl\s+delete\b", re.I)),
    ("drain", re.compile(r"\bkubectl\s+drain\b", re.I)),
    ("scale_to_zero", re.compile(r"\bkubectl\s+scale\b[^\n]*--replicas[= ]0\b", re.I)),
    ("force_delete", re.compile(r"--force\b[^\n]*--grace-period[= ]?0", re.I)),
    ("apply", re.compile(r"\bkubectl\s+(?:apply|replace|patch|edit)\b", re.I)),
    ("helm_uninstall", re.compile(r"\bhelm\s+(?:uninstall|delete|rollback)\b", re.I)),
    ("etcd", re.compile(r"\betcdctl\b[^\n]*\b(?:del|snapshot\s+restore)\b", re.I)),
    ("host_shell", re.compile(r"\brm\s+-rf\s+/(?:\s|$)", re.I)),
]

#: SQL that must never reach the database from generated text.
SQL_WRITE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE"
               r"|COPY|VACUUM|ATTACH|PRAGMA)\b", re.I),
    re.compile(r";\s*\S"),          # stacked statements
    re.compile(r"--|/\*|\*/"),      # comment-based smuggling
    re.compile(r"\binto\s+outfile\b", re.I),
]


def find(patterns: list[tuple[str, re.Pattern[str]]], text: str) -> list[str]:
    """Labels of every pattern that matched."""
    return [label for label, pattern in patterns if pattern.search(text)]


def matches_any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def redact(patterns: list[tuple[str, re.Pattern[str]]], text: str) -> tuple[str, list[str]]:
    """Replace every match with a labelled placeholder."""
    hits: list[str] = []
    for label, pattern in patterns:
        text, count = pattern.subn("[REDACTED:" + label + "]", text)
        if count:
            hits.append(label)
    return text, hits
