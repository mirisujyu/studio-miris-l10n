#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
글꼴 저장/복원 패치 — 시작 시 systemdata.dat 의 글꼴 이름을 기본값에 주입

배경
----
게임은 글꼴 창에서 고른 글꼴을 `save/systemdata.dat` 에 저장하지만,
**다시 시작할 때 그 값을 폰트 생성에 반영하지 않는다.**
(디버거 로그로 확인: 저장된 이름이 `CreateFontIndirectA` 까지 한 번도 도달하지 않음)

실제 글꼴 이름은 `0x403970`/`0x40398B` 에서 설정 변수에 **기본값**으로 등록되는
exe 내 문자열에서 온다:

    0x4756A0  MINCHO  (원본 "ＭＳ 明朝")     ← 본문이 실제로 쓰는 쪽
    0x475690  GOTHIC  (원본 "ＭＳ ゴシック")

두 문자열은 `.data`(쓰기 가능) 에 있고 각각 16바이트 슬롯이다.
그래서 **프로그램 시작 직후 세이브에서 글꼴 이름을 읽어 이 두 슬롯에 덮어쓰면**
기존 기본값 경로가 그대로 저장값을 집어들게 된다.

`systemdata.dat` 포맷은 앞부분이 단순하다:

    +0x00  4B  헤더 (0x00000021)
    +0x04  4B  0x00000001
    +0x08  ..  글꼴 이름 (널 종단)

동작
----
엔트리포인트를 코드 케이브로 돌린다. 케이브는:
  1. `save\\systemdata.dat` 를 읽기로 연다 (없으면 아무것도 안 하고 통과)
  2. 앞 64바이트를 읽는다
  3. +8 의 널 종단 문자열을 MINCHO/GOTHIC 슬롯에 최대 15자 + 널로 복사
  4. 원래 엔트리포인트로 점프

