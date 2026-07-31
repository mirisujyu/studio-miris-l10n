#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exe 전체 패치 — 무패치 원본에서 한 번에 (SKILL 6·8·9·10절).

`config.EXE_IN`(무패치 원본) 을 읽어 최종 패치본을 `config.EXE_OUT` 으로 쓴다.
순서가 중요하다:

  1. UI 지역화 (10절)   : UpdateResourceW 로 .rsrc 교체 + 인라인 MessageBox CP949.
                          (리소스 크기가 바뀌며 헤더 SizeOf* 필드도 이 단계가 갱신)
  2. 엔진 바이트 패치    : .text/.data 고정 오프셋. .rsrc 앞이라 1단계 뒤에도 안전.
     - 6-1 리드 비트맵   : CP949 리드 0x81-0xFE 로 재생성
     - 6-2 charset       : 폰트 charset 0x80 -> 0x81 (본문·열거)
     - 6-3 SJIS idiom    : sub ...,0x81; cmp ...,0x7E (imm32)
     - 8-1 글꼴 필터     : 비례/FIXED_PITCH 검사 NOP, 제외필터→이름필터
     - 8-2 기본 글꼴     : GOTHIC/MINCHO 슬롯 -> 폰트명
     - 8-3 폴백 face
  3. 코드 케이브 (9절)  : 글꼴 저장/복원. 엔트리포인트를 케이브로. (원래 OEP 필요)

원본에서 시작하므로 몇 번을 돌려도 결과가 같다. 각 오프셋의 **찾는 법**은 SKILL.md.

  ujyu exe                      # config.EXE_IN -> config.EXE_OUT
