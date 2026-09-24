import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

from eventos.services.nvidia import parse_nvidia_content, post_nvidia


class NvidiaResponseTests(unittest.TestCase):
    def test_parses_structured_object(self):
        records = parse_nvidia_content('{"events": [{"title": "Concierto"}]}')
        self.assertEqual(records, [{"title": "Concierto"}])

    def test_keeps_compatibility_with_legacy_list(self):
        records = parse_nvidia_content('[{"title": "Teatro"}]')
        self.assertEqual(records, [{"title": "Teatro"}])

    def test_accepts_markdown_fence_if_provider_adds_one(self):
        records = parse_nvidia_content('```json\n{"events": []}\n```')
        self.assertEqual(records, [])

    def test_extracts_json_after_a_provider_preamble(self):
        records = parse_nvidia_content('Resultado:\n{"events": [{"title": "Danza"}]}')
        self.assertEqual(records, [{"title": "Danza"}])

    def test_rejects_an_object_without_events(self):
        with self.assertRaises(ValueError):
            parse_nvidia_content('{"resultado": []}')

    def test_rejects_truncated_json(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_nvidia_content('{"events": [')

    def test_timeout_is_retried_without_exceeding_policy(self):
        limiter = Mock()
        response = Mock(status_code=200)
        env = {
            "NVIDIA_MAX_RETRIES": "2",
            "NVIDIA_RETRY_MAX_ELAPSED_SECONDS": "0",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "eventos.services.nvidia.nvidia_rate_limiter", return_value=limiter
        ), patch(
            "eventos.services.nvidia.requests.post",
            side_effect=[requests.Timeout("lento"), response],
        ) as request:
            result = post_nvidia({"messages": []}, "test")
        self.assertIs(result, response)
        self.assertEqual(request.call_count, 2)
        limiter.defer.assert_not_called()

    def test_operation_overrides_bound_timeout_and_retries(self):
        limiter = Mock()
        with patch(
            "eventos.services.nvidia.nvidia_rate_limiter", return_value=limiter
        ), patch(
            "eventos.services.nvidia.requests.post",
            side_effect=requests.Timeout("lento"),
        ) as request:
            with self.assertRaises(requests.Timeout):
                post_nvidia(
                    {"messages": []},
                    "test",
                    retries=1,
                    timeout=2,
                    max_wait=1,
                    max_elapsed=10,
                    operation="OCR NVIDIA",
                )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs["timeout"], 2)


if __name__ == "__main__":
    unittest.main()
