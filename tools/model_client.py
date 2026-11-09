"""Small JSON chat client; credentials come from process or persistent user environment."""
import json
from datetime import datetime, timezone
import copy
import math
import os
import re
import time
import urllib.error
from urllib.parse import urlsplit
import urllib.request


GENERATION_PARAMETERS = {"temperature", "top_p", "top_k", "min_p", "frequency_penalty", "presence_penalty",
                         "repetition_penalty", "seed", "stop", "max_tokens", "max_completion_tokens", "n",
                         "logprobs", "top_logprobs", "reasoning_effort", "thinking_budget", "chat_template_kwargs"}


class PermanentModelError(RuntimeError):
    """A provider-declared unavailable model must not be sampled again."""


class ModelJSONError(RuntimeError):
    def __init__(self, message, raw_content, metadata):
        super().__init__(message)
        self.raw_content = raw_content
        self.metadata = metadata


def parse_json_content(content):
    """Accept JSON or one whole JSON code fence; never extract from surrounding prose."""
    fenced = re.fullmatch(r"\s*```(?:json)?[ \t]*\r?\n(.*?)\r?\n```[ \t]*\s*", content, re.DOTALL | re.IGNORECASE)
    body = fenced[1] if fenced else content
    try:
        value = json.loads(body)
    except json.JSONDecodeError as error:
        if error.msg != "Invalid control character at" or body[error.pos:error.pos + 1] not in ("\r", "\n"):
            raise
        # A line break inside a field NAME establishes provider wrapping. With
        # only text values affected, keep the original line endings as text.
        token = r'"(?:[^"\\]|\\.)*"'
        wrapped = any("\n" in match[0] or "\r" in match[0]
                      for match in re.finditer(token + r'(?=\s*:)', body, re.DOTALL))
        if re.search(r'\\[\r\n]', body):
            raise

        def line_endings(match):
            return match[0].replace("\r", "" if wrapped else r"\r").replace("\n", "" if wrapped else r"\n")

        def unique_keys(pairs):
            if len(dict(pairs)) != len(pairs):
                raise json.JSONDecodeError("Ambiguous duplicate key in repaired JSON", body, 0)
            return dict(pairs)

        value = json.loads(re.sub(token, line_endings, body, flags=re.DOTALL), object_pairs_hook=unique_keys)
        method = "json_string_line_wraps.v1" if wrapped else "json_string_line_endings.v1"
        return value, {"method": method, "raw_content": content}
    receipt = {"method": "whole_json_fence.v1", "raw_content": content} if fenced else None
    return value, receipt


def generation_parameters(config):
    """Allow sampling controls without allowing identity, messages or auth overrides."""
    value = config.get("request_parameters", {})
    if not isinstance(value, dict) or any(key not in GENERATION_PARAMETERS for key in value):
        raise ValueError("request_parameters may contain only supported generation parameters")
    if config.get("api_format", "openai") not in ("openai", "anthropic"):
        raise ValueError("api_format must be openai or anthropic")
    for key in ("include_thinking_parameter", "json_mode", "omit_temperature"):
        if key in config and type(config[key]) is not bool:
            raise ValueError(key + " must be boolean")
    for key in ("temperature", "top_p", "top_k", "min_p", "frequency_penalty", "presence_penalty", "repetition_penalty"):
        if key in value and (type(value[key]) not in (int, float) or not math.isfinite(value[key])):
            raise ValueError("Generation sampling values must be finite numbers")
    for key in ("seed", "max_tokens", "max_completion_tokens", "top_logprobs", "thinking_budget", "n"):
        if key in value and type(value[key]) is not int:
            raise ValueError("Generation token counts, seed and choice counts must be integers")
    if any(key in value and value[key] <= 0 for key in ("max_tokens", "max_completion_tokens", "thinking_budget")):
        raise ValueError("Generation token limits must be positive")
    if "n" in value and value["n"] != 1:
        raise ValueError("IRON accepts exactly one generated choice per decision")
    if "logprobs" in value and type(value["logprobs"]) is not bool:
        raise ValueError("logprobs must be boolean")
    if "reasoning_effort" in value and (not isinstance(value["reasoning_effort"], str) or not 0 < len(value["reasoning_effort"]) <= 64):
        raise ValueError("reasoning_effort must be a short string")
    if "stop" in value:
        stops = value["stop"] if isinstance(value["stop"], list) else [value["stop"]]
        if any(not isinstance(item, str) for item in stops):
            raise ValueError("stop must be a string or a list of strings")
    if "chat_template_kwargs" in value:
        template = value["chat_template_kwargs"]
        if not isinstance(template, dict) or set(template) - {"enable_thinking"} or any(type(item) is not bool for item in template.values()):
            raise ValueError("Only a boolean enable_thinking may be supplied through chat_template_kwargs")
    return copy.deepcopy(value)


