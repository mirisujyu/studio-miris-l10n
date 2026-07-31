#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exe 오프셋 후보 스캐너 (SKILL 6~9절의 "찾는 법"을 자동화).

`config.py` 에 채워야 하는 `OFF_*` / `CAVE_VA` / `BUF_VA` 값을 **후보로 뽑아** 보여
준다. 값을 config 에 쓰지는 않는다 - 사람이 확인하고 붙여넣는다(`--config`).

무엇을 어떤 근거로 찾는가

  6-1 OFF_LEAD_BITMAP    32B 비트맵 중 세워진 비트가 SJIS 리드 범위와 일치하는 곳.
                         + 코드가 그 VA 를 참조하면 확실.
  6-2 OFF_CHARSET_*      `C6 44 24 ?? 80` (mov byte [esp+X],0x80). 열거용은
                         EnumFontFamiliesExA 호출 근처에 있는 쪽으로 가른다.
  6-3 SJIS_IDIOM         xor r,0x20 / sub r,0xA1 / cmp r,0x3C 연속 3명령.
  8-2 OFF_FONT_*         cp932 글꼴 이름(ゴシック·明朝 등) 문자열이 있는 16B 슬롯.
                         코드에서 push 되고 16B 간격으로 붙은 쌍이 기본 글꼴.
  15  OFF_SCREEN_W/H     화면 폭/높이 dword 즉치 (`push imm32`·`mov reg,imm32` 만).
                         W 와 H 가 가까이 붙은 쌍을 확실로 올린다.
  9   CAVE_VA            실행 섹션 끝의 연속 0 패딩 (기본 200B 이상).
      BUF/NBYTES_VA      쓰기 가능 섹션의 연속 0 구간.
      IAT_*              가져오기 테이블에서 그대로 읽는다(확실).

찾지 못하는 것 (수동): `OFF_FILTER_*`(8-1 글꼴 목록 필터), `OFF_MOVIE_SCALE`.
디스어셈블로 확인할 때는 `ujyu exe disasm <exe> at <VA>` 를 쓴다.

  ujyu exe scan <exe>              사람이 읽는 후보 표
  ujyu exe scan <exe> --config     config.py 에 붙여넣을 스니펫만
