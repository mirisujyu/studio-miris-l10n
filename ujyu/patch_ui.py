# -*- coding: utf-8 -*-
"""Windows UI 한글 패치 (SKILL 10절).

- MENU/DIALOG/STRINGTABLE 리소스를 UpdateResourceW 로 UTF-16 교체.
- MessageBox 등 인라인 ANSI 문자열을 CP949 로 제자리 치환.
- 다이얼로그 폰트를 한글 폰트로.

원문(일본어) 문자열을 기준으로 찾으므로 **무패치 원본 exe** 에서 바로 적용된다.

  from patch_ui import apply_ui
  apply_ui(src_exe, dst_exe)          # config 의 UI_STRINGS / DLGFONT 사용

CLI:  ujyu exe ui [<src.exe> <dst.exe>]     (생략 시 config.EXE_IN / config.EXE_OUT)
"""
import json, io, os, struct, shutil, ctypes, sys, argparse
from ctypes import wintypes
import pefile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ujyu.titleconfig import config as C


# ---- WCHAR 헬퍼 ----
def _rd_wsz(b, o):
    s = bytearray()
    while o + 1 < len(b):
        w = b[o] | (b[o + 1] << 8); o += 2
        if w == 0: break
        s += struct.pack('<H', w)
    return s.decode('utf-16le'), o

def _wsz(s): return s.encode('utf-16le') + b'\x00\x00'

def _align4(b):
    while len(b) % 4: b += b'\x00'
    return b


def _make_tr(M):
    def tr(s):  # 정확일치 -> strip 일치(공백보존) -> 원문
        if s in M: return M[s]
        st = s.strip()
        if st and st in M:
            lead = s[:len(s) - len(s.lstrip())]; trail = s[len(s.rstrip()):]
            return lead + M[st] + trail
        return s
    def tr_menu(s):  # "라벨\t단축키" -> 라벨만
        if '\t' in s:
            p = s.split('\t'); p[0] = tr(p[0]); return '\t'.join(p)
        return tr(s)
    return tr, tr_menu


def _patch_menu(raw, tr_menu):
    out = bytearray(raw[:4])
    def walk(o):
        res = bytearray()
        while o < len(raw):
            flags = struct.unpack('<H', raw[o:o + 2])[0]; o += 2
            popup = flags & 0x10
            res += struct.pack('<H', flags)
            if not popup:
                mid = struct.unpack('<H', raw[o:o + 2])[0]; o += 2
                res += struct.pack('<H', mid)
            txt, o = _rd_wsz(raw, o)
            res += _wsz(tr_menu(txt))
            if popup:
                sub, o = walk(o); res += sub
            if flags & 0x80: break
        return res, o
    body, _ = walk(4)
    return bytes(out[:4] + body)


def _patch_string(raw, tr):
    out = bytearray(); o = 0
    for _ in range(16):
        if o + 2 > len(raw): break
        ln = struct.unpack('<H', raw[o:o + 2])[0]; o += 2
        s = raw[o:o + ln * 2].decode('utf-16le') if ln else ""
        o += ln * 2
        k = tr(s)
        out += struct.pack('<H', len(k)) + k.encode('utf-16le')
    return bytes(out)


def _patch_dialog(raw, tr, dlgfont):
    o = 0
    style, exstyle = struct.unpack('<II', raw[0:8]); o = 8
    cdit, x, y, cx, cy = struct.unpack('<5H', raw[8:18]); o = 18
    out = bytearray(raw[0:18])
    def sz_or_ord(o):
        w = struct.unpack('<H', raw[o:o + 2])[0]
        if w == 0: return raw[o:o + 2], o + 2, None
        if w == 0xFFFF: return raw[o:o + 4], o + 4, None
        s, o2 = _rd_wsz(raw, o); return None, o2, s
    for _field in ('menu', 'class'):
        b, o, s = sz_or_ord(o)
        if s is None: out += b
        else: out += _wsz(tr(s))
    title, o = _rd_wsz(raw, o); out += _wsz(tr(title))
    if style & 0x40:                                  # DS_SETFONT
        pts = struct.unpack('<H', raw[o:o + 2])[0]; o += 2
        _face, o = _rd_wsz(raw, o)
        out += struct.pack('<H', pts) + _wsz(dlgfont)
    for _ in range(cdit):
        o = (o + 3) & ~3
        out = bytearray(_align4(out))
        out += raw[o:o + 18]; o += 18
        w = struct.unpack('<H', raw[o:o + 2])[0]
        if w == 0xFFFF: out += raw[o:o + 4]; o += 4
        else:
            s, o = _rd_wsz(raw, o); out += _wsz(s)     # class명 유지
        w = struct.unpack('<H', raw[o:o + 2])[0]
        if w == 0xFFFF: out += raw[o:o + 4]; o += 4
        else:
            s, o = _rd_wsz(raw, o); out += _wsz(tr(s))
        ec = struct.unpack('<H', raw[o:o + 2])[0]; out += raw[o:o + 2 + ec]; o += 2 + ec
    return bytes(out)