안전장치: 파일 없음 / 읽은 바이트 < 10 / 이름 첫 글자가 0x20 미만 이면 건드리지 않는다.
"""
import argparse
import struct, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ujyu.titleconfig import config as C
EXE_IN  = C.EXE_IN
EXE_OUT = C.EXE_OUT

BASE   = C.IMAGE_BASE
CAVE   = C.CAVE_VA
BUF    = C.BUF_VA
NBYTES = C.NBYTES_VA
MINCHO = C.IMAGE_BASE + C.OFF_FONT_MINCHO
GOTHIC = C.IMAGE_BASE + C.OFF_FONT_GOTHIC
IAT_CreateFileA = C.IAT_CreateFileA
IAT_ReadFile    = C.IAT_ReadFile
IAT_CloseHandle = C.IAT_CloseHandle


def rva2off(b, va):
    pe = struct.unpack('<I', b[0x3c:0x40])[0]
    nsec = struct.unpack('<H', b[pe + 6:pe + 8])[0]
    opt = struct.unpack('<H', b[pe + 20:pe + 22])[0]
    r = va - BASE
    for i in range(nsec):
        o = pe + 24 + opt + i * 40
        vs, sva, rs, ptr = struct.unpack('<IIII', b[o + 8:o + 24])
        if sva <= r < sva + max(vs, rs):
            return ptr + (r - sva)
    raise ValueError("VA 0x%X 매핑 실패" % va)


def build(oep):
    """코드 케이브 바이트열 생성. 라벨은 2패스로 해결."""
    fname = C.SAVE_REL_PATH.encode() + b"\x00"

    def emit(fname_va, done_off, copy_off):
        c = bytearray()
        c += b"\x60"                                              # pushad
        # CreateFileA(fname, GENERIC_READ, FILE_SHARE_READ, 0, OPEN_EXISTING, NORMAL, 0)
        c += b"\x6a\x00"                                          # push 0
        c += b"\x68" + struct.pack("<I", 0x80)                    # push FILE_ATTRIBUTE_NORMAL
        c += b"\x6a\x03"                                          # push OPEN_EXISTING
        c += b"\x6a\x00"                                          # push 0
        c += b"\x6a\x01"                                          # push FILE_SHARE_READ
        c += b"\x68" + struct.pack("<I", 0x80000000)              # push GENERIC_READ
        c += b"\x68" + struct.pack("<I", fname_va)                # push fname
        c += b"\xff\x15" + struct.pack("<I", IAT_CreateFileA)
        c += b"\x83\xf8\xff"                                      # cmp eax,-1
        c += b"\x0f\x84" + struct.pack("<i", done_off - (len(c) + 6))
        c += b"\x8b\xf0"                                          # mov esi,eax
        # ReadFile(h, BUF, 64, &NBYTES, 0)
        c += b"\x6a\x00"
        c += b"\x68" + struct.pack("<I", NBYTES)
        c += b"\x6a\x40"                                          # push 64
        c += b"\x68" + struct.pack("<I", BUF)
        c += b"\x56"                                              # push esi
        c += b"\xff\x15" + struct.pack("<I", IAT_ReadFile)
        c += b"\x56"
        c += b"\xff\x15" + struct.pack("<I", IAT_CloseHandle)
        # 읽은 바이트 >= 10 ?
        c += b"\x83\x3d" + struct.pack("<I", NBYTES) + b"\x0a"
        c += b"\x0f\x82" + struct.pack("<i", done_off - (len(c) + 6))
        # 이름 첫 글자가 정상인가
        c += b"\xbe" + struct.pack("<I", BUF + C.SAVE_NAME_OFF)                 # mov esi, BUF+8
        c += b"\x80\x3e\x20"                                      # cmp byte [esi],0x20
        c += b"\x0f\x82" + struct.pack("<i", done_off - (len(c) + 6))
        # MINCHO 슬롯에 복사
        c += b"\xbf" + struct.pack("<I", MINCHO)
        c += b"\xe8" + struct.pack("<i", copy_off - (len(c) + 5))
        # GOTHIC 슬롯에 복사
        c += b"\xbe" + struct.pack("<I", BUF + C.SAVE_NAME_OFF)
        c += b"\xbf" + struct.pack("<I", GOTHIC)
        c += b"\xe8" + struct.pack("<i", copy_off - (len(c) + 5))
        done_here = len(c)
        c += b"\x61"                                              # popad
        c += b"\xe9" + struct.pack("<i", (oep - (CAVE + len(c) + 5)))
        copy_here = len(c)
        # --- COPY: esi=src, edi=dst, 최대 15자 + 널
        c += b"\x51"                                              # push ecx
        c += b"\xb9\x0f\x00\x00\x00"                              # mov ecx,15
        l1 = len(c)
        c += b"\x8a\x06"                                          # mov al,[esi]
        c += b"\x88\x07"                                          # mov [edi],al
        c += b"\x46\x47"                                          # inc esi / inc edi
        c += b"\x84\xc0"                                          # test al,al
        c += b"\x74\x06"                                          # je L2
        c += b"\x49"                                              # dec ecx
        c += b"\x75" + bytes([(l1 - (len(c) + 2)) & 0xFF])        # jnz L1
        c += b"\xc6\x07\x00"                                      # mov byte [edi],0
        c += b"\x59\xc3"                                          # pop ecx / ret
        return bytes(c), done_here, copy_here, len(c)

    # 1패스: 크기 파악 → 2패스: 정확한 오프셋으로 재생성
    code, d, cp, size = emit(0, 0, 0)
    for _ in range(3):
        fname_va = CAVE + size
        code, d, cp, size = emit(fname_va, d, cp)
    return code + fname


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu exe fontrestore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="글꼴 저장/복원 코드 케이브만 단독 적용 (엔트리포인트를 케이브로)",
        epilog="입출력은 config 고정: config.EXE_IN -> config.EXE_OUT\n"
               "보통은 `ujyu exe` 가 이 단계까지 한 번에 처리한다. 케이브만 시험할 때 쓴다.\n"
               "예:\n"
               "  ujyu exe fontrestore\n")
    ap.parse_args()

    b = bytearray(open(EXE_IN, 'rb').read())
    pe = struct.unpack('<I', bytes(b[0x3c:0x40]))[0]
    oep = BASE + struct.unpack('<I', bytes(b[pe + 24 + 16:pe + 24 + 20]))[0]
    print("원래 OEP = 0x%08X" % oep)

    code = build(oep)
    off = rva2off(bytes(b), CAVE)
    if bytes(b[off:off + len(code)]) != b"\x00" * len(code):
        raise SystemExit("코드 케이브가 비어있지 않음 — 중단")
    b[off:off + len(code)] = code
    print("스텁 %d바이트 @ VA 0x%08X (file 0x%X)" % (len(code), CAVE, off))

    # 엔트리포인트를 케이브로
    b[pe + 24 + 16:pe + 24 + 20] = struct.pack("<I", CAVE - BASE)
    open(EXE_OUT, 'wb').write(bytes(b))
    print("새 OEP  = 0x%08X" % CAVE)
    print("저장: %s" % EXE_OUT)


if __name__ == "__main__":
    main()