"""
import argparse
import re
import struct

import pefile

IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_WRITE = 0x80000000

SURE = "확실"
GUESS = "추정"

REGS = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")

# 6-1 비트맵이 알려진 리드 범위와 정확히 일치하면 확실로 본다.
KNOWN_LEAD_RANGES = {
    ((0x81, 0x9F), (0xE0, 0xEF)): "SJIS 리드",
    ((0x81, 0x9F), (0xE0, 0xFC)): "SJIS 리드(전범위)",
    ((0x81, 0xFE),): "CP949 리드 - 이미 패치된 exe로 보인다",
}

# 8-2 글꼴 이름 조각 (cp932 바이트, 화면 표시용 이름)
FONT_KEYS = [
    ("ゴシック", "gothic"),
    ("明朝", "mincho"),
    ("メイリオ", "gothic"),
    ("Gothic", "gothic"),
    ("Mincho", "mincho"),
]


def _safe(s):
    """CP949 콘솔에 그대로 못 쓰는 문자를 ? 로 바꾼다 (출력 중 죽지 않게)."""
    if isinstance(s, bytes):
        s = s.decode("latin1")
    return s.encode("cp949", "replace").decode("cp949")


def _align(v, n):
    return (v + n - 1) & ~(n - 1)


def _ranges(values):
    """정수 집합 -> [(lo,hi), ...] 연속 구간."""
    out = []
    for v in sorted(values):
        if out and v == out[-1][1] + 1:
            out[-1][1] = v
        else:
            out.append([v, v])
    return [tuple(r) for r in out]


def _fmt_ranges(rs):
    return ", ".join("0x%02X-0x%02X" % (a, b) if a != b else "0x%02X" % a for a, b in rs)


class Img:
    """PE 를 파일오프셋 기준으로 훑기 위한 최소 래퍼."""

    def __init__(self, path):
        self.path = path
        self.raw = open(path, "rb").read()
        self.pe = pefile.PE(path, fast_load=True)
        self.pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.salign = self.pe.OPTIONAL_HEADER.SectionAlignment or 0x1000
        self.secs = []
        for s in self.pe.sections:
            if not s.SizeOfRawData:
                continue
            self.secs.append({
                "name": s.Name.rstrip(b"\0").decode("latin1", "replace"),
                "rva": s.VirtualAddress,
                "vsz": s.Misc_VirtualSize,
                "ptr": s.PointerToRawData,
                "rsz": min(s.SizeOfRawData, max(0, len(self.raw) - s.PointerToRawData)),
                "exec": bool(s.Characteristics & IMAGE_SCN_MEM_EXECUTE),
                "write": bool(s.Characteristics & IMAGE_SCN_MEM_WRITE),
            })

    def data(self, sec):
        return self.raw[sec["ptr"]:sec["ptr"] + sec["rsz"]]

    def sec_of_off(self, off):
        for s in self.secs:
            if s["ptr"] <= off < s["ptr"] + s["rsz"]:
                return s
        return None

    def va(self, off):
        s = self.sec_of_off(off)
        if s is None:
            return None
        return self.base + s["rva"] + (off - s["ptr"])

    def mapped_end_rva(self, sec):
        """로더가 매핑하는 끝 RVA (raw 와 VirtualSize 반올림 중 작은 쪽)."""
        return sec["rva"] + min(sec["rsz"], _align(sec["vsz"], self.salign))

    def code_secs(self):
        return [s for s in self.secs if s["exec"]]

    def find_dword_refs(self, value):
        """실행 섹션에서 그 dword 가 그대로 박힌 파일오프셋들 (참조 추정)."""
        pat = struct.pack("<I", value)
        out = []
        for s in self.code_secs():
            d = self.data(s)
            i = d.find(pat)
            while i >= 0:
                out.append(s["ptr"] + i)
                i = d.find(pat, i + 1)
        return out

    def iat(self, names):
        """{함수명: IAT VA} - 가져오기 테이블에서 직접."""
        got = {}
        for d in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
            for imp in d.imports:
                if imp.name:
                    n = imp.name.decode("latin1")
                    if n in names:
                        got[n] = imp.address
        return got


class Cand:
    """후보 하나 = config 키 + 값 + 신뢰도 + 근거 한 줄."""

    def __init__(self, key, off, conf, why, is_va=False):
        self.key, self.off, self.conf, self.why, self.is_va = key, off, conf, why, is_va

    def text(self):
        return "0x%08X" % self.off if self.is_va else "0x%X" % self.off


# ─────────────────────────────────────────── 6-1 리드바이트 비트맵
def scan_lead_bitmap(img, verbose=False):
    """32B 비트맵 후보. 리드 판정은 0x80 미만 비트가 하나도 없다는 점을 이용한다."""
    out = []
    for s in img.secs:
        d = img.data(s)
        # 앞 16바이트(0x00-0x7F)가 전부 0 이고 그 다음이 0 이 아닌 자리만 본다.
        for m in re.finditer(rb"\x00{16,}(?=[^\x00])", d):
            i = m.end() - 16
            win = d[i:i + 32]
            if len(win) < 32:
                continue
            bits = {b * 8 + k for b in range(32) for k in range(8)
                    if win[b] & (0x80 >> k)}
            if not bits or min(bits) < 0x81:
                continue
            rs = _ranges(bits)
            desc = KNOWN_LEAD_RANGES.get(tuple(rs))
            off = s["ptr"] + i
            refs = img.find_dword_refs(img.va(off))
            if desc:
                conf = SURE if refs else GUESS
                why = "%s (%s) / 코드 참조 %d곳" % (_fmt_ranges(rs), desc, len(refs))
            else:
                # 알려진 범위와 다르면: 코드가 쓰는 32B 표 모양일 때만 후보로 둔다.
                tight = (len(bits) >= 24 and len(rs) <= 3
                         and max(bits) <= 0xFE and refs)
                loose = len(bits) >= 16 and refs                # -v 에서만
                if not (tight or (verbose and loose)):
                    continue
                conf = GUESS
                why = "%s / 비트 %d개 / 코드 참조 %d곳" % (_fmt_ranges(rs), len(bits), len(refs))
            out.append(Cand("OFF_LEAD_BITMAP", off, conf, "%s %s" % (s["name"], why)))
    out.sort(key=lambda c: (c.conf != SURE, c.off))
    return out


# ─────────────────────────────────────────── 6-2 폰트 charset
def scan_charset(img):
    """`C6 44 24 ?? 80` = mov byte [esp+X],0x80. 값(0x80) 바이트 오프셋을 준다."""
    hits = []
    for s in img.code_secs():
        d = img.data(s)
        for m in re.finditer(rb"\xc6\x44\x24.\x80", d):
            hits.append(s["ptr"] + m.start())
    enum_calls = []
    iat = img.iat({"EnumFontFamiliesExA", "EnumFontFamiliesA", "EnumFontFamiliesExW"})
    for va in iat.values():
        for s in img.code_secs():
            d = img.data(s)
            pat = b"\xff\x15" + struct.pack("<I", va)
            i = d.find(pat)
            while i >= 0:
                enum_calls.append(s["ptr"] + i)
                i = d.find(pat, i + 1)
    out = []
    for h in hits:
        near = min([abs(h - c) for c in enum_calls], default=None)
        val = h + 4                                  # 0x80 즉치 바이트
        if near is not None and near <= 0x800:
            out.append(Cand("OFF_CHARSET_ENUM", val, GUESS if len(hits) > 2 else SURE,
                            "mov byte [esp+0x%02X],0x80 / EnumFontFamilies 호출 %dB 이내"
                            % (img.raw[h + 3], near)))
        else:
            out.append(Cand("OFF_CHARSET_BODY", val, GUESS if len(hits) > 2 else SURE,
                            "mov byte [esp+0x%02X],0x80 / 열거 호출과 멂" % img.raw[h + 3]))
    if len(hits) != 2:
        for c in out:
            c.conf = GUESS
            c.why += " (총 %d곳 - 본문/열거 구분 확인 필요)" % len(hits)
    return out


# ─────────────────────────────────────────── 6-3 SJIS 리드 판정 idiom
_SUB_A1 = rb"(?:\x81[\xe8-\xef]|\x2d)\xa1\x00\x00\x00"
_IDIOM = re.compile(rb"\x83([\xf0-\xf7])\x20" + _SUB_A1 + rb"\x83([\xf8-\xff])\x3c")


def scan_sjis_idiom(img):
    """xor r,0x20 / sub r,0xA1 / cmp r,0x3C -> CP949(0x81-0xFE) 인정으로 교체."""
    out = []
    for s in img.code_secs():
        d = img.data(s)
        for m in re.finditer(_IDIOM, d):
            off = s["ptr"] + m.start()
            old = m.group()
            reg = old[1] & 7                            # xor 의 대상 레지스터
            osub = old[3:-3]                            # 원본 sub (인코딩 그대로 쓴다)
            sub = (b"\x2d" if osub[0] == 0x2D else osub[:2]) + struct.pack("<I", 0x81)
            # xor 는 NOP, sub 즉치는 0x81(imm32), cmp 즉치는 0x7E -> 0x81-0xFE 인정
            new = b"\x90" * 3 + sub + b"\x83" + bytes([0xF8 | reg]) + b"\x7e"
            if len(new) != len(old):                    # 길이 안 맞으면 버린다
                continue
            r = REGS[reg]
            out.append(Cand("SJIS_IDIOM", off, SURE,
                            "xor %s,0x20; sub %s,0xA1; cmp %s,0x3C -> %s (0x81-0xFE 인정)"
                            % (r, r, r, new.hex())))
            out[-1].hexrep = new.hex()
    return out


# ─────────────────────────────────────────── 8-2 기본 글꼴 슬롯
def _cstr_at(d, i):
    """i 를 포함하는 널종단 문자열의 (시작, 끝) - 앞쪽 널까지 되짚는다."""
    a = d.rfind(b"\x00", 0, i)
    a = 0 if a < 0 else a + 1
    b = d.find(b"\x00", i)
    return (a, len(d) if b < 0 else b)


def scan_font_slots(img):
    """cp932 글꼴 이름이 든 16B 슬롯. 코드에서 push 되는 것만 후보로 올린다."""
    found = {}
    for s in img.secs:
        if s["exec"]:
            continue
        d = img.data(s)
        for text, kind in FONT_KEYS:
            pat = text.encode("cp932")
            i = d.find(pat)
            while i >= 0:
                a, b = _cstr_at(d, i)
                if b - a <= 15:
                    off = s["ptr"] + a
                    z = b
                    while z < len(d) and d[z] == 0:
                        z += 1
                    found.setdefault(off, {
                        "kind": kind, "name": d[a:b], "slot": z - a,
                        "sec": s["name"], "write": s["write"],
                        "refs": img.find_dword_refs(img.va(off)),
                    })
                i = d.find(pat, i + 1)

    # 코드에서 참조되고 16B 간격으로 붙은 gothic/mincho 짝 = 기본 글꼴 (8-2)
    pair = None
    refd = {o: f for o, f in found.items() if f["refs"]}
    for o, f in sorted(refd.items()):
        o2 = o + 16
        g = refd.get(o2)
        if g and {f["kind"], g["kind"]} == {"gothic", "mincho"} \
                and f["slot"] >= 16 and g["slot"] >= 16:
            # 두 참조가 같은 함수 안(가까이)에 있으면 8-2 의 그 코드다
            near = min(abs(x - y) for x in f["refs"] for y in g["refs"])
            pair = (o, o2, near)
            break

    out = []
    for o, f in sorted(found.items()):
        name = "%s(%s)" % (_safe(f["name"].decode("cp932", "replace")), f["name"].hex())
        why = "%s %s / 슬롯 %dB / push %d곳" % (
            f["sec"], name, f["slot"], len(f["refs"]))
        if not f["write"]:
            why += " / 쓰기불가 섹션"
        if pair and o in (pair[0], pair[1]):
            key = "OFF_FONT_GOTHIC" if f["kind"] == "gothic" else "OFF_FONT_MINCHO"
            out.append(Cand(key, o, SURE, why + " / 16B 간격 짝(참조 %dB 이내)" % pair[2]))
        elif f["refs"] and f["slot"] >= 16:
            out.append(Cand("OFF_FONT_FALLBACK", o, GUESS, why + " / 짝이 아닌 슬롯"))
        else:
            out.append(Cand("(글꼴 문자열)", o, GUESS, why))
    return out


# ─────────────────────────────────────────── 15 화면 폭/높이
def _imm_hits(img, value):
    """dword 즉치 후보: `push imm32`(68) / `mov reg,imm32`(B8-BF) 문맥만."""
    pat = struct.pack("<I", value)
    out = []
    for s in img.code_secs():
        d = img.data(s)
        i = d.find(pat)
        while i >= 0:
            if i >= 1:
                pb = d[i - 1]
                if pb == 0x68:
                    out.append((s["ptr"] + i, "push "))
                elif 0xB8 <= pb <= 0xBF:
                    out.append((s["ptr"] + i, "mov %s," % REGS[pb - 0xB8]))
            i = d.find(pat, i + 1)
    return out


def scan_screen(img, w, h, verbose=False):
    ws, hs = _imm_hits(img, w), _imm_hits(img, h)
    out = []
    for off, ctx in ws:
        near = [o for o, _ in hs if abs(o - off) <= 32]
        conf = SURE if near else GUESS
        why = "%s%d" % (ctx, w) + (" / 높이 즉치와 %dB 이내 쌍" % min(
            abs(o - off) for o in near) if near else " / 짝 없음")
        if conf == GUESS and not verbose:
            continue
        out.append(Cand("OFF_SCREEN_W", off, conf, why))
    for off, ctx in hs:
        near = [o for o, _ in ws if abs(o - off) <= 32]
        conf = SURE if near else GUESS
        why = "%s%d" % (ctx, h) + (" / 폭 즉치와 %dB 이내 쌍" % min(
            abs(o - off) for o in near) if near else " / 짝 없음")
        if conf == GUESS and not verbose:
            continue
        out.append(Cand("OFF_SCREEN_H", off, conf, why))
    return out


# ─────────────────────────────────────────── 9 코드 케이브 / 데이터 여유
def _zero_runs(d, minlen):
    return [(m.start(), m.end() - m.start())
            for m in re.finditer(rb"\x00{%d,}" % minlen, d)]


def scan_cave(img, minlen):
    """실행 섹션의 연속 0 패딩 -> CAVE_VA (VA 로 보고)."""
    out = []
    for s in img.code_secs():
        d = img.data(s)
        for start, ln in sorted(_zero_runs(d, minlen), key=lambda t: -t[1])[:3]:
            off = s["ptr"] + start
            va = img.va(off)
            va16 = _align(va, 16)
            room = ln - (va16 - va)
            end_rva = img.mapped_end_rva(s)
            if (va16 - img.base) + room > end_rva:      # 매핑 밖이면 못 쓴다
                room = end_rva - (va16 - img.base)
            if room < minlen:
                continue
            tail = start + ln >= len(d)
            out.append(Cand("CAVE_VA", va16, SURE if tail else GUESS,
                            "%s %s 연속 0 %dB (VA 0x%08X-0x%08X, 16B 정렬 후 %dB 여유)"
                            % (s["name"], "섹션 끝 패딩" if tail else "중간 빈 구간",
                               ln, va, va + ln, room), is_va=True))
    out.sort(key=lambda c: c.conf != SURE)
    return out


def scan_buf(img, minlen=256):
    """쓰기 가능 섹션의 연속 0 구간 -> BUF_VA / NBYTES_VA."""
    out = []
    for s in img.secs:
        if s["exec"] or not s["write"]:
            continue
        d = img.data(s)
        for start, ln in sorted(_zero_runs(d, minlen), key=lambda t: -t[1])[:3]:
            off = s["ptr"] + start
            va = img.va(off)
            # 눈에 잘 띄는 주소를 고른다 (0x1000 정렬이 남으면 그쪽)
            for al in (0x1000, 0x100, 0x10):
                cand = _align(va, al)
                if cand - va + 0x100 <= ln:
                    break
            room = ln - (cand - va)
            out.append(Cand("BUF_VA", cand, SURE if room >= 0x100 else GUESS,
                            "%s 연속 0 %dB (VA 0x%08X-0x%08X) / 정렬 후 %dB 여유, "
                            "NBYTES_VA = BUF_VA+0x40"
                            % (s["name"], ln, va, va + ln, room), is_va=True))
            out.append(Cand("NBYTES_VA", cand + 0x40, out[-1].conf,
                            "위 버퍼 뒤 4B (64B 읽기 버퍼 다음)", is_va=True))
            break                                    # 섹션당 가장 큰 구간 하나
    return out


def scan_iat(img):
    want = ("CreateFileA", "ReadFile", "CloseHandle")
    got = img.iat(set(want))
    return [Cand("IAT_" + n, got[n], SURE, "가져오기 테이블 (KERNEL32)", is_va=True)
            for n in want if n in got]


# ─────────────────────────────────────────── 출력
GROUPS = [
    ("[6-1] 리드바이트 비트맵 (32B)", ["OFF_LEAD_BITMAP"]),
    ("[6-2] 폰트 charset (0x80 -> 0x81)", ["OFF_CHARSET_BODY", "OFF_CHARSET_ENUM"]),
    ("[6-3] SJIS 리드 판정 idiom (보조 경로)", ["SJIS_IDIOM"]),
    ("[8-2] 기본 글꼴 슬롯 (16B)", ["OFF_FONT_GOTHIC", "OFF_FONT_MINCHO",
                                    "OFF_FONT_FALLBACK", "(글꼴 문자열)"]),
    ("[15] 화면 폭/높이 dword", ["OFF_SCREEN_W", "OFF_SCREEN_H"]),
    ("[9] 코드 케이브 / 데이터 여유 (VA)", ["CAVE_VA", "BUF_VA", "NBYTES_VA",
                                            "IAT_CreateFileA", "IAT_ReadFile",
                                            "IAT_CloseHandle"]),
]


def _report(img, cands):
    print("exe: %s (%d bytes, ImageBase 0x%08X)" % (img.path, len(img.raw), img.base))
    for s in img.secs:
        flags = ("실행 " if s["exec"] else "") + ("쓰기" if s["write"] else "")
        print("  %-8s VA 0x%08X-0x%08X  file 0x%X+0x%X  %s"
              % (s["name"], img.base + s["rva"], img.base + s["rva"] + s["vsz"],
                 s["ptr"], s["rsz"], flags))
    print()
    for title, keys in GROUPS:
        rows = [c for c in cands if c.key in keys]
        print(title)
        if not rows:
            print("  후보 없음 - 수동으로 찾아야 한다 (SKILL.md)")
        for c in rows:
            print("  %-17s %-12s %-4s %s" % (c.key, c.text(), c.conf, _safe(c.why)))
        print()
    print("자동 탐색 안 됨: OFF_FILTER_PITCH/PATTERN/PUSH/JCC (8-1 글꼴 목록 필터), "
          "OFF_MOVIE_SCALE")
    print("확인: ujyu exe disasm <exe> at <VA> / xref <VA>")


def _snippet(cands, base):
    def one(key):
        c = [x for x in cands if x.key == key]
        if not c:
            return "None   # 못 찾음 - SKILL.md 보고 수동"
        best = min(c, key=lambda x: x.conf != SURE)
        mark = "" if best.conf == SURE else "   # 추정 - 확인 필요"
        return best.text() + mark

    def many(key):
        c = [x for x in cands if x.key == key and x.conf == SURE]
        return "[%s]" % ", ".join(x.text() for x in c) if c else "[]   # 못 찾음"

    idiom = [x for x in cands if x.key == "SJIS_IDIOM"]
    lines = [
        "# ujyu exe scan 결과 (사람이 확인한 뒤 남길 것)",
        "IMAGE_BASE          = 0x%08X" % base,
        "OFF_LEAD_BITMAP     = %s" % one("OFF_LEAD_BITMAP"),
        "OFF_CHARSET_BODY    = %s" % one("OFF_CHARSET_BODY"),
        "OFF_CHARSET_ENUM    = %s" % one("OFF_CHARSET_ENUM"),
        "SJIS_IDIOM          = [%s]" % ", ".join(
            '(0x%X, "%s")' % (x.off, x.hexrep) for x in idiom),
        "",
        "OFF_FONT_GOTHIC     = %s" % one("OFF_FONT_GOTHIC"),
        "OFF_FONT_MINCHO     = %s" % one("OFF_FONT_MINCHO"),
        "OFF_FONT_FALLBACK   = %s" % one("OFF_FONT_FALLBACK"),
        "",
        "OFF_SCREEN_W = %s" % many("OFF_SCREEN_W"),
        "OFF_SCREEN_H = %s" % many("OFF_SCREEN_H"),
        "",
        "CAVE_VA         = %s" % one("CAVE_VA"),
        "BUF_VA          = %s" % one("BUF_VA"),
        "NBYTES_VA       = %s" % one("NBYTES_VA"),
        "IAT_CreateFileA = %s" % one("IAT_CreateFileA"),
        "IAT_ReadFile    = %s" % one("IAT_ReadFile"),
        "IAT_CloseHandle = %s" % one("IAT_CloseHandle"),
        "",
        "# 아래는 스캐너가 찾지 못한다 (8-1 글꼴 목록 필터 / 무비 배율)",
        "OFF_FILTER_PITCH    = []",
        "OFF_FILTER_PATTERN  = None",
        "OFF_FILTER_PUSH     = None",
        "OFF_FILTER_JCC      = None",
        "OFF_MOVIE_SCALE     = None",
    ]
    print("\n".join(lines))


def scan(path, width=640, height=480, cave_min=200, verbose=False):
    img = Img(path)
    cands = []
    cands += scan_lead_bitmap(img, verbose)
    cands += scan_charset(img)
    cands += scan_sjis_idiom(img)
    cands += scan_font_slots(img)
    cands += scan_screen(img, width, height, verbose)
    cands += scan_cave(img, cave_min)
    cands += scan_buf(img)
    cands += scan_iat(img)
    return img, cands


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu exe scan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="config 에 넣을 exe 오프셋 후보 스캔 (SKILL 6~9절의 찾는 법 자동화)",
        epilog="exe 를 고치지 않는다. 값은 사람이 확인해 config.py 에 붙여넣는다.\n"
               "예:\n"
               "  ujyu exe scan orig/game.exe                 # 후보 표\n"
               "  ujyu exe scan orig/game.exe --config        # config 스니펫만\n"
               "  ujyu exe scan orig/game.exe --width 800 --height 600\n")
    ap.add_argument("exe", help="스캔할 무패치 원본 exe 경로")
    ap.add_argument("--width", type=int, default=640,
                    help="찾을 화면 폭 dword (기본: 640)")
    ap.add_argument("--height", type=int, default=480,
                    help="찾을 화면 높이 dword (기본: 480)")
    ap.add_argument("--cave-min", type=int, default=200,
                    help="코드 케이브로 인정할 최소 연속 0 바이트 (기본: 200)")
    ap.add_argument("--config", action="store_true",
                    help="사람이 읽는 표 없이 config.py 스니펫만 출력")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="신뢰도 낮은 후보(짝 없는 즉치 등)까지 전부 표시")
    a = ap.parse_args()

    try:
        img, cands = scan(a.exe, a.width, a.height, a.cave_min, a.verbose)
    except FileNotFoundError:
        raise SystemExit("exe 를 열 수 없다: %s" % a.exe)
    except pefile.PEFormatError as e:
        raise SystemExit("PE 파일이 아니다: %s (%s)" % (a.exe, e))

    if a.config:
        _snippet(cands, img.base)
        return 0
    _report(img, cands)
    print()
    print("--- config.py 스니펫 " + "-" * 40)
    _snippet(cands, img.base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
