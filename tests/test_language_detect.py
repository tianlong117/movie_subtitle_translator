import unittest

import pysubs2

from app.language_detect import (
    build_prompt,
    build_sample,
    detect_language,
    parse_detected_language,
)
from app.subtitle_io import SubtitleDocument


def make_document(texts: list[str]) -> SubtitleDocument:
    subs = pysubs2.SSAFile()
    for text in texts:
        subs.events.append(
            pysubs2.SSAEvent(start=0, end=1000, text=text)
        )
    return SubtitleDocument(subs=subs, fmt="ass")


class SampleTests(unittest.TestCase):
    def test_strips_ass_tags_and_joins_events(self):
        document = make_document(
            [r"{\i1}Hello{\i0}\Nworld", "Bonjour"]
        )
        self.assertEqual(
            build_sample(document), "Hello\nworld\nBonjour"
        )

    def test_respects_max_events(self):
        document = make_document(["one", "two", "three"])
        self.assertEqual(
            build_sample(document, max_events=2), "one\ntwo"
        )

    def test_respects_max_chars(self):
        document = make_document(["one", "two", "three"])
        self.assertEqual(
            build_sample(document, max_chars=4), "one\n"
        )


class ParseTests(unittest.TestCase):
    def test_exact_code(self):
        self.assertEqual(parse_detected_language("ja"), "ja")

    def test_code_in_sentence(self):
        self.assertEqual(
            parse_detected_language("语言代码：zh-Hant"),
            "zh-Hant",
        )

    def test_chinese_label(self):
        self.assertEqual(parse_detected_language("日语"), "ja")

    def test_english_name(self):
        self.assertEqual(
            parse_detected_language("Japanese"), "ja"
        )

    def test_simplified_chinese_name(self):
        self.assertEqual(
            parse_detected_language("Chinese (Simplified)"),
            "zh-Hans",
        )

    def test_invalid(self):
        self.assertIsNone(parse_detected_language("???"))


class PromptTests(unittest.TestCase):
    def test_prompt_lists_codes_and_keeps_sample(self):
        system, user = build_prompt("こんにちは")
        self.assertIn("ja=日语", system)
        self.assertIn("只输出一个代码", system)
        self.assertEqual(user, "こんにちは")


class DetectTests(unittest.TestCase):
    def test_uses_client_and_parses(self):
        class FakeClient:
            def __init__(self, response: str):
                self.response = response
                self.calls: list[tuple] = []

            def complete(
                self,
                system: str,
                user: str,
                max_tokens: int | None = None,
            ) -> str:
                self.calls.append((system, user, max_tokens))
                return self.response

        client = FakeClient("ja\n")
        code = detect_language(client, "こんにちは")
        self.assertEqual(code, "ja")
        self.assertEqual(len(client.calls), 1)
        self.assertIn("こんにちは", client.calls[0][1])
        self.assertEqual(client.calls[0][2], 512)


if __name__ == "__main__":
    unittest.main()
