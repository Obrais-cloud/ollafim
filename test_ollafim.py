#!/usr/bin/env python3
"""
Unit tests for ollafim that don't require Ollama running.

Run with: python3 test_ollafim.py
"""
import sys
import unittest

import ollafim as o


class TestTemplateDetection(unittest.TestCase):
    def test_qwen_coder(self):
        self.assertEqual(o.detect_template("qwen2.5-coder:1.5b-base").family, "qwen")
        self.assertEqual(o.detect_template("qwen3-coder-next:q4_K_M").family, "qwen")

    def test_deepseek(self):
        self.assertEqual(
            o.detect_template("deepseek-coder-v2:16b").family, "deepseek-coder"
        )

    def test_codestral(self):
        self.assertEqual(o.detect_template("codestral:22b").family, "codestral")

    def test_codellama(self):
        self.assertEqual(o.detect_template("codellama:7b-code").family, "codellama")

    def test_starcoder(self):
        self.assertEqual(o.detect_template("starcoder2:7b").family, "starcoder")

    def test_codegemma(self):
        self.assertEqual(o.detect_template("codegemma:2b").family, "codegemma")

    def test_unknown_falls_back_to_qwen_when_coder(self):
        # Heuristic: any *coder* model defaults to qwen tokens.
        t = o.detect_template("acme-coder:7b")
        self.assertIsNotNone(t)
        self.assertEqual(t.family, "qwen")

    def test_unknown_returns_none(self):
        self.assertIsNone(o.detect_template("llama3.3:70b"))
        self.assertIsNone(o.detect_template("gemma4:31b-q8"))


class TestPromptBuilding(unittest.TestCase):
    def test_qwen_format(self):
        tpl = o.detect_template("qwen2.5-coder:1.5b-base")
        s = o.build_fim_prompt("def fib(n):\n    ", "\n    return r", tpl)
        self.assertIn("<|fim_prefix|>def fib(n):", s)
        self.assertIn("<|fim_suffix|>", s)
        self.assertTrue(s.endswith("<|fim_middle|>"))

    def test_codestral_suffix_first(self):
        tpl = o.detect_template("codestral:22b")
        s = o.build_fim_prompt("PRE", "SUF", tpl)
        # Codestral must put suffix block before prefix block.
        self.assertEqual(s, "[SUFFIX]SUF[PREFIX]PRE")
        self.assertLess(s.index("[SUFFIX]"), s.index("[PREFIX]"))

    def test_deepseek_format(self):
        tpl = o.detect_template("deepseek-coder-v2:16b")
        s = o.build_fim_prompt("PRE", "SUF", tpl)
        self.assertIn("<｜fim▁begin｜>PRE", s)
        self.assertIn("<｜fim▁hole｜>SUF", s)
        self.assertTrue(s.endswith("<｜fim▁end｜>"))


class TestStops(unittest.TestCase):
    def test_qwen_stops_include_im_end(self):
        tpl = o.detect_template("qwen2.5-coder:1.5b-base")
        self.assertIn("<|im_end|>", tpl.stops)

    def test_starcoder_stops_endoftext(self):
        tpl = o.detect_template("starcoder2:7b")
        self.assertIn("<|endoftext|>", tpl.stops)


class TestCLI(unittest.TestCase):
    def test_help(self):
        with self.assertRaises(SystemExit) as ctx:
            o.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_version(self):
        with self.assertRaises(SystemExit) as ctx:
            o.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
