import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pysubs2

from app.llm_client import LLMError
from app.subtitle_io import SubtitleDocument
from app.translator import (
    TranslationCancelled,
    TranslationError,
    TranslationJob,
    parse_response,
    split_batches,
)


def make_document(texts):
    subs = pysubs2.SSAFile()
    for index, text in enumerate(texts):
        subs.events.append(
            pysubs2.SSAEvent(
                start=index * 1000,
                end=index * 1000 + 900,
                text=text,
            )
        )
    return SubtitleDocument(subs=subs, fmt="srt")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system_prompt, user_prompt, max_tokens=None):
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class EchoClient:
    def __init__(self):
        self.calls = []

    def complete(self, system_prompt, user_prompt, max_tokens=None):
        self.calls.append(user_prompt)
        result = []
        for line in user_prompt.splitlines():
            match = re.match(r"^(\d+)\|(.*)$", line.strip())
            if match:
                result.append(f"{match.group(1)}|译{match.group(2)}")
        return "\n".join(result)


class BlockingClient:
    def __init__(self, response="1|你好"):
        self.response = response
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, system_prompt, user_prompt, max_tokens=None):
        self.started.set()
        self.release.wait(timeout=5)
        return self.response


class ConcurrentClient:
    def __init__(self, delay=0.05):
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def complete(self, system_prompt, user_prompt, max_tokens=None):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        result = []
        for line in user_prompt.splitlines():
            match = re.match(r"^(\d+)\|(.*)$", line.strip())
            if match:
                result.append(f"{match.group(1)}|译{match.group(2)}")
        return "\n".join(result)


class SplitBatchTests(unittest.TestCase):
    def test_split_by_count(self):
        items = [(index, f"line {index}") for index in range(1, 6)]
        batches = split_batches(items, batch_size=2, max_chars=1000)
        self.assertEqual([len(batch) for batch in batches], [2, 2, 1])

    def test_split_by_chars(self):
        items = [(1, "a" * 5), (2, "b" * 5), (3, "c" * 5)]
        batches = split_batches(items, batch_size=10, max_chars=12)
        self.assertEqual(
            [[item_id for item_id, _ in batch] for batch in batches],
            [[1, 2], [3]],
        )

    def test_oversized_item_gets_own_batch(self):
        items = [(1, "a" * 20), (2, "b")]
        batches = split_batches(items, batch_size=10, max_chars=10)
        self.assertEqual(
            [[item_id for item_id, _ in batch] for batch in batches],
            [[1], [2]],
        )


class ParseResponseTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(
            parse_response("1|你好\n2|世界", [1, 2]),
            {1: "你好", 2: "世界"},
        )

    def test_full_width_separator(self):
        self.assertEqual(
            parse_response("1｜你好\n2｜世界", [1, 2]),
            {1: "你好", 2: "世界"},
        )

    def test_missing_id_rejected(self):
        self.assertIsNone(parse_response("1|你好", [1, 2]))

    def test_extra_id_rejected(self):
        self.assertIsNone(
            parse_response("1|你好\n2|世界\n3|多余", [1, 2])
        )

    def test_duplicate_id_rejected(self):
        self.assertIsNone(parse_response("1|你好\n1|世界", [1]))

    def test_code_fence_stripped(self):
        text = "```text\n1|你好\n2|世界\n```"
        self.assertEqual(parse_response(text, [1, 2]), {1: "你好", 2: "世界"})

    def test_empty_translation_rejected(self):
        self.assertIsNone(parse_response("1|你好\n2|", [1, 2]))

    def test_empty_translation_does_not_hide_extra_id(self):
        self.assertIsNone(parse_response("1|你好\n2|", [1]))

    def test_empty_translation_does_not_hide_duplicate_id(self):
        self.assertIsNone(parse_response("1|你好\n1|", [1]))

    def test_unmatched_nonempty_line_rejected(self):
        self.assertIsNone(parse_response("1|你好\n续行\n2|世界", [1, 2]))


class TranslationJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.partial = Path(self.temp_dir.name) / "partial.srt"

    def run_job(
        self,
        document,
        client,
        batch_size=10,
        concurrency=2,
        cancel_event=None,
        progress_cb=None,
    ):
        job = TranslationJob(
            document=document,
            client=client,
            batch_size=batch_size,
            concurrency=concurrency,
            source_lang="en",
            target_lang="zh-Hans",
            movie_name="Test Movie",
            partial_path=self.partial,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
        )
        return job.run()

    def test_translates_all_events(self):
        document = make_document(["Hello", "World"])
        client = FakeClient(["1|你好\n2|世界"])
        report = self.run_job(document, client)
        self.assertEqual(
            [event.text for event in document.subs.events],
            ["你好", "世界"],
        )
        self.assertEqual(report.total_events, 2)
        self.assertEqual(report.unique_texts, 2)
        self.assertEqual(report.batches, 1)
        self.assertEqual(len(client.calls), 1)
        system, user = client.calls[0]
        self.assertIn("英语", system)
        self.assertIn("简体中文", system)
        self.assertIn("⟦0⟧", system)
        self.assertIn("Test Movie", user)

    def test_duplicate_lines_are_sent_once(self):
        document = make_document(["Hello", "Hello"])
        client = FakeClient(["1|你好"])
        report = self.run_job(document, client)
        self.assertEqual(
            [event.text for event in document.subs.events],
            ["你好", "你好"],
        )
        self.assertEqual(report.total_events, 2)
        self.assertEqual(report.unique_texts, 1)
        self.assertEqual(len(client.calls), 1)

    def test_ass_tags_are_restored(self):
        document = make_document([r"{\an8}Hello\Nworld"])
        client = FakeClient(["1|⟦0⟧你好⟦1⟧世界"])
        self.run_job(document, client)
        self.assertEqual(
            document.subs.events[0].text,
            r"{\an8}你好\N世界",
        )

    def test_batch_retry_on_missing_id(self):
        document = make_document(["Hello", "World"])
        client = FakeClient(["1|你好", "1|你好\n2|世界"])
        self.run_job(document, client)
        self.assertEqual(
            [event.text for event in document.subs.events],
            ["你好", "世界"],
        )
        self.assertEqual(len(client.calls), 2)
        self.assertNotIn("上一次输出", client.calls[0][0])
        self.assertIn("上一次输出", client.calls[1][0])

    def test_placeholder_failure_falls_back_per_line(self):
        document = make_document([r"{\an8}Hello\Nworld"])
        client = FakeClient(["1|你好", "1|你好", "1|你好", "你好世界"])
        self.run_job(document, client)
        self.assertEqual(
            document.subs.events[0].text,
            r"{\an8}你好世界",
        )
        self.assertEqual(len(client.calls), 4)

    def test_duplicate_text_with_different_tags_is_not_merged(self):
        document = make_document([r"{\an8}Hello", r"{\i1}Hello"])
        client = FakeClient(["1|⟦0⟧你好\n2|⟦0⟧你好啊"])
        report = self.run_job(document, client)
        self.assertEqual(report.unique_texts, 2)
        self.assertEqual(document.subs.events[0].text, r"{\an8}你好")
        self.assertEqual(document.subs.events[1].text, r"{\i1}你好啊")

    def test_cancel_before_start_saves_partial(self):
        document = make_document(["Hello"])
        cancel = threading.Event()
        cancel.set()
        client = FakeClient([])
        with self.assertRaises(TranslationCancelled) as ctx:
            self.run_job(document, client, cancel_event=cancel)
        self.assertTrue(self.partial.exists())
        self.assertEqual(ctx.exception.partial_path, self.partial)
        self.assertEqual(len(client.calls), 0)

    def test_client_error_saves_partial(self):
        document = make_document(["Hello"])
        client = FakeClient([LLMError("boom")])
        with self.assertRaises(TranslationError) as ctx:
            self.run_job(document, client)
        self.assertTrue(self.partial.exists())
        self.assertEqual(ctx.exception.partial_path, self.partial)

    def test_worker_translation_error_gets_partial_path(self):
        document = make_document(["Hello"])
        client = FakeClient([TranslationError("worker boom")])
        with self.assertRaises(TranslationError) as ctx:
            self.run_job(document, client)
        self.assertTrue(self.partial.exists())
        self.assertEqual(ctx.exception.partial_path, self.partial)

    def test_empty_document_raises(self):
        document = make_document([])
        client = FakeClient([])
        with self.assertRaises(TranslationError):
            self.run_job(document, client)
        self.assertEqual(len(client.calls), 0)

    def test_concurrent_batches_all_applied(self):
        document = make_document(["One", "Two", "Three", "Four"])
        client = EchoClient()
        progress = []
        report = self.run_job(
            document,
            client,
            batch_size=1,
            concurrency=2,
            progress_cb=lambda done, total, message: progress.append(
                (done, total)
            ),
        )
        self.assertEqual(report.total_events, 4)
        self.assertEqual(
            [event.text for event in document.subs.events],
            ["译One", "译Two", "译Three", "译Four"],
        )
        self.assertEqual(progress[-1], (4, 4))
        self.assertEqual(len(client.calls), 4)

    def test_cancel_during_final_wave(self):
        document = make_document(["Hello"])
        client = BlockingClient()
        cancel = threading.Event()
        outcome = {}

        def run():
            try:
                outcome["report"] = self.run_job(
                    document, client, cancel_event=cancel
                )
            except Exception as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(client.started.wait(5))
        cancel.set()
        client.release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(outcome.get("error"), TranslationCancelled)
        self.assertTrue(self.partial.exists())
        self.assertIn("你好", self.partial.read_text(encoding="utf-8"))

    def test_partial_save_failure_not_advertised(self):
        document = make_document(["Hello"])
        client = FakeClient([LLMError("boom")])
        with patch(
            "app.translator.save_subtitle",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(TranslationError) as ctx:
                self.run_job(document, client)
        self.assertIsNone(ctx.exception.partial_path)
        self.assertFalse(self.partial.exists())

    def test_concurrency_overlaps(self):
        document = make_document(["One", "Two", "Three", "Four"])
        client = ConcurrentClient(delay=0.05)
        self.run_job(
            document, client, batch_size=1, concurrency=2
        )
        self.assertEqual(client.max_active, 2)
        self.assertEqual(
            [event.text for event in document.subs.events],
            ["译One", "译Two", "译Three", "译Four"],
        )

    def test_parse_exhaustion_falls_back_per_line(self):
        document = make_document(["Hello", "World"])
        client = FakeClient(
            ["1|你好", "1|你好", "1|你好", "你好", "世界"]
        )
        self.run_job(document, client)
        self.assertEqual(
            [event.text for event in document.subs.events],
            ["你好", "世界"],
        )
        self.assertEqual(len(client.calls), 5)


if __name__ == "__main__":
    unittest.main()
