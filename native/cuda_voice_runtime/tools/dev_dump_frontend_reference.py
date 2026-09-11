"""Dump reference output for the Chinese text frontend (development only).

The self-written runtime has to reproduce `chinese_to_phones` and the RoBERTa
feature extraction bit for bit. This script records what the packaged Python
frontend produces so the port can be diffed against it, and also writes a
`.fsvt` fixture for `fsv_voice_cli --mode replay --only roberta`.

    py -3 tools/dev_dump_frontend_reference.py --package <voice package> \\
        --output build/cuda_voice_runtime/dev/frontend-reference.npz \\
        --fixture build/cuda_voice_runtime/dev/roberta-zh.fsvt
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

ONNX_DTYPE = {
    np.dtype("float32"): 1,
    np.dtype("uint8"): 2,
    np.dtype("int8"): 3,
    np.dtype("uint16"): 4,
    np.dtype("int16"): 5,
    np.dtype("int32"): 6,
    np.dtype("int64"): 7,
    np.dtype("bool"): 9,
    np.dtype("float16"): 10,
    np.dtype("float64"): 11,
}


def write_fixture(path: Path, tensors) -> None:
    with path.open("wb") as handle:
        handle.write(b"FSVTFIX1")
        handle.write(struct.pack("<I", len(tensors)))
        for name, array in tensors.items():
            array = np.ascontiguousarray(array)
            raw = array.tobytes()
            encoded = name.encode("utf-8")
            handle.write(struct.pack("<I", len(encoded)))
            handle.write(encoded)
            handle.write(struct.pack("<ii", ONNX_DTYPE[array.dtype], array.ndim))
            for dim in array.shape:
                handle.write(struct.pack("<q", int(dim)))
            handle.write(struct.pack("<Q", len(raw)))
            handle.write(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--text", action="append", default=[])
    args = parser.parse_args()

    package = args.package.resolve()
    texts = args.text or [
        "你好世界。",
        "同桌，你好~我是拉贝尔学部的爱弥斯，和你一样。",
        "月亮代表我的心",
        "中华人民共和国成立了",
    ]

    sys.path.insert(0, str(package))
    infer = importlib.import_module("infer")
    get_phones_and_bert = infer.load_text_frontend(package / "common")
    import genie_tts.G2P.Chinese.ChineseG2P as chinese
    from tokenizers import Tokenizer

    frontend = chinese.gsv_frontend
    tokenizer = Tokenizer.from_file(
        str(package / "common" / "RoBERTa" / "roberta_tokenizer" / "tokenizer.json")
    )
    roberta = ort.InferenceSession(
        str(package / "common" / "RoBERTa" / "RoBERTa.onnx"),
        providers=["CPUExecutionProvider"],
    )
    g2pw = frontend.g2pw
    g2pw_calls = []
    original_g2pw_run = g2pw.session.run

    class RecordingG2pw:
        def run(self, output_names, inputs):
            outputs = original_g2pw_run(output_names, inputs)
            g2pw_calls.append(
                (
                    {key: np.array(value, copy=True) for key, value in inputs.items()},
                    {key: np.array(value, copy=True) for key, value in outputs.items()}
                    if isinstance(outputs, dict)
                    else np.array(outputs[0], copy=True),
                )
            )
            return outputs

    g2pw.session = RecordingG2pw()

    arrays = {}
    records = []
    for index, text in enumerate(texts):
        normalised = frontend.normalize_text(text)
        phones, word2ph = frontend.g2p(normalised)
        encoded = tokenizer.encode(normalised)
        input_ids = np.asarray([encoded.ids], dtype=np.int64)
        attention_mask = np.asarray([encoded.attention_mask], dtype=np.int64)
        repeats = np.asarray(word2ph, dtype=np.int64)
        bert = roberta.run(
            None,
            {"input_ids": input_ids, "attention_mask": attention_mask, "repeats": repeats},
        )[0].astype(np.float32)
        phones_seq, bert_via_api = get_phones_and_bert(text, language="Chinese")

        arrays["case%d.normalised" % index] = np.frombuffer(
            normalised.encode("utf-8"), dtype=np.uint8
        )
        arrays["case%d.phones" % index] = np.frombuffer(
            "\t".join(phones).encode("utf-8"), dtype=np.uint8
        )
        arrays["case%d.phones_seq" % index] = phones_seq.astype(np.int64)
        arrays["case%d.word2ph" % index] = repeats
        arrays["case%d.input_ids" % index] = input_ids
        arrays["case%d.attention_mask" % index] = attention_mask
        arrays["case%d.bert" % index] = bert
        records.append(
            {
                "index": index,
                "text": text,
                "normalised": normalised,
                "phones": phones,
                "word2ph": word2ph,
                "tokens": encoded.tokens,
                "equals_api": bool(np.array_equal(bert_via_api, bert)),
            }
        )
        print(
            "case %d: %d tokens, %d phones, bert %s"
            % (index, len(encoded.ids), len(phones), bert.shape)
        )

    arrays["manifest.json"] = np.frombuffer(
        json.dumps(records, ensure_ascii=False).encode("utf-8"), dtype=np.uint8
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.output, **arrays)
    print("wrote %s" % args.output)

    if args.fixture:
        g2pw_inputs = None
        g2pw_outputs = None
        if g2pw_calls:
            # The richest batch exercises the most graph paths.
            best_inputs, best_outputs = max(
                g2pw_calls, key=lambda call: int(call[0]["input_ids"].shape[0])
            )
            g2pw_inputs = best_inputs
            g2pw_outputs = (
                best_outputs
                if isinstance(best_outputs, dict)
                else {"g2pw.out.0": best_outputs}
            )
        tensors = {
            "roberta.in.0.input_ids": np.asarray([arr for arr in [arrays["case0.input_ids"][0]]], dtype=np.int64),
            "roberta.in.1.attention_mask": arrays["case0.attention_mask"],
            "roberta.in.2.repeats": arrays["case0.word2ph"],
            "roberta.out.0": arrays["case0.bert"],
        }
        if g2pw_inputs:
            for order, name in enumerate(
                ["input_ids", "token_type_ids", "attention_mask", "phoneme_mask",
                 "char_ids", "position_ids"]
            ):
                if name in g2pw_inputs:
                    tensors["g2pw.in.%d.%s" % (order, name)] = g2pw_inputs[name]
            for name, value in g2pw_outputs.items():
                key = name if name.startswith("g2pw.") else "g2pw.out.0"
                tensors[key] = value
        write_fixture(args.fixture, tensors)
        print("wrote %s" % args.fixture)
        if g2pw_inputs:
            print("g2pw queries: %s, output %s" % (
                g2pw_inputs["input_ids"].shape, g2pw_outputs["g2pw.out.0"].shape))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
