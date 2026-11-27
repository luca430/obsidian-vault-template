"""Typed transport failures, separate from scientific validation and human decisions."""

from __future__ import annotations

import re

from .storage import VaultError


class InferenceError(VaultError):
    code = "provider_failure"
    retryable = False

    def __init__(self, message, *, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class AllowanceExhausted(InferenceError):
    code = "allowance_exhausted"
    retryable = True


class RateLimited(InferenceError):
    code = "rate_limited"
    retryable = True


class ContextExhausted(InferenceError):
    code = "context_exhausted"


class AuthenticationError(InferenceError):
    code = "authentication_failed"


class InvalidModel(InferenceError):
    code = "invalid_model_configuration"


class InvalidOutput(InferenceError):
    code = "invalid_output"
    retryable = True


class OperationDeferred(InferenceError):
    code = "deferred"
    retryable = True


def provider_error(detail, provider, *, code=None):
    text = str(detail)
    lowered = text.casefold()
    kind = (
        AllowanceExhausted
        if re.search(
            r"insufficient_quota|usage_limit_(reached|exceeded)|usagelimitreached|session limit|usage limit|usage allowance|you['’]?ve (hit|reached) your (usage )?limit|credit.{0,25}(exhaust|balance|insufficient)|out of.{0,15}(usage|credits)|billing.{0,20}(limit|quota)|weekly.{0,15}limit|monthly.{0,15}limit|quota.{0,20}(exhaust|exceed)",
            lowered,
        )
        else AuthenticationError
        if re.search(
            r"unauthori[sz]ed|\b401\b|not logged in|authentication|invalid api key|token.{0,20}expired",
            lowered,
        )
        else InvalidModel
        if re.search(
            r"model_not_found|invalid.{0,15}model|model.{0,35}(not found|does not exist|not available|unsupported)|unsupported.{0,15}(reasoning|effort)|invalid.{0,15}effort",
            lowered,
        )
        else ContextExhausted
        if re.search(
            r"context_length_exceeded|context.{0,20}(window|length).{0,20}(exceed|exhaust|limit)|too many (input )?tokens|prompt is too long",
            lowered,
        )
        else RateLimited
        if re.search(
            r"rate_limit|rate limit|too many requests|\b429\b|overloaded_error|\b529\b", lowered
        )
        else InvalidOutput
        if re.search(
            r"max_tokens|max_output_tokens|output.{0,20}(truncat|incomplete)|invalid json|structured[_ ]output|error_max_turns",
            lowered,
        )
        else InferenceError
    )
    match = re.search(r'retry[-_ ]after["\s:=]+(\d+)', lowered)
    delay = int(match[1]) if match else None
    # Provider output is untrusted and may contain secrets/source text. Keep a typed
    # diagnostic here; complete transport output remains in private staging only.
    messages = {
        AllowanceExhausted: "provider usage allowance exhausted; remaining requests are deferred",
        RateLimited: "temporary rate limit; retry during the next eligible synchronization",
        ContextExhausted: "context window exhausted; adjust the input/chunk budget before retrying",
        AuthenticationError: "authentication failed; log in to the configured provider before resuming",
        InvalidModel: "requested model or reasoning effort is unavailable or invalid; correct the agent settings",
        InvalidOutput: "model output is invalid or incomplete; the operation can be retried",
        InferenceError: "provider request failed; inspect private staging diagnostics",
    }
    return kind(
        f"{provider}: {messages[kind]}" + (f" (exit {code})" if code is not None else ""),
        retry_after=delay,
    )
