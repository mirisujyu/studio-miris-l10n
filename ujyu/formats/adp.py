#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Studio Miris ``ADPx`` adaptive DPCM audio decoder.

The format was recovered from the 32-bit x86 decoder in Kannagi no Tori:

* ``0x449620`` validates the header and selects the channel decoder.
* ``0x4496c0`` decodes mono payloads.
* ``0x449890`` decodes stereo payloads.
* ``0x4495c0`` builds the saturating predictor-state lookup table.

Container layout::

    0x00  char[4]  "ADPx"
    0x04  u24le    sample rate
    0x07  u8       channels (1 or 2)
    0x08  ...      compressed payload

Each channel consumes one little-endian 32-bit word per block and produces six
signed 16-bit PCM samples.  A stereo block is therefore 8 bytes and produces
six interleaved PCM frames.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Tuple, Union
import wave


MAGIC = b"ADPx"
HEADER_SIZE = 8
SAMPLES_PER_BLOCK = 6

# EXE 0x46fc20, indexed by the adaptive state (0..48).
STEP_TABLE = (
    16, 17, 19, 21, 23, 25, 28, 31, 34, 37,
    41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253,
    279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
    724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552,
)

# EXE 0x46fd68.  Applied after every 5-bit code.
CODE_STATE_DELTA = (
    -1, -1, -1, -1, -1, -1, -1, -1,
    1, 2, 3, 4, 5, 6, 7, 8,
    8, 7, 6, 5, 4, 3, 2, 1,
    -1, -1, -1, -1, -1, -1, -1, -1,
)

# EXE 0x46fde8.  The high two bits of every packed word adjust the state.
BLOCK_STATE_DELTA = (0, 2, -2, -4)

# The original decoder deliberately handles code 16 asymmetrically on
# alternating samples (tables at 0x46fce4 and 0x46fce8).
SIGNED_CODE_A = tuple(i if i < 16 else i - 32 for i in range(32))
SIGNED_CODE_B = tuple(i if i <= 16 else i - 32 for i in range(32))


class ADPError(ValueError):
    """Raised when an ADPx stream is malformed or unsupported."""


BytesLike = Union[bytes, bytearray, memoryview]
PathLike = Union[str, Path]


@dataclass(frozen=True)
class ADPHeader:
    sample_rate: int
    channels: int

    @property
    def compressed_block_size(self) -> int:
        return 4 * self.channels

    @property
    def pcm_frame_size(self) -> int:
        return 2 * self.channels


def parse_header(data: BytesLike) -> ADPHeader:
    """Parse and validate the eight-byte ADPx header."""
    if len(data) < HEADER_SIZE:
        raise ADPError("ADPx header is truncated (need 8 bytes)")
    if bytes(data[:4]) != MAGIC:
        raise ADPError("not an ADPx stream")

    sample_rate = int.from_bytes(data[4:7], "little")
    channels = int(data[7])
    if sample_rate <= 0:
        raise ADPError("invalid ADPx sample rate: %d" % sample_rate)
    if channels not in (1, 2):
        raise ADPError("unsupported ADPx channel count: %d" % channels)
    return ADPHeader(sample_rate=sample_rate, channels=channels)


def _clamp_state(value: int) -> int:
    if value < 0:
        return 0
    if value > 48:
        return 48
    return value


