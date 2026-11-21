import unittest
from unittest.mock import Mock

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from app.config import LLMSettings
from app.llm_client import (
    LLMAuthError,
    LLMClient,
    LLMError,
    LLMRetryableError,
)


def completion(content: str):
    message = Mock()
    message.content = content
    choice = Mock()
    choice.message = message
    response = Mock()
    response.choices = [choice]
    return response


def status_error(error_cls, status: int):
    request = httpx.Request("POST", "http://test/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return error_cls("error", response=response, body=None)


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeOpenAI:
    def __init__(self, outcomes):
        self.chat = Mock()
        self.chat.completions = FakeCompletions(outcomes)


class LLMClientTests(unittest.TestCase):
    def make_client(self, outcomes, sleeps=None):
        fake = FakeOpenAI(outcomes)
        if sleeps is None:
            sleeps = []
        settings = LLMSettings(
            api_key="sk-test", model="fake-model", temperature=0.2
        )
        client = LLMClient(settings, client=fake, sleep=sleeps.append)
        return client, fake, sleeps

    def test_returns_content_and_sends_expected_arguments(self):
        client, fake, _ = self.make_client([completion("你好")])
        self.assertEqual(client.complete("sys", "user"), "你好")
        kwargs = fake.chat.completions.calls[0]
        self.assertEqual(kwargs["model"], "fake-model")
        self.assertEqual(kwargs["temperature"], 0.2)
        self.assertEqual(
            kwargs["messages"][0], {"role": "system", "content": "sys"}
        )
        self.assertEqual(
            kwargs["messages"][1], {"role": "user", "content": "user"}
        )
        self.assertNotIn("max_tokens", kwargs)

    def test_retries_rate_limit(self):
        sleeps = []
        client, fake, _ = self.make_client(
            [status_error(RateLimitError, 429), completion("OK")], sleeps
        )
        self.assertEqual(client.complete("sys", "user"), "OK")
        self.assertEqual(len(fake.chat.completions.calls), 2)
        self.assertEqual(sleeps, [1])

    def test_auth_error_is_not_retried(self):
        client, fake, _ = self.make_client(
            [status_error(AuthenticationError, 401)]
        )
        with self.assertRaises(LLMAuthError):
            client.complete("sys", "user")
        self.assertEqual(len(fake.chat.completions.calls), 1)

    def test_empty_content_is_retried(self):
        client, fake, _ = self.make_client(
            [completion("   "), completion("OK")]
        )
        self.assertEqual(client.complete("sys", "user"), "OK")
        self.assertEqual(len(fake.chat.completions.calls), 2)

    def test_persistent_rate_limit_raises_retryable(self):
        sleeps = []
        outcomes = [status_error(RateLimitError, 429) for _ in range(3)]
        client, fake, _ = self.make_client(outcomes, sleeps)
        with self.assertRaises(LLMRetryableError):
            client.complete("sys", "user")
        self.assertEqual(len(fake.chat.completions.calls), 3)
        self.assertEqual(sleeps, [1, 2])

    def test_retryable_error_classes_are_retried(self):
        request = httpx.Request("POST", "http://test/v1/chat/completions")
        cases = [
            APITimeoutError(request=request),
            APIConnectionError(request=request),
            status_error(InternalServerError, 500),
            status_error(APIStatusError, 408),
            status_error(APIStatusError, 409),
        ]
        for error in cases:
            with self.subTest(error=type(error).__name__):
                client, fake, _ = self.make_client(
                    [error, completion("OK")]
                )
                self.assertEqual(client.complete("sys", "user"), "OK")
                self.assertEqual(len(fake.chat.completions.calls), 2)

    def test_bad_request_is_not_retried(self):
        client, fake, _ = self.make_client(
            [status_error(BadRequestError, 400)]
        )
        with self.assertRaises(LLMError):
            client.complete("sys", "user")
        self.assertEqual(len(fake.chat.completions.calls), 1)

    def test_permission_denied_is_auth_error(self):
        client, fake, _ = self.make_client(
            [status_error(PermissionDeniedError, 403)]
        )
        with self.assertRaises(LLMAuthError):
            client.complete("sys", "user")
        self.assertEqual(len(fake.chat.completions.calls), 1)

    def test_other_http_status_is_not_retried(self):
        client, fake, _ = self.make_client(
            [status_error(APIStatusError, 404)]
        )
        with self.assertRaises(LLMError):
            client.complete("sys", "user")
        self.assertEqual(len(fake.chat.completions.calls), 1)

    def test_max_retries_must_be_positive(self):
        settings = LLMSettings(api_key="sk-test", model="fake-model")
        with self.assertRaises(ValueError):
            LLMClient(settings, client=FakeOpenAI([]), max_retries=0)

    def test_empty_choices_are_retried(self):
        empty = Mock()
        empty.choices = []
        client, fake, _ = self.make_client(
            [empty, completion("OK")]
        )
        self.assertEqual(client.complete("sys", "user"), "OK")
        self.assertEqual(len(fake.chat.completions.calls), 2)

    def test_test_connection_passes_max_tokens(self):
        client, fake, _ = self.make_client([completion("OK")])
        self.assertEqual(client.test_connection(), "OK")
        self.assertEqual(fake.chat.completions.calls[0]["max_tokens"], 5)


if __name__ == "__main__":
    unittest.main()
