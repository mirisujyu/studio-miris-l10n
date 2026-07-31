#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown 내 JSON manifest 기반 범용 이미지 텍스트 주입기.

이 파일에는 특정 게임의 파일명, 번역문, 좌표, 색 또는 글꼴을 넣지 않는다.
물리 경로는 타이틀 리포의 config.py에서, 렌더링 설정은 --spec으로 지정한
Markdown의 ``image-text-manifest`` JSON 블록에서 읽는다.

실행 예:
  ujyu image --check
  ujyu image --variant FONT_VARIANT
  ujyu image --all
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ujyu.titleconfig import config as C


MANIFEST_START = "<!-- image-text-manifest:start -->"
MANIFEST_END = "<!-- image-text-manifest:end -->"


class ManifestError(ValueError):
    pass


def color(value: str | list[int] | None) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if isinstance(value, list):
        if len(value) == 3:
            return (*value, 255)
        if len(value) == 4:
            return tuple(value)
        raise ManifestError(f"색 배열은 RGB/RGBA여야 합니다: {value!r}")
    text = value.removeprefix("#")
    if len(text) == 6:
        text += "FF"
    if len(text) != 8:
        raise ManifestError(f"색은 #RRGGBB 또는 #RRGGBBAA여야 합니다: {value!r}")
    return tuple(int(text[i:i + 2], 16) for i in range(0, 8, 2))