def decode_payload(payload: BytesLike, channels: int) -> bytes:
    """Decode an ADPx payload to interleaved little-endian PCM16 bytes."""
    if channels not in (1, 2):
        raise ADPError("unsupported ADPx channel count: %d" % channels)

    payload = memoryview(payload)
    block_size = 4 * channels
    if len(payload) % block_size:
        raise ADPError(
            "truncated ADPx payload: %d bytes is not divisible by block size %d"
            % (len(payload), block_size)
        )

    block_count = len(payload) // block_size
    pcm = bytearray(block_count * SAMPLES_PER_BLOCK * channels * 2)
    states = [0] * channels
    predictors = [0] * channels
    input_offset = 0
    output_block_size = SAMPLES_PER_BLOCK * channels * 2

    for block_index in range(block_count):
        output_base = block_index * output_block_size
        for channel in range(channels):
            packed = struct.unpack_from("<I", payload, input_offset)[0]
            input_offset += 4

            state = _clamp_state(
                states[channel] + BLOCK_STATE_DELTA[packed >> 30]
            )
            predictor = predictors[channel]

            for sample_index in range(SAMPLES_PER_BLOCK):
                code = (packed >> (sample_index * 5)) & 0x1F
                signed_code = (
                    SIGNED_CODE_A[code]
                    if sample_index % 2 == 0
                    else SIGNED_CODE_B[code]
                )
                predictor = (
                    predictor + ((signed_code * STEP_TABLE[state]) >> 2)
                ) & 0xFFFF

                output_offset = (
                    output_base + (sample_index * channels + channel) * 2
                )
                pcm[output_offset] = predictor & 0xFF
                pcm[output_offset + 1] = predictor >> 8
                state = _clamp_state(state + CODE_STATE_DELTA[code])

            states[channel] = state
            predictors[channel] = predictor

    return bytes(pcm)


def decode(data: BytesLike) -> Tuple[ADPHeader, bytes]:
    """Decode a complete ADPx stream and return ``(header, pcm16le)``."""
    header = parse_header(data)
    return header, decode_payload(memoryview(data)[HEADER_SIZE:], header.channels)


def decode_file(source: PathLike, output: PathLike) -> ADPHeader:
    """Decode *source* and write a PCM16 WAV file to *output*."""
    source = Path(source)
    output = Path(output)
    header, pcm = decode(source.read_bytes())
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(header.channels)
        wav.setsampwidth(2)
        wav.setframerate(header.sample_rate)
        wav.writeframes(pcm)
    return header


def stream_info(path: PathLike) -> Tuple[ADPHeader, int, int, float]:
    """Return ``(header, blocks, frames, seconds)`` without decoding samples."""
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = parse_header(stream.read(HEADER_SIZE))
    payload_size = size - HEADER_SIZE
    block_size = header.compressed_block_size
    if payload_size % block_size:
        raise ADPError(
            "truncated ADPx payload: %d bytes is not divisible by block size %d"
            % (payload_size, block_size)
        )
    blocks = payload_size // block_size
    frames = blocks * SAMPLES_PER_BLOCK
    return header, blocks, frames, frames / header.sample_rate


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ujyu adp",
        description="ADPx 오디오 정보를 확인하거나 PCM16 WAV로 디코딩",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예:\n"
            "  ujyu adp info movie/opening.adp\n"
            "  ujyu adp decode movie/opening.adp movie/opening.wav"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    info_parser = subparsers.add_parser("info", help="헤더·블록·재생 시간 표시")
    info_parser.add_argument("source", help="입력 ADPx 파일")

    decode_parser = subparsers.add_parser(
        "decode", help="ADPx를 무압축 PCM16 WAV로 디코딩"
    )
    decode_parser.add_argument("source", help="입력 ADPx 파일")
    decode_parser.add_argument(
        "output",
        nargs="?",
        help="출력 WAV 경로 (기본: 입력 파일과 같은 이름의 .wav)",
    )

    args = parser.parse_args()
    try:
        if args.command == "info":
            header, blocks, frames, seconds = stream_info(args.source)
            print(
                "rate=%d channels=%d blocks=%d frames=%d seconds=%.6f"
                % (
                    header.sample_rate,
                    header.channels,
                    blocks,
                    frames,
                    seconds,
                )
            )
            return 0

        output = Path(args.output) if args.output else Path(args.source).with_suffix(".wav")
        header = decode_file(args.source, output)
        _header, _blocks, frames, seconds = stream_info(args.source)
        print(
            "디코딩 완료: %s (%d Hz, %d ch, %d frames, %.6f s)"
            % (output, header.sample_rate, header.channels, frames, seconds)
        )
        return 0
    except (OSError, ADPError) as exc:
        raise SystemExit("ADPx 디코딩 실패: %s" % exc)


if __name__ == "__main__":
    raise SystemExit(main())