def request_body(config, messages):
    parameters = generation_parameters(config)
    body = {"model": config["model"], "messages": messages, "stream": False,
            "max_tokens": config["max_tokens"], "temperature": config["temperature"]}
    if config.get("api_format") == "anthropic":
        allowed = {"max_tokens", "temperature", "top_p", "top_k", "stop", "reasoning_effort"}
        if set(parameters) - allowed:
            raise ValueError("Unsupported Anthropic generation parameter")
        system, conversation = [], []
        for message in messages:
            if message["role"] == "system" and not conversation:
                system.append(message["content"])
            elif message["role"] in ("user", "assistant"):
                conversation.append(copy.deepcopy(message))
            else:
                raise ValueError("Anthropic adapter requires leading system and user/assistant messages")
        body["messages"] = conversation
        if system:
            body["system"] = "\n\n".join(system)
        body.update({key: value for key, value in parameters.items() if key in {"max_tokens", "temperature", "top_p", "top_k"}})
        if "reasoning_effort" in parameters:
            body["output_config"] = {"effort": parameters["reasoning_effort"]}
        if "stop" in parameters:
            value = parameters["stop"]
            body["stop_sequences"] = value if isinstance(value, list) else [value]
        if config.get("omit_temperature"):
            body.pop("temperature", None)
        return body
    hostname = (urlsplit(config["base_url"]).hostname or "").lower()
    default_thinking_parameter = hostname == "siliconflow.cn" or hostname.endswith(".siliconflow.cn")
    if config.get("include_thinking_parameter", default_thinking_parameter):
        if type(config.get("enable_thinking", False)) is not bool:
            raise ValueError("enable_thinking must be boolean")
        body["enable_thinking"] = config.get("enable_thinking", False)
    if config.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
        if not any("json" in message["content"].lower() for message in messages):
            instruction = "只输出 JSON 对象。"
            if messages and messages[0]["role"] == "system":
                body["messages"] = [{**messages[0], "content": messages[0]["content"] + "\n" + instruction}, *messages[1:]]
            else:
                body["messages"] = [{"role": "system", "content": instruction}, *messages]
    body.update(parameters)
    if config.get("omit_temperature"):
        body.pop("temperature", None)
    # Providers which use max_completion_tokens generally reject both limit keys.
    if "max_completion_tokens" in parameters and "max_tokens" not in parameters:
        body.pop("max_tokens", None)
    return body


def api_key_from_environment(name):
    """Honor process overrides, then Windows persistent current-user variables."""
    value = os.environ.get(name)
    if value or os.name != "nt":
        return value
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, name)
            return value if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str) else None
    except FileNotFoundError:
        return None


