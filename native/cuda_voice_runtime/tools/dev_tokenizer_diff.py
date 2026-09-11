"""Diff the self-written WordPiece tokenizer against the reference.

The reference is the `tokenizers` Rust wheel the voice package's
tokenizer.json was written for, so this is the same comparison the runtime
ultimately has to win.

    py -3 tools/dev_tokenizer_diff.py [--extra-case "..."]
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(ROOT))
TOKENIZER = os.path.join(
    r"C:\AemeathDeskPet\voice\ONNX_aimisiV2",
    "common",
    "RoBERTa",
    "roberta_tokenizer",
    "tokenizer.json",
)
CLI = os.path.join(REPO, "build", "cuda_voice_runtime", "Release", "fsv_frontend_cli.exe")

ZH_POOL = "你好世界，这是测试。请问我们要去哪里？拉贝尔学部爱弥斯同桌晚上好呀真棒"
MIXED_POOL = "D盘 3.5G nice 2024年12月31日 100% A股 AI模型 GPT-4o v2.0"
PUNCT_POOL = "!?…,.-;:'\"()[]{}<>+-=*/\\|@#$%^&_~`。，、；：！？（）【】「」《》“”‘’—～·"
EMOJI_POOL = "😎👍😂🔥🎉❤️"
LATIN_POOL = "Hello World café naïve ÅNGSTRÖM Ελληνικά Привет"


def fixed_cases():
    cases = [
        "",
        " ",
        "你好世界。",
        "同桌，你好~我是拉贝尔学部的爱弥斯，和你一样。",
        "Hello World",
        "a\tb\nc\rd",
        "a\x01b\x7fc\x9fd",
        "a\x00b",
        "a\ufffdb",
        "a\u00a0b",
        "a\uff21b",
        "㐀𠀀",
        "café naïve ÀÉÎÕÜ",
        "İIı",
        "ΑΒΓ αβγ",
        "a-b_c,d!?;:'\"()[]{}",
        "a😎b👍",
        "D盘 3.5G nice",
        "  a   b  ",
        "你好世界。",
        "a，b、c«d–e¡f",
        "x+y=z$w%v&u*i/j<k>l",
        "a。b！c？d…e",
        "ＡＢＣ",
        "３．５",
        "ﬁﬂ",
        "ǅǄǆ",
        "①⑴",
        "Ⅷⅷ",
        "㍿",
        "ｱｲｳ",
        "ㄱㄴ",
        "한국어",
        "カタカナ",
        "ひらがな",
        "n̂ e\u0301",
        "月亮代表我的心",
        "中华人民共和国成立了",
        "1234567890",
        "😀😃😄",
        "Ｈｅｌｌｏ　Ｗｏｒｌｄ",
        "测试…省略号",
        "“引号”和‘单引号’",
        "50%的把握",
        "π≈3.14159",
        "×÷±",
    ]
    return cases


def random_cases(count: int, seed: int = 20260910):
    rng = random.Random(seed)
    pools = [ZH_POOL, MIXED_POOL, PUNCT_POOL, EMOJI_POOL, LATIN_POOL]
    cases = []
    for _ in range(count):
        length = rng.randint(1, 40)
        pieces = []
        for _ in range(length):
            pool = rng.choice(pools)
            pieces.append(rng.choice(pool))
        cases.append("".join(pieces))
    return cases


def run_cli(cases, tmp_dir):
    corpus = os.path.join(tmp_dir, "tokenizer_cases.txt")
    with open(corpus, "w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(case.replace("\n", " ").replace("\r", " ") + "\n")
    completed = subprocess.run(
        [CLI, "--tokenizer", TOKENIZER, "tokenize", corpus],
        capture_output=True,
        check=True,
    )
    text = completed.stdout.decode("utf-8", errors="replace")
    lines = text.split("\n")
    results = []
    index = 0
    while index + 2 < len(lines) + 0 and lines[index].startswith("N:"):
        normalised = lines[index][2:]
        tokens = lines[index + 1][2:].split(" ") if lines[index + 1][2:] else []
        ids = [int(item) for item in lines[index + 2][2:].split(",") if item]
        results.append((normalised, tokens, ids))
        index += 3
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--random-count", type=int, default=400)
    parser.add_argument("--extra-case", action="append", default=[])
    args = parser.parse_args()

    from tokenizers import Tokenizer

    reference = Tokenizer.from_file(TOKENIZER)
    cases = fixed_cases() + args.extra_case + random_cases(args.random_count)
    # The CLI reads one case per line, so newlines cannot be represented.
    cases = [case for case in cases if "\n" not in case and "\r" not in case]

    tmp_dir = os.path.join(ROOT, "..", "..", "build", "cuda_voice_runtime", "dev")
    tmp_dir = os.path.abspath(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    ours = run_cli(cases, tmp_dir)
    if len(ours) != len(cases):
        print("CLI returned %d results for %d cases" % (len(ours), len(cases)))
        return 2

    failures = 0
    for case, (normalised, tokens, ids) in zip(cases, ours):
        expected_normalised = reference.normalizer.normalize_str(case)
        expected = reference.encode(case)
        expected_tokens = reference.encode(case, add_special_tokens=False).tokens
        if normalised != expected_normalised:
            failures += 1
            print("NORMALIZE %r\n  ours=%r\n  ref =%r" % (case, normalised, expected_normalised))
            continue
        if tokens != expected_tokens or ids != expected.ids:
            failures += 1
            print("TOKENS %r\n  ours=%r\n  ref =%r" % (case, tokens, expected_tokens))
            print("  ids ours=%r\n  ids ref =%r" % (ids, expected.ids))
            if failures > 20:
                break
    print("cases=%d failures=%d" % (len(cases), failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