def apply_ui(src, dst, ui_strings_path=None, dlgfont=None):
    """src exe 를 UI 한글 패치해 dst 로 쓴다. src 는 무패치 원본이어도 된다."""
    ui_strings_path = ui_strings_path or C.UI_STRINGS
    dlgfont = dlgfont or C.DLGFONT
    TR = json.load(io.open(ui_strings_path, encoding="utf-8"))
    M = {}; M.update(TR["menu"]); M.update(TR["dialog"]); M.update(TR["string_table"])
    tr, tr_menu = _make_tr(M)

    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy(src, dst)

    pe = pefile.PE(src)
    jobs = []
    for te in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        typ = te.id
        if typ not in (4, 5, 6): continue
        for ne in te.directory.entries:
            for le in ne.directory.entries:
                d = le.data.struct; raw = pe.get_data(d.OffsetToData, d.Size)
                resid = str(ne.name) if ne.name is not None else ne.id; lang = le.id
                if typ == 4: nb = _patch_menu(raw, tr_menu)
                elif typ == 6: nb = _patch_string(raw, tr)
                elif typ == 5:
                    if raw[:2] == b'\x01\x00' and raw[2:4] == b'\xff\xff':  # DLGTEMPLATEEX 건너뜀
                        continue
                    nb = _patch_dialog(raw, tr, dlgfont)
                jobs.append((typ, resid, lang, nb))
    pe.close()

    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    k32.BeginUpdateResourceW.restype = wintypes.HANDLE
    k32.UpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID,
                                    wintypes.WORD, wintypes.LPVOID, wintypes.DWORD]
    k32.UpdateResourceW.restype = wintypes.BOOL
    k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    k32.EndUpdateResourceW.restype = wintypes.BOOL
    h = k32.BeginUpdateResourceW(dst, False)
    if not h: raise ctypes.WinError(ctypes.get_last_error())
    for typ, resid, lang, nb in jobs:
        buf = (ctypes.c_char * len(nb)).from_buffer_copy(nb)
        name_arg = ctypes.c_wchar_p(resid) if isinstance(resid, str) else ctypes.c_void_p(resid)
        ok = k32.UpdateResourceW(h, ctypes.c_void_p(typ), name_arg, lang,
                                 ctypes.cast(buf, wintypes.LPVOID), len(nb))
        if not ok: print("  UpdateResource 실패 typ%d id%s: err%d" % (typ, resid, ctypes.get_last_error()))
    if not k32.EndUpdateResourceW(h, False): raise ctypes.WinError(ctypes.get_last_error())
    n_res = len(jobs)

    # MessageBox 인라인 (CP949, in-place)
    b = bytearray(open(dst, 'rb').read())
    pe2 = pefile.PE(src); ib = pe2.OPTIONAL_HEADER.ImageBase
    def va2off(va):
        for s in pe2.sections:
            if s.VirtualAddress <= va - ib < s.VirtualAddress + s.Misc_VirtualSize:
                return va - ib - s.VirtualAddress + s.PointerToRawData
        return None
    data = pe2.get_memory_mapped_image()
    n_mb = 0
    for jp, kr in TR.get("messagebox", {}).items():
        jpb = jp.replace("\\n", "\n").encode('cp932')
        krb = kr.replace("\\n", "\n").encode('cp949')
        idx = data.find(jpb)
        if idx < 0: print("  MB 원문 못찾음:", jp[:12]); continue
        if len(krb) > len(jpb): print("  MB 너무 김(스킵):", kr[:12]); continue
        off = va2off(ib + idx)
        b[off:off + len(jpb) + 1] = krb + b'\x00' + b'\x00' * (len(jpb) - len(krb))
        n_mb += 1
    pe2.close()
    open(dst, 'wb').write(b)
    return n_res, n_mb


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu exe ui",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="exe 의 MENU/DIALOG/STRINGTABLE + MessageBox 인라인 문자열 한글 패치",
        epilog="예:\n"
               "  ujyu exe ui                      # config.EXE_IN -> config.EXE_OUT\n"
               "  ujyu exe ui orig.exe out.exe     # 경로 직접 지정\n")
    ap.add_argument("src", nargs="?", default=C.EXE_IN,
                    help="입력 exe, 무패치 원본이어도 된다 (기본: config.EXE_IN)")
    ap.add_argument("dst", nargs="?", default=C.EXE_OUT,
                    help="출력 exe (기본: config.EXE_OUT)")
    a = ap.parse_args()
    s, d = a.src, a.dst
    nr, nmb = apply_ui(s, d)
    print("리소스 %d개 교체, MessageBox 인라인 %d개 -> %s" % (nr, nmb, d))


if __name__ == "__main__":
    raise SystemExit(main())