def load_manifest(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if MANIFEST_START not in text or MANIFEST_END not in text:
        raise ManifestError(
            f"{path}에 {MANIFEST_START!r} / {MANIFEST_END!r} 블록이 없습니다.")
    block = text.split(MANIFEST_START, 1)[1].split(MANIFEST_END, 1)[0]
    fence_start = block.find("```json")
    if fence_start < 0:
        raise ManifestError("manifest 블록에 ```json 코드 펜스가 없습니다.")
    payload_start = block.find("\n", fence_start)
    fence_end = block.find("```", payload_start + 1)
    if payload_start < 0 or fence_end < 0:
        raise ManifestError("manifest JSON 코드 펜스가 닫히지 않았습니다.")
    try:
        manifest = json.loads(block[payload_start + 1:fence_end])
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest JSON 오류: {exc}") from exc
    if manifest.get("schema") != 1:
        raise ManifestError(
            f"지원하지 않는 manifest schema: {manifest.get('schema')!r}")
    if not isinstance(manifest.get("fonts"), list):
        raise ManifestError("manifest.fonts 배열이 필요합니다.")
    if not isinstance(manifest.get("operations"), list):
        raise ManifestError("manifest.operations 배열이 필요합니다.")
    return manifest


def variant_table(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in manifest["fonts"]:
        name = item.get("name")
        if not name or name in result:
            raise ManifestError(f"중복되거나 빈 글꼴 변형 이름: {name!r}")
        if not item.get("regular") or not item.get("bold"):
            raise ManifestError(f"{name}: regular/bold 글꼴 파일이 필요합니다.")
        result[name] = item
    return result


class Injector:
    def __init__(
        self,
        manifest: dict[str, Any],
        variant: str,
        original_dir: Path,
        textless_dir: Path,
        font_dir: Path,
        output_dir: Path,
    ) -> None:
        variants = variant_table(manifest)
        if variant not in variants:
            raise ManifestError(
                f"알 수 없는 글꼴 변형 {variant!r}; "
                f"가능한 값: {', '.join(variants)}")
        font_spec = variants[variant]
        self.manifest = manifest
        self.variant = variant
        self.original_dir = original_dir
        self.textless_dir = textless_dir
        self.output_dir = output_dir
        self.font_paths = {
            "Regular": font_dir / font_spec["regular"],
            "Bold": font_dir / font_spec["bold"],
        }
        self.font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self.canvases: dict[str, Image.Image] = {}
        self.bounds: dict[str, tuple[int, int, int, int]] = {}
        self.touched: set[str] = set()

    def validate(self) -> None:
        for weight, path in self.font_paths.items():
            if not path.is_file():
                raise ManifestError(
                    f"{self.variant} {weight} 글꼴 파일이 없습니다: {path}")
        ids: set[str] = set()
        available_files = {p.name for p in self.textless_dir.glob("*.png")}
        if not available_files:
            raise ManifestError(f"무문자 PNG가 없습니다: {self.textless_dir}")
        for index, operation in enumerate(self.manifest["operations"]):
            op_id = operation.get("id")
            op_type = operation.get("type")
            filename = operation.get("file")
            if not op_id or op_id in ids:
                raise ManifestError(f"operation #{index}: 중복/빈 id {op_id!r}")
            ids.add(op_id)
            if op_type not in {
                "text", "vertical_text", "copy_original",
                "line_relative", "resize_from",
            }:
                raise ManifestError(
                    f"{op_id}: 지원하지 않는 type {op_type!r}")
            if not filename:
                raise ManifestError(f"{op_id}: file이 없습니다.")
            if filename not in available_files:
                raise ManifestError(
                    f"{op_id}: textless에 파일이 없습니다: {filename}")
            if op_type == "text":
                render = operation.get("render", {}).get(self.variant)
                if render is None:
                    raise ManifestError(
                        f"{op_id}: {self.variant} render 설정이 없습니다.")
                for key in ("x", "y", "size"):
                    if key not in render:
                        raise ManifestError(
                            f"{op_id}: render.{self.variant}.{key}가 없습니다.")
            elif op_type == "line_relative":
                relative = operation.get("relative_to")
                if relative not in ids:
                    raise ManifestError(
                        f"{op_id}: 앞서 정의된 relative_to가 필요합니다: "
                        f"{relative!r}")
            elif op_type == "resize_from":
                source = operation.get("source_file")
                if source not in available_files:
                    raise ManifestError(
                        f"{op_id}: source_file이 textless에 없습니다: {source}")

    def font(self, size: int, weight: str) -> ImageFont.FreeTypeFont:
        if weight not in self.font_paths:
            raise ManifestError(
                f"지원하지 않는 weight {weight!r}; Regular/Bold만 가능합니다.")
        key = (weight, size)
        if key not in self.font_cache:
            self.font_cache[key] = ImageFont.truetype(
                str(self.font_paths[weight]), size)
        return self.font_cache[key]

    def canvas(self, filename: str) -> Image.Image:
        if filename not in self.canvases:
            path = self.textless_dir / filename
            if not path.is_file():
                raise ManifestError(f"textless 파일이 없습니다: {path}")
            self.canvases[filename] = Image.open(path).convert("RGBA")
        self.touched.add(filename)
        return self.canvases[filename]

    def text_bitmap(
        self,
        text: str,
        size: int,
        fill: tuple[int, int, int, int],
        weight: str,
        stroke: tuple[int, int, int, int] | None,
        stroke_width: int,
        tracking: float,
    ) -> Image.Image:
        face = self.font(size, weight)
        widths = [
            face.size * 0.32 if char == " " else face.getlength(char)
            for char in text
        ]
        width = sum(widths) + max(0, len(text) - 1) * tracking
        glyph_box = face.getbbox(text, stroke_width=stroke_width)
        height = glyph_box[3] - glyph_box[1]
        canvas = Image.new("RGBA", (1024, 256), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        x = (canvas.width - width) / 2
        y = (canvas.height - height) / 2 - glyph_box[1]
        if tracking == 0 and " " not in text:
            draw.text(
                (x, y),
                text,
                font=face,
                fill=fill,
                stroke_width=stroke_width,
                stroke_fill=stroke,
            )
        else:
            for char in text:
                if char == " ":
                    x += size * 0.32 + tracking
                    continue
                draw.text(
                    (x, y),
                    char,
                    font=face,
                    fill=fill,
                    stroke_width=stroke_width,
                    stroke_fill=stroke,
                )
                x += face.getlength(char) + tracking
        bbox = canvas.getbbox()
        return canvas.crop(bbox) if bbox else canvas

    @staticmethod
    def assert_inside(
        op_id: str,
        image: Image.Image,
        box: tuple[int, int, int, int],
    ) -> None:
        x0, y0, x1, y1 = box
        if not (0 <= x0 <= x1 <= image.width and
                0 <= y0 <= y1 <= image.height):
            raise ManifestError(
                f"{op_id}: 렌더링 영역 {box}이 캔버스 "
                f"{image.size} 밖으로 나갑니다.")

    def render_text(self, operation: dict[str, Any]) -> None:
        op_id = operation["id"]
        params = operation["render"][self.variant]
        fill = color(operation.get("fill", "#FFFFFFFF"))
        stroke = color(operation.get("stroke"))
        size = int(params["size"])
        tracking = float(params.get("tracking", 0))
        bitmap = self.text_bitmap(
            operation["text"],
            size,
            fill,
            operation.get("weight", "Regular"),
            stroke,
            int(operation.get("stroke_width", 0)),
            tracking,
        )
        x, y = int(params["x"]), int(params["y"])
        box = (x, y, x + bitmap.width, y + bitmap.height)
        image = self.canvas(operation["file"])
        self.assert_inside(op_id, image, box)
        image.alpha_composite(bitmap, (x, y))
        self.bounds[op_id] = box

    def render_vertical_text(self, operation: dict[str, Any]) -> None:
        fill = color(operation.get("fill", "#FFFFFFFF"))
        stroke = color(operation.get("stroke"))
        size = int(operation["size"])
        weight = operation.get("weight", "Regular")
        stroke_width = int(operation.get("stroke_width", 0))
        scales = operation.get("character_scale", {})
        bitmaps: list[Image.Image] = []
        for char in operation["text"]:
            bitmap = self.text_bitmap(
                char, size, fill, weight, stroke, stroke_width, 0)
            scale = float(scales.get(char, 1))
            if scale != 1:
                bitmap = bitmap.resize(
                    (max(1, round(bitmap.width * scale)),
                     max(1, round(bitmap.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            bitmaps.append(bitmap)
        gap = int(operation.get("gap", 0))
        width = max(bitmap.width for bitmap in bitmaps)
        height = sum(bitmap.height for bitmap in bitmaps)
        height += gap * max(0, len(bitmaps) - 1)
        vertical = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        cursor_y = 0
        for bitmap in bitmaps:
            vertical.alpha_composite(
                bitmap, ((width - bitmap.width) // 2, cursor_y))
            cursor_y += bitmap.height + gap
        x0, y0, x1, y1 = operation["box"]
        x = round((x0 + x1 - vertical.width) / 2)
        y = round((y0 + y1 - vertical.height) / 2)
        box = (x, y, x + vertical.width, y + vertical.height)
        image = self.canvas(operation["file"])
        self.assert_inside(operation["id"], image, box)
        image.alpha_composite(vertical, (x, y))
        self.bounds[operation["id"]] = box

    def copy_original(self, operation: dict[str, Any]) -> None:
        filename = operation["file"]
        original_path = self.original_dir / filename
        if not original_path.is_file():
            raise ManifestError(f"원본 파일이 없습니다: {original_path}")
        source_box = tuple(operation["source_box"])
        destination = tuple(operation.get(
            "destination", source_box[:2]))
        original = Image.open(original_path).convert("RGBA")
        crop = original.crop(source_box)
        image = self.canvas(filename)
        image.alpha_composite(crop, destination)
        self.bounds[operation["id"]] = (
            destination[0],
            destination[1],
            destination[0] + crop.width,
            destination[1] + crop.height,
        )

    def render_line_relative(self, operation: dict[str, Any]) -> None:
        relative = self.bounds[operation["relative_to"]]
        width = relative[2] - relative[0] + int(operation.get("width_add", 0))
        height = int(operation.get("height", 1))
        x = round(float(operation["center_x"]) - width / 2)
        y = int(operation["y"])
        box = (x, y, x + width, y + height)
        image = self.canvas(operation["file"])
        self.assert_inside(operation["id"], image, box)
        ImageDraw.Draw(image).rectangle(
            (x, y, x + width - 1, y + height - 1),
            fill=color(operation.get("fill", "#FFFFFFFF")),
        )
        self.bounds[operation["id"]] = box

    def resize_from(self, operation: dict[str, Any]) -> None:
        source_name = operation["source_file"]
        source = self.canvas(source_name)
        convert_mode = operation.get("convert")
        if convert_mode:
            source = source.convert(convert_mode)
        resampling = {
            "nearest": Image.Resampling.NEAREST,
            "bilinear": Image.Resampling.BILINEAR,
            "bicubic": Image.Resampling.BICUBIC,
            "lanczos": Image.Resampling.LANCZOS,
        }
        method = resampling.get(operation.get("resample", "lanczos"))
        if method is None:
            raise ManifestError(
                f"{operation['id']}: 알 수 없는 resample 값")
        resized = source.resize(tuple(operation["size"]), method).convert("RGBA")
        filename = operation["file"]
        self.canvases[filename] = resized
        self.touched.add(filename)
        self.bounds[operation["id"]] = (0, 0, resized.width, resized.height)

    def render(self) -> int:
        self.validate()
        handlers = {
            "text": self.render_text,
            "vertical_text": self.render_vertical_text,
            "copy_original": self.copy_original,
            "line_relative": self.render_line_relative,
            "resize_from": self.resize_from,
        }
        for operation in self.manifest["operations"]:
            handlers[operation["type"]](operation)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for filename in sorted(self.touched):
            image = self.canvases[filename]
            reference = self.original_dir / filename
            if not reference.is_file():
                reference = self.textless_dir / filename
            mode = Image.open(reference).mode
            output = image.convert("RGB") if mode == "RGB" else image.convert("RGBA")
            output.save(self.output_dir / filename)
        return len(self.touched)


def default_spec() -> Path:
    value = getattr(C, "IMAGE_SPEC", None)
    if not value:
        raise ManifestError(
            "config.py에 IMAGE_SPEC 경로를 지정해야 합니다.")
    return Path(value)


def selected_variant() -> str:
    value = os.environ.get(
        "MIRIS_IMAGE_VARIANT",
        getattr(C, "IMAGE_VARIANT", None),
    )
    if not value:
        raise ManifestError(
            "config.py에 IMAGE_VARIANT를 지정하거나 --variant를 사용하세요.")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ujyu image", description=__doc__)
    parser.add_argument(
        "--spec",
        type=Path,
        default=None,
        help="JSON manifest 블록을 포함한 Markdown (기본: config.IMAGE_SPEC)",
    )
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument(
        "--variant",
        help="렌더링할 글꼴 변형 (기본: config.IMAGE_VARIANT)",
    )
    choice.add_argument(
        "--all",
        action="store_true",
        help="manifest의 모든 글꼴 변형 렌더링",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="manifest·입력·글꼴만 검사하고 이미지는 쓰지 않음",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="사용 가능한 글꼴 변형 목록 출력",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spec_path = args.spec or default_spec()
    manifest = load_manifest(spec_path)
    variants = variant_table(manifest)
    if args.list:
        for name in variants:
            print(name)
        return
    names = list(variants) if args.all else [args.variant or selected_variant()]
    original_dir = Path(C.IMAGE_ORIGINAL_DIR)
    textless_dir = Path(C.IMAGE_TEXTLESS_DIR)
    font_dir = Path(C.IMAGE_FONT_DIR)
    output_prefix = str(C.IMAGE_TEXTED_PREFIX)
    for name in names:
        injector = Injector(
            manifest,
            name,
            original_dir,
            textless_dir,
            font_dir,
            Path(output_prefix + name),
        )
        if args.check:
            injector.validate()
            print(
                f"{name}: OK "
                f"({len(manifest['operations'])} operations)")
            continue
        count = injector.render()
        print(f"{name}: created {count} PNG files -> {injector.output_dir}")


if __name__ == "__main__":
    main()