"""
import argparse
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ujyu.titleconfig import config as C
from ujyu import patch_ui
from ujyu import patch_fontrestore as CAVE


def _lead_bitmap():
    """CP949 리드 0x81-0xFE 를 세운 32바이트 비트맵 (6-1)."""
    bm = bytearray(32)
    for i in range(0x81, 0xFF):
        bm[i >> 3] |= (0x80 >> (i & 7))
    return bytes(bm)


def _slot(name, size=16):
    nb = name.encode("cp949") if isinstance(name, str) else name
    if len(nb) >= size:
        raise SystemExit("글꼴명이 슬롯(%dB)보다 김: %r" % (size, name))
    return nb + b"\x00" * (size - len(nb))


def apply_engine(b):
    """엔진 바이트 패치를 in-place (bytearray) 로 적용. 오프셋은 config."""
    C.require("OFF_LEAD_BITMAP", "OFF_CHARSET_BODY", "OFF_CHARSET_ENUM",
              "OFF_FILTER_PATTERN", "OFF_FILTER_PUSH", "OFF_FILTER_JCC",
              "OFF_FONT_GOTHIC", "OFF_FONT_MINCHO", "OFF_FONT_FALLBACK",
              "FONT_FACE", "FONT_FALLBACK", "FILTER_PREFIX")
    b[C.OFF_LEAD_BITMAP:C.OFF_LEAD_BITMAP + 32] = _lead_bitmap()          # 6-1
    b[C.OFF_CHARSET_BODY] = 0x81                                          # 6-2
    b[C.OFF_CHARSET_ENUM] = 0x81
    for off, hx in C.SJIS_IDIOM:                                          # 6-3
        rep = bytes.fromhex(hx)
        b[off:off + len(rep)] = rep
    # 코드에 박힌 짧은 2바이트 문자 상수(괄호·기호 등)를 CP949 로 옮긴다.
    # MessageBox 인라인(patch_ui)은 내용으로 찾지만, 이런 1~2자는 같은 바이트열이
    # 여러 곳에 있어 내용 검색이 위험하다. 그래서 오프셋을 명시한다.
    for off, hx in (getattr(C, "INLINE_RECODE", None) or []):
        rep = bytes.fromhex(hx)
        b[off:off + len(rep)] = rep
    for off in C.OFF_FILTER_PITCH:                                        # 8-1
        b[off:off + 6] = b"\x90" * 6
    # 제외 패턴 push 를 우리 이름 패턴(OFF_FILTER_PATTERN)으로, jne -> je
    b[C.OFF_FILTER_PUSH:C.OFF_FILTER_PUSH + 4] = struct.pack(
        "<I", C.IMAGE_BASE + C.OFF_FILTER_PATTERN)
    b[C.OFF_FILTER_JCC] = 0x84
    pat = C.FILTER_PREFIX.encode("latin1") if isinstance(C.FILTER_PREFIX, str) else C.FILTER_PREFIX
    b[C.OFF_FILTER_PATTERN:C.OFF_FILTER_PATTERN + len(pat) + 1] = pat + b"\x00"
    b[C.OFF_FONT_GOTHIC:C.OFF_FONT_GOTHIC + 16] = _slot(C.FONT_FACE)      # 8-2
    b[C.OFF_FONT_MINCHO:C.OFF_FONT_MINCHO + 16] = _slot(C.FONT_FACE)
    b[C.OFF_FONT_FALLBACK:C.OFF_FONT_FALLBACK + 16] = _slot(C.FONT_FALLBACK)  # 8-3


def apply_cave(b):
    """글꼴 저장/복원 코드 케이브 + OEP 재지정 (9절). in-place."""
    pe = struct.unpack('<I', bytes(b[0x3c:0x40]))[0]
    oep = C.IMAGE_BASE + struct.unpack('<I', bytes(b[pe + 24 + 16:pe + 24 + 20]))[0]
    code = CAVE.build(oep)
    off = CAVE.rva2off(bytes(b), CAVE.CAVE)
    if bytes(b[off:off + len(code)]) != b"\x00" * len(code):
        raise SystemExit("코드 케이브가 비어있지 않음 — 중단")
    b[off:off + len(code)] = code
    b[pe + 24 + 16:pe + 24 + 20] = struct.pack("<I", CAVE.CAVE - C.IMAGE_BASE)
    return oep, CAVE.CAVE, len(code)


def build(src=None, dst=None):
    src = src or C.EXE_IN
    dst = dst or C.EXE_OUT
    if not os.path.exists(src):
        raise SystemExit("원본 exe 없음: %s" % src)

    print("[1] UI 지역화 (UpdateResourceW + 인라인 MessageBox)")
    n_res, n_mb = patch_ui.apply_ui(src, dst)        # 원본 -> 배포본 (리소스+인라인)
    print("    리소스 %d개, MessageBox %d개" % (n_res, n_mb))

    b = bytearray(open(dst, "rb").read())
    print("[2] 엔진 바이트 패치")
    apply_engine(b)
    print("[3] 코드 케이브")
    oep, cave, sz = apply_cave(b)
    print("    OEP 0x%08X -> 케이브 0x%08X (%dB)" % (oep, cave, sz))

    open(dst, "wb").write(bytes(b))
    print("완료: %s (%d bytes)" % (dst, len(b)))
    return dst


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu exe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="무패치 원본 exe 를 한 번에 패치 (UI 지역화 + 엔진 바이트 + 코드 케이브)",
        epilog="입출력은 config 고정: config.EXE_IN -> config.EXE_OUT\n"
               "예:\n"
               "  ujyu exe        # 원본에서 배포용 exe 재생성 (몇 번 돌려도 결과 동일)\n"
               "\n"
               "부분 패치·분석은 하위 명령으로:\n"
               "  ujyu exe ui             Windows UI 리소스/인라인 문자열\n"
               "  ujyu exe movie          무비 2배 확대 끄기\n"
               "  ujyu exe fontrestore    글꼴 저장/복원 코드 케이브\n"
               "  ujyu exe scan           config 에 넣을 오프셋 후보 스캔\n"
               "  ujyu exe disasm         x86 디스어셈블 분석\n")
    ap.parse_args()
    build()


if __name__ == "__main__":
    raise SystemExit(main())
