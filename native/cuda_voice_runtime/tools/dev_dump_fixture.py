"""Dump ORT reference tensors for the self-written runtime (development only).

Produces a single binary fixture consumed by tools/fsv_voice_cli.cpp so the
driver-only runtime can be compared stage by stage against ONNX Runtime.
This script is a build-time/development aid: it never ships to users.
"""

from __future__ import annotations

import argparse
import importlib
import json
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

SAMPLING = {"top_k", "top_p", "temperature", "repetition_penalty", "sample_noise"}


class RecordingSession:
    """Wraps an ORT session and copies every call's inputs/outputs into a sink."""

    def __init__(self, session, sink, tag):
        self._session = session
        self._sink = sink
        self._tag = tag

    def __getattr__(self, name):
        return getattr(self._session, name)

    def run(self, output_names, inputs):
        outputs = self._session.run(output_names, inputs)
        for index, (name, value) in enumerate(inputs.items()):
            if isinstance(value, np.ndarray):
                self._sink[f"{self._tag}.in.{index}.{name}"] = np.ascontiguousarray(value)
        for index, value in enumerate(outputs):
            if isinstance(value, np.ndarray):
                self._sink[f"{self._tag}.out.{index}"] = np.ascontiguousarray(value)
        return outputs


def write_fixture(path: Path, tensors: dict[str, np.ndarray]) -> None:
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
    total = sum(value.nbytes for value in tensors.values())
    print(f"wrote {path} ({len(tensors)} tensors, {total/1048576:.1f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--text", default="你好世界。")
    parser.add_argument("--prompt-text", default=None)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-steps", type=int, default=64)
    parser.add_argument("--top-k", type=int, default=15)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--repetition-penalty", type=float, default=1.35)
    args = parser.parse_args()

    package = args.package.resolve()
    sys.path.insert(0, str(package))
    infer = importlib.import_module("infer")
    model = infer.AimisiOnnx(package, ["CPUExecutionProvider"])

    sink: dict[str, np.ndarray] = {}
    noise: list[np.ndarray] = []
    rng = np.random.default_rng(args.seed)
    prompt_text = (args.prompt_text or model.reference_text).strip()

    original_sampling = model._sampling_inputs

    def sampling(top_k, top_p, temperature, repetition_penalty, local_rng):
        values = original_sampling(top_k, top_p, temperature, repetition_penalty, rng)
        noise.append(np.array(values["sample_noise"], copy=True))
        return values

    model._sampling_inputs = sampling
    model.hubert = RecordingSession(model.hubert, sink, "hubert")
    model.speaker = RecordingSession(model.speaker, sink, "speaker")
    model.t2s_encoder = RecordingSession(model.t2s_encoder, sink, "encoder")
    model.t2s_first = RecordingSession(model.t2s_first, sink, "first")
    model.t2s_stage = RecordingSession(model.t2s_stage, sink, "stage")
    model.vits = RecordingSession(model.vits, sink, "vits")

    # These two ran during construction, before the recording wrappers existed.
    silence = np.zeros((1, round(32000 * infer.PROMPT_SEMANTIC_SILENCE_SECONDS)), dtype=np.float32)
    model.hubert.run(None, {"input_values": np.concatenate((model.reference_16k, silence), axis=1)})
    model.speaker.run(None, {"waveform": model.reference_16k})
    sink["reference.16k"] = np.ascontiguousarray(model.reference_16k)
    sink["reference.32k"] = np.ascontiguousarray(model.reference_32k)

    stage_state_names = [
        item.name for item in model.t2s_stage.get_inputs() if item.name not in SAMPLING
    ]
    first_input_names = [item.name for item in model.t2s_first.get_inputs()]
    stage_input_names = [item.name for item in model.t2s_stage.get_inputs()]
    encoder_input_names = [item.name for item in model.t2s_encoder.get_inputs()]
    encoder_output_names = [item.name for item in model.t2s_encoder.get_outputs()]
    first_output_names = [item.name for item in model.t2s_first.get_outputs()]
    stage_output_names = [item.name for item in model.t2s_stage.get_outputs()]
    vits_input_names = [item.name for item in model.vits.get_inputs()]

    prompt_seq, prompt_bert = model._prompt_features(prompt_text, "zh")
    text_seq, text_bert = model._phones(args.text, "zh")
    audio = model.synthesize(
        args.text,
        "zh",
        args.max_steps,
        prompt_text=prompt_text,
        top_k=args.top_k,
        top_p=args.top_p,
        temperature=args.temperature,
        repetition_penalty=args.repetition_penalty,
        seed=args.seed,
        text_split_method="cut0",
    )

    sink["prompt.seq"] = prompt_seq
    sink["prompt.bert"] = prompt_bert
    sink["text.seq"] = text_seq
    sink["text.bert"] = text_bert
    sink["noise.steps"] = np.stack(noise) if noise else np.zeros((0, 1025), dtype=np.float32)
    sink["reference.audio"] = np.ascontiguousarray(audio.astype(np.float32))
    sink["meta.json"] = np.frombuffer(
        json.dumps(
            {
                "text": args.text,
                "prompt_text": prompt_text,
                "seed": args.seed,
                "sample_rate": 32000,
                "encoder_inputs": encoder_input_names,
                "encoder_outputs": encoder_output_names,
                "first_inputs": first_input_names,
                "first_outputs": first_output_names,
                "stage_inputs": stage_input_names,
                "stage_outputs": stage_output_names,
                "stage_state_names": stage_state_names,
                "vits_inputs": vits_input_names,
                "prompt_seq_columns": int(prompt_seq.shape[-1]),
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        dtype=np.uint8,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_fixture(args.output, sink)
    print(f"reference audio samples: {audio.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