def complete(config, messages, attempts=3):
    """One chat completion; provider-variance failures (transport, truncated, malformed JSON, 429/5xx)
    are resampled up to three times because a fresh sample is independent and nothing is applied until
    downstream validation passes. Permanent rejections such as HTTP 4xx auth/balance errors are never retried."""
    key = api_key_from_environment(config["api_key_env"])
    if not key:
        raise ValueError("Model API key environment variable is missing")
    last_error, failed_samples = None, []
    for attempt in range(1, max(1, attempts) + 1):
        try:
            value, metadata = _complete_once(config, messages, key)
            if failed_samples:
                metadata["failed_samples"] = failed_samples
            return value, metadata
        except (RuntimeError, json.JSONDecodeError) as error:
            last_error = error
            if isinstance(error, ModelJSONError):
                failed_samples.append({"raw_content": error.raw_content, "metadata": error.metadata, "error": str(error)})
            text = str(error)
            if isinstance(error, PermanentModelError):
                retryable = False
            elif isinstance(error, json.JSONDecodeError):
                retryable = True
            elif text.startswith("Model HTTP "):
                code = text[len("Model HTTP "):].split(":", 1)[0].strip()
                retryable = code == "429" or code.startswith("5")
            else:
                retryable = True
            if attempt >= max(1, attempts) or not retryable:
                if isinstance(error, ModelJSONError):
                    error.failed_samples = failed_samples
                raise
            time.sleep(2 * attempt)
    raise last_error


def _complete_once(config, messages, key):
    body = request_body(config, messages)
    anthropic = config.get("api_format") == "anthropic"
    headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"} if anthropic else {"Authorization": "Bearer " + key})
    request = urllib.request.Request(config["base_url"].rstrip("/") + ("/messages" if anthropic else "/chat/completions"),
                                     data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={**headers, "Content-Type": "application/json"})
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        with urllib.request.urlopen(request, timeout=config["timeout_seconds"]) as response:
            value = json.load(response)
            trace_id = response.headers.get("x-siliconcloud-trace-id") or response.headers.get("request-id")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace").replace(key, "[redacted]")
        try:
            provider_error = json.loads(detail).get("error", {})
        except (ValueError, AttributeError):
            provider_error = {}
        error_type = PermanentModelError if isinstance(provider_error, dict) and provider_error.get("code") == "model_not_found" else RuntimeError
        raise error_type(f"Model HTTP {error.code}: {detail[:600]}") from None
    except urllib.error.URLError as error:
        raise RuntimeError("Model transport error: " + str(error.reason).replace(key, "[redacted]")) from None
    ended_at = datetime.now(timezone.utc).isoformat()
    elapsed_seconds = round(time.monotonic() - start, 3)
    provider_usage = None
    if anthropic:
        if not isinstance(value, dict) or value.get("stop_reason") != "end_turn" or not isinstance(value.get("content"), list):
            raise RuntimeError("Anthropic model did not finish a complete message")
        content = "".join(block["text"] for block in value["content"] if block.get("type") == "text" and isinstance(block.get("text"), str))
        provider_usage = value.get("usage", {})
        prompt = sum(provider_usage.get(key, 0) for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        completion = provider_usage.get("output_tokens", 0)
        value = {**value, "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                 "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}}
    if not isinstance(value, dict) or not isinstance(value.get("choices"), list) or len(value["choices"]) != 1 or not isinstance(value["choices"][0], dict):
        raise RuntimeError("Model response did not contain one complete choice")
    choice = value["choices"][0]
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if choice.get("finish_reason") != "stop" or not isinstance(content, str):
        raise RuntimeError(f"Model did not finish a complete text response (finish_reason={choice.get('finish_reason')!r}, "
                           f"content_type={type(content).__name__}, reasoning_content_present={isinstance(message, dict) and bool(message.get('reasoning_content'))})")
    metadata = {"requested_model": config["model"], "returned_model": value.get("model"),
                    "response_id": value.get("id"), "trace_id": trace_id,
                    "usage": value.get("usage"), "elapsed_seconds": elapsed_seconds,
                    "started_at": started_at, "ended_at": ended_at,
                    "finish_reason": choice.get("finish_reason")}
    if provider_usage is not None:
        metadata.update(api_format="anthropic", provider_usage=provider_usage)
    if body.get("response_format", {}).get("type") == "json_object" and body["messages"] != messages:
        metadata["json_mode_instruction_added"] = True
    # Preserve the final text and actual usage when JSON fails, without executing it.
    try:
        result, content_normalization = parse_json_content(content)
    except json.JSONDecodeError as error:
        raise ModelJSONError("Model returned malformed JSON: " + str(error) + "; content_prefix=" + repr(content[:100]), content, metadata) from None
    if content_normalization is not None:
        metadata["content_normalization"] = content_normalization
    return result, metadata
