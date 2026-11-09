"""Provider request compatibility and credential handling, using urllib mocks only."""
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import urllib.error

from model_client import api_key_from_environment, complete, parse_json_content, request_body, ModelJSONError
from model_trial import recorded_complete
from showrunner import MODEL_FIELDS, model_settings


CONFIG = {"base_url": "https://api.siliconflow.cn/v1", "model": "test-model", "api_key_env": "IRON_UNIT_TEST_KEY",
          "enable_thinking": False, "temperature": .3, "max_tokens": 1800, "timeout_seconds": 90}
MESSAGES = [{"role": "system", "content": "Only JSON"}, {"role": "user", "content": "Return a small object"}]
TEST_KEY = "test-only-credential-not-a-real-key"


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode("utf-8"))
        self.headers = {"x-siliconcloud-trace-id": "test-trace"}


def completion(content='{"ok":true}', finish="stop"):
    return {"id": "test-response", "model": "test-model", "choices": [
        {"message": {"content": content}, "finish_reason": finish}], "usage": {"total_tokens": 10}}


class ModelClientTests(unittest.TestCase):
    def test_failed_json_is_saved_with_prompt_and_usage_without_a_success_record(self):
        metadata = {"requested_model": CONFIG["model"], "returned_model": CONFIG["model"], "usage": {"total_tokens": 20}}
        error = ModelJSONError("bad JSON", "UNPARSEABLE_MODEL_TEXT", metadata)
        error.failed_samples = [{"raw_content": error.raw_content, "metadata": metadata}]
        with tempfile.TemporaryDirectory() as folder, patch("model_trial.complete", side_effect=error):
            root = Path(folder)
            with self.assertRaises(ModelJSONError):
                recorded_complete(root, "decision_TEST", CONFIG, MESSAGES, "decision", "TEST")
            paths = list((root / "failed_model_calls").glob("*.json"))
            self.assertEqual(len(paths), 1)
            value = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(value["raw_content"], error.raw_content)
            self.assertEqual(value["messages"], MESSAGES)
            self.assertEqual(value["attempts"][0]["metadata"]["usage"]["total_tokens"], 20)
            self.assertFalse((root / "model_calls/decision_TEST.json").exists())

    def test_json_mode_interview_adds_wire_instruction_without_changing_cached_prompt(self):
        messages = [{"role": "system", "content": "现在采访"}, {"role": "user", "content": "输出{country,answer}"}]
        body = request_body(CONFIG, messages)
        self.assertIn("JSON", body["messages"][0]["content"])
        self.assertEqual(messages[0]["content"], "现在采访")
        self.assertEqual(request_body({**CONFIG, "json_mode": False}, messages)["messages"], messages)

    def test_literal_line_breaks_preserve_speech_or_reconstruct_wrapped_keys(self):
        speech = '{"message":"第一行\n第二行","escaped":"a\\nb"}'
        value, receipt = parse_json_content(speech)
        self.assertEqual(value, {"message": "第一行\n第二行", "escaped": "a\nb"})
        self.assertEqual(receipt["raw_content"], speech)
        wrapped = '{"country":"\nITA","pre\ndictions":["控\n制权"],"escaped":"a\\nb"}'
        value, receipt = parse_json_content(wrapped)
        self.assertEqual(value, {"country":"ITA", "predictions":["控制权"], "escaped":"a\nb"})
        self.assertEqual(receipt["method"], "json_string_line_wraps.v1")
        for malformed in ('{"a":1,"a\n":2}', wrapped + "extra", '{"x":"a\tb"}'):
            with self.assertRaises(ValueError):
                parse_json_content(malformed)

    def test_whole_json_fence_preserves_original_without_accepting_extra_prose(self):
        text = '```json\n{"country":"FRA","message":"原样保留"}\n```'
        value, receipt = parse_json_content(text)
        self.assertEqual(value, {"country": "FRA", "message": "原样保留"})
        self.assertEqual(receipt, {"method": "whole_json_fence.v1", "raw_content": text})
        for invalid in ("Here is JSON:\n" + text, text + "\nAdditional instructions", '```json\n{"broken":\n```'):
            with self.assertRaises(ValueError):
                parse_json_content(invalid)
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), \
                patch("model_client.urllib.request.urlopen", return_value=Response(completion(text))):
            result, metadata = complete(CONFIG, MESSAGES, attempts=1)
        self.assertEqual(result, value)
        self.assertEqual(metadata["content_normalization"], receipt)

    def test_anthropic_messages_protocol_auth_effort_and_usage(self):
        config = {**CONFIG, "base_url": "https://provider.test/v1", "api_format": "anthropic",
                  "omit_temperature": True, "json_mode": False,
                  "request_parameters": {"reasoning_effort": "high", "max_tokens": 4096}}
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30, "output_tokens": 3}
        response = {"id": "msg_test", "model": "test-model", "stop_reason": "end_turn", "usage": usage,
                    "content": [{"type": "thinking", "thinking": "DO_NOT_USE_AS_FINAL"}, {"type": "text", "text": '{"ok":true}'}]}
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), \
                patch("model_client.urllib.request.urlopen", return_value=Response(response)) as opener:
            value, metadata = complete(config, MESSAGES, attempts=1)
        request = opener.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://provider.test/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), TEST_KEY)
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertNotIn(TEST_KEY, request.data.decode())
        self.assertEqual(body["system"], MESSAGES[0]["content"])
        self.assertEqual(body["messages"], MESSAGES[1:])
        self.assertEqual(body["output_config"], {"effort": "high"})
        self.assertEqual(body["max_tokens"], 4096)
        for field in ("temperature", "response_format", "enable_thinking", "reasoning_effort"):
            self.assertNotIn(field, body)
        self.assertEqual(value, {"ok": True})
        self.assertEqual(metadata["usage"], {"prompt_tokens": 60, "completion_tokens": 3, "total_tokens": 63})
        self.assertEqual(metadata["provider_usage"], usage)
        self.assertEqual(metadata["finish_reason"], "stop")

    def test_anthropic_truncation_and_reasoning_only_are_never_executed(self):
        config = {**CONFIG, "api_format": "anthropic", "omit_temperature": True}
        for reason, blocks in [("max_tokens", [{"type": "text", "text": '{"ok":true}'}]),
                               ("end_turn", [{"type": "thinking", "thinking": '{"ok":true}'}])]:
            response = {"stop_reason": reason, "content": blocks, "usage": {}}
            with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), \
                    patch("model_client.urllib.request.urlopen", return_value=Response(response)), self.assertRaises(RuntimeError):
                complete(config, MESSAGES, attempts=1)

    def test_unavailable_model_is_not_retried_even_when_provider_uses_503(self):
        body = io.BytesIO(json.dumps({"error": {"code": "model_not_found", "message": "no available channel"}}).encode())
        error = urllib.error.HTTPError("https://provider.test", 503, "Unavailable", {}, body)
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), \
                patch("model_client.urllib.request.urlopen", side_effect=error) as opener, \
                patch("model_client.time.sleep") as wait:
            with self.assertRaisesRegex(RuntimeError, "model_not_found"):
                complete(CONFIG, MESSAGES)
        self.assertEqual(opener.call_count, 1)
        wait.assert_not_called()

    def test_persistent_windows_user_key_is_available_to_existing_processes(self):
        class Key:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        registry = SimpleNamespace(HKEY_CURRENT_USER=1, REG_SZ=1, REG_EXPAND_SZ=2,
                                   OpenKey=lambda *args: Key(), QueryValueEx=lambda *args: (TEST_KEY, 1))
        with patch.dict(os.environ, {}, clear=True), patch("model_client.os.name", "nt"), patch.dict("sys.modules", {"winreg": registry}):
            self.assertEqual(api_key_from_environment(CONFIG["api_key_env"]), TEST_KEY)
        with patch.dict(os.environ, {CONFIG["api_key_env"]: "PROCESS_OVERRIDE"}):
            self.assertEqual(api_key_from_environment(CONFIG["api_key_env"]), "PROCESS_OVERRIDE")

    def test_siliconflow_legacy_body_and_key_only_in_header(self):
        output = io.StringIO()
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), redirect_stdout(output), \
                patch("model_client.urllib.request.urlopen", return_value=Response(completion())) as opener:
            value, metadata = complete(CONFIG, MESSAGES)
        request = opener.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body, {"model": "test-model", "messages": MESSAGES, "stream": False,
                                "max_tokens": 1800, "temperature": .3, "enable_thinking": False,
                                "response_format": {"type": "json_object"}})
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TEST_KEY)
        self.assertNotIn(TEST_KEY, request.full_url)
        self.assertNotIn(TEST_KEY, request.data.decode("utf-8"))
        self.assertNotIn(TEST_KEY, json.dumps(metadata))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(value, {"ok": True})
        start = datetime.fromisoformat(metadata["started_at"])
        end = datetime.fromisoformat(metadata["ended_at"])
        self.assertIsNotNone(start.utcoffset())
        self.assertLessEqual(start, end)
        self.assertGreaterEqual(metadata["elapsed_seconds"], 0)

    def test_provider_thinking_default_uses_actual_hostname(self):
        for base in ("http://localhost:8000/v1", "https://api.example.invalid/v1", "https://api.siliconflow.cn.evil.invalid/v1"):
            with self.subTest(base=base):
                self.assertNotIn("enable_thinking", request_body({**CONFIG, "base_url": base}, MESSAGES))
        for base in ("https://siliconflow.cn/v1", "https://api.siliconflow.cn/v1"):
            self.assertIn("enable_thinking", request_body({**CONFIG, "base_url": base}, MESSAGES))
        explicit = request_body({**CONFIG, "base_url": "http://localhost:8000/v1", "include_thinking_parameter": True}, MESSAGES)
        self.assertIs(explicit["enable_thinking"], False)
        self.assertNotIn("enable_thinking", request_body({**CONFIG, "include_thinking_parameter": False}, MESSAGES))

    def test_json_mode_can_be_omitted_but_returned_content_still_must_be_json(self):
        config = {**CONFIG, "json_mode": False}
        self.assertNotIn("response_format", request_body(config, MESSAGES))
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), \
                patch("model_client.urllib.request.urlopen", return_value=Response(completion("plain text"))):
            with self.assertRaisesRegex(RuntimeError, "malformed JSON"):
                complete(config, MESSAGES, attempts=1)

    def test_generation_controls_merge_without_mutating_config_or_messages(self):
        parameters = {"top_p": .8, "seed": 42, "temperature": .6,
                      "max_completion_tokens": 1200, "chat_template_kwargs": {"enable_thinking": False}}
        config = {**CONFIG, "base_url": "http://localhost:8000/v1", "request_parameters": parameters}
        body = request_body(config, MESSAGES)
        self.assertEqual(body["temperature"], .6)
        self.assertEqual(body["max_completion_tokens"], 1200)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("enable_thinking", body)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        body["chat_template_kwargs"]["enable_thinking"] = True
        self.assertIs(parameters["chat_template_kwargs"]["enable_thinking"], False)
        self.assertEqual(CONFIG["temperature"], .3)
        self.assertEqual(MESSAGES[0]["content"], "Only JSON")

    def test_request_parameters_cannot_override_identity_prompt_or_auth(self):
        protected = {"model": "other", "messages": [], "stream": True, "api_key": TEST_KEY,
                     "headers": {"Authorization": TEST_KEY}, "Authorization": TEST_KEY,
                     "base_url": "https://elsewhere.invalid/v1", "response_format": {"type": "text"},
                     "enable_thinking": True}
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), patch("model_client.urllib.request.urlopen") as opener:
            for key, value in protected.items():
                with self.subTest(field=key), self.assertRaises(ValueError) as error:
                    complete({**CONFIG, "request_parameters": {key: value}}, MESSAGES)
                self.assertNotIn(TEST_KEY, str(error.exception))
            opener.assert_not_called()
        for control in ({"json_mode": "false"}, {"include_thinking_parameter": 1}, {"api_format": "unknown"}, {"omit_temperature": "true"},
                        {"request_parameters": {"n": 2}}, {"request_parameters": {"seed": True}},
                        {"request_parameters": {"chat_template_kwargs": {"messages": []}}}):
            with self.subTest(control=control), self.assertRaises(ValueError):
                request_body({**CONFIG, **control}, MESSAGES)

    def test_incomplete_or_missing_response_is_resampled_then_rejected(self):
        responses = [completion('{"incomplete":', "length"), completion(None), {"choices": []},
                     {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": []}}]},
                     {"choices": [completion()["choices"][0], completion()["choices"][0]]}]
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}):
            for value in responses:
                with self.subTest(response=value), \
                        patch("model_client.urllib.request.urlopen",
                              side_effect=[Response(value) for _ in range(3)]) as opener, \
                        patch("model_client.time.sleep"), self.assertRaises(RuntimeError):
                    complete(CONFIG, MESSAGES)
                self.assertEqual(opener.call_count, 3)

    def test_provider_variance_recovers_on_the_next_sample(self):
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), \
                patch("model_client.urllib.request.urlopen",
                      side_effect=[Response(completion('{"incomplete":', "length")), Response(completion())]) as opener, \
                patch("model_client.time.sleep") as sleeper:
            value, _ = complete(CONFIG, MESSAGES)
        self.assertEqual(value, {"ok": True})
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once_with(2)

    def test_permanent_http_4xx_is_never_resampled(self):
        failure = urllib.error.HTTPError(CONFIG["base_url"], 402, "Payment Required", {}, io.BytesIO(b'{"code":30001}'))
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}), \
                patch("model_client.urllib.request.urlopen", side_effect=failure) as opener, \
                patch("model_client.time.sleep") as sleeper, self.assertRaisesRegex(RuntimeError, "Model HTTP 402"):
            complete(CONFIG, MESSAGES)
        self.assertEqual(opener.call_count, 1)
        sleeper.assert_not_called()

    def test_http_and_transport_errors_redact_credentials(self):
        errors = [urllib.error.HTTPError(CONFIG["base_url"], 401, "Unauthorized", {},
                                        io.BytesIO(("Authorization: Bearer " + TEST_KEY).encode())),
                  urllib.error.URLError("transport echoed " + TEST_KEY)]
        with patch.dict(os.environ, {CONFIG["api_key_env"]: TEST_KEY}):
            for failure in errors:
                with patch("model_client.urllib.request.urlopen", side_effect=failure), self.assertRaises(RuntimeError) as error:
                    complete(CONFIG, MESSAGES)
                self.assertNotIn(TEST_KEY, str(error.exception))
                self.assertIn("[redacted]", str(error.exception))

    def test_optional_parameters_fingerprint_preserves_legacy_and_rejects_changes(self):
        self.assertEqual(model_settings(CONFIG), {key: CONFIG[key] for key in MODEL_FIELDS})
        metadata = {"requested_model": "test-model", "response_id": "once"}
        with tempfile.TemporaryDirectory() as path, patch("model_trial.complete", return_value=({"ok": True}, metadata)) as mocked:
            root = Path(path)
            recorded_complete(root, "old_sf", CONFIG, MESSAGES, require_settings=True)
            recorded_complete(root, "old_sf", dict(CONFIG), MESSAGES, require_settings=True)
            self.assertEqual(mocked.call_count, 1)
            for override in ({"json_mode": False}, {"include_thinking_parameter": False},
                             {"api_format": "anthropic"}, {"omit_temperature": True},
                             {"request_parameters": {"top_p": .8}}):
                with self.subTest(override=override), self.assertRaisesRegex(ValueError, "parameters changed"):
                    recorded_complete(root, "old_sf", {**CONFIG, **override}, MESSAGES, require_settings=True)


if __name__ == "__main__":
    unittest.main()
