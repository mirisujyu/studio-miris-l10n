#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전체 추출 - 번역에 필요한 모든 텍스트·정보를 뽑는다.

기존 처리기를 순서대로 엮어 돌리는 **오케스트레이터**다(서브프로세스 없이 직접 호출):

  1 scenario   ujyu.scn(vneg.extract)      -> config.STRINGS (v2 strings.json)
  2 csv        ujyu.formats.common_csv     -> _common_csv_todo.txt (COMMON_CSV 후보)
  3 exe        pefile + 인라인 cp932 스캔  -> config.UI_STRINGS 초안
  4 image      ujyu.formats.axr            -> config.IMAGE_SPEC 골격 (manifest)
  5 nameplates ujyu.gen_nameplates         -> config.NAMEPLATES_MD (+ _names.json)

번역이 들어있을 수 있는 산출물(strings.json·ui_strings.json·IMAGES.md)은 이미 있으면
**덮어쓰지 않고 건너뛴다**. `--force` 를 줄 때만 덮어쓰고 그때도 `.bak` 을 남긴다.
`--out DIR` 을 주면 config 경로 대신 그 폴더에 쓴다(검증·미리보기용).

각 단계는 실패해도 나머지를 계속 진행하고, 끝에 실패 목록을 요약한다(실패 시 종료 코드 1).
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
from collections import Counter
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from ujyu.titleconfig import config as C          # noqa: E402
from ujyu.formats import axr, common_csv          # noqa: E402

STEPS = ["scenario", "csv", "exe", "image", "nameplates"]
_TITLES = {"scenario": "시나리오 텍스트", "csv": "common.csv 표시 문자열",
           "exe": "exe 텍스트", "image": "이미지 텍스트 목록", "nameplates": "네임플레이트"}

# 표시용 일본어 문자(히라가나·전각가타카나·한자·반복기호). 반각 가나는 바이너리
# 스캔에서 오탐이 많아 넣지 않는다.
_JP = re.compile("[぀-ゟ゠-ヿ一-鿿々〆]")
# 반각 가나(U+FF61~U+FF9F). exe 바이너리 스캔에서 오탐의 표지로 쓴다.
_HALFKANA = re.compile("[｡-ﾟ]")


def _safe(s):
    """콘솔(CP949)에 없는 문자를 지운 문자열. 일본어 원문은 파일로만 남긴다."""
    return str(s).encode("cp949", "replace").decode("cp949")


def _rel(p):
    """읽기 좋은 경로 (너무 길면 뒤쪽만)."""
    p = str(p)
    return p if len(p) <= 96 else "..." + p[-93:]


class Ctx(object):
    """단계 공통 설정 - 출력 위치와 덮어쓰기 정책."""

    def __init__(self, out=None, force=False, verbose=False):
        self.out = out
        self.force = force
        self.verbose = verbose
        self.orig = getattr(C, "ORIG_DIR", "") or ""
        self.work = self.out or getattr(C, "WORK_DIR", "") or ""

    def dest(self, cfg_value, name):
        """산출 경로. --out 이 있으면 그 폴더의 name, 없으면 config 값."""
        if self.out:
            return os.path.join(self.out, name)
        if cfg_value:
            return cfg_value
        return os.path.join(self.work, name)

    def tmp(self, name):
        """config 대응이 없는 부산물(작업 파일) 경로."""
        return os.path.join(self.work, name)


def _mkparent(path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


def _guard(dest, force):
    """덮어쓰기 정책. 반환 (써도 되는가, 알림). --force 면 .bak 을 남긴다."""
    if not os.path.exists(dest):
        return True, None
    if not force:
        return False, "이미 있어 건너뜀 (덮어쓰려면 --force): %s" % _rel(dest)
    shutil.copy2(dest, dest + ".bak")
    return True, "기존 파일 백업 -> %s.bak" % os.path.basename(dest)


def _archives(names):
    """아카이브 이름 목록 -> 존재하는 원본 경로들. 반환 (경로들, 없는것들)."""
    found, missing = [], []
    for a in (names or []):
        p = a if os.path.isabs(a) else os.path.join(getattr(C, "ORIG_DIR", "") or "",
                                                    os.path.basename(a))
        (found if os.path.isfile(p) else missing).append(p)
    return found, missing


# ─────────────────────────────────────────── 1. 시나리오 텍스트
def step_scenario(ctx):
    """config.ARCHIVES 에서 v2 strings.json 을 만든다 (ujyu.scn extract 경로)."""
    from ujyu import scn                          # vneg.extract 를 쓰는 추출 경로

    dest = ctx.dest(getattr(C, "STRINGS", None), "strings.json")
    ok, note = _guard(dest, ctx.force)
    if note:
        print("    %s" % note)
    if not ok:
        return "스킵 (기존 번역 보호)"

    arcs, missing = _archives(getattr(C, "ARCHIVES", None))
    if not arcs:
        raise SystemExit("시나리오 아카이브를 찾을 수 없습니다 (config.ARCHIVES / ORIG_DIR 확인)")
    if missing:
        print("    경고: 원본에 없어 제외: %s" % ", ".join(os.path.basename(m) for m in missing))

    _mkparent(dest)
    argv = list(arcs) + ["-o", dest]
    rre = getattr(C, "RESOURCE_RE", None)
    if rre:
        argv += ["--resource-re", rre]
    buf = io.StringIO()
    with redirect_stdout(buf):                    # 원문(일본어)이 섞여 나오므로 걸러서 다시 찍는다
        scn.cmd_extract(argv)
    if ctx.verbose:
        for line in buf.getvalue().splitlines():
            print("    %s" % _safe(line))

    rows = json.load(io.open(dest, encoding="utf-8"))
    kinds = Counter(r.get("kind") for r in rows)
    kind_s = " ".join("%s=%d" % (k, kinds[k]) for k in sorted(kinds))
    return "%d조각 (%s) -> %s" % (len(rows), kind_s, _rel(dest))


# ─────────────────────────────────────────── 2. common.csv 표시 문자열
def step_csv(ctx):
    """아카이브 안 common.csv 의 `string,<키>,<값>` 중 일본어가 남은 것을 모은다."""
    # common.csv 는 시나리오 아카이브마다 각각 들어 있고 내용이 다르다(제자리 편집 원칙).
    arcs, _missing = _archives(getattr(C, "ARCHIVES", None))
    if not arcs:
        raise SystemExit("아카이브를 찾을 수 없습니다 (config.ARCHIVES / ORIG_DIR 확인)")

    hits = []                                     # (아카이브, 키, 값)
    for p in arcs:
        data, entries, tbl = axr.load(p)
        for name, off, sz in entries:
            if os.path.basename(name).lower() != "common.csv":
                continue
            buf = axr.getfile(data, tbl, off, sz)
            for typ, key, rest in common_csv.fields(buf):
                if typ != "string" or not rest:
                    continue
                val = rest.decode("cp932", "replace")
                if _JP.search(val):
                    hits.append((os.path.basename(p), key, val))

    dest = ctx.tmp("_common_csv_todo.txt")
    _mkparent(dest)
    keys = []
    with io.open(dest, "w", encoding="utf-8") as f:
        f.write("# common.csv 표시 문자열 후보 (ujyu extract 생성)\n"
                "#\n"
                "# 아카이브 안 common.csv 의 `string,<키>,<값>` 중 일본어가 남은 것들이다.\n"
                "# 번역할 것만 골라 아래 스니펫을 config.py 의 COMMON_CSV 에 붙여 값을 채운다.\n"
                "# (주입은 아카이브별 제자리 편집 - docs/formats/COMMON_CSV.md)\n\n")
        for arc, key, val in hits:
            f.write("# %-14s string,%s,%s\n" % (arc, key, val))
        f.write("\nCOMMON_CSV = {\n")
        for key, val in sorted(dict((k, v) for _a, k, v in hits).items()):
            keys.append(key)
            f.write("    # %s\n    %r: \"\",\n" % (val, key))
        f.write("}\n")

    shown = ", ".join(sorted(set(keys))[:12]) or "(없음)"
    if len(set(keys)) > 12:
        shown += " ..."
    return "%d건 / 키 %d개 [%s] -> %s" % (len(hits), len(set(keys)), shown, _rel(dest))


# ─────────────────────────────────────────── 3. exe 텍스트
# 리소스 파서는 patch_ui 의 패처와 같은 구조를 읽기 전용으로 훑는다(WINDOWS_UI.md).
# 구조를 따라가지 않고 UTF-16 를 통째로 긁으면 메뉴 ID·컨트롤 좌표가 글자로 섞인다.
def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


def _rd_wsz(b, o):
    """UTF-16LE 널종료 문자열 -> (문자열, 다음 오프셋)."""
    out = []
    while o + 1 < len(b):
        w = _u16(b, o)
        o += 2
        if w == 0:
            break
        out.append(chr(w))
    return "".join(out), o


def _menu_texts(raw):
    """MENU 리소스의 항목 라벨들 (단축키 표기는 뗀다)."""
    out = []

    def walk(o):
        while o < len(raw):
            flags = _u16(raw, o)
            o += 2
            popup = flags & 0x10
            if not popup:
                o += 2                            # 메뉴 ID
            txt, o = _rd_wsz(raw, o)
            out.append(txt.split("\t")[0])
            if popup:
                o = walk(o)
            if flags & 0x80:                      # MF_END
                break
        return o

    walk(4)
    return out


def _dialog_texts(raw):
    """DIALOG 리소스의 제목 + 컨트롤 캡션들."""
    if raw[:2] == b"\x01\x00" and raw[2:4] == b"\xff\xff":
        return []                                 # DLGTEMPLATEEX 는 다루지 않는다
    out = []
    style = int.from_bytes(raw[0:4], "little")
    cdit = _u16(raw, 8)
    o = 18

    def sz_or_ord(o):
        w = _u16(raw, o)
        if w == 0:
            return o + 2, None
        if w == 0xFFFF:
            return o + 4, None
        s, o2 = _rd_wsz(raw, o)
        return o2, s

    for _field in ("menu", "class"):
        o, s = sz_or_ord(o)
        if s:
            out.append(s)
    title, o = _rd_wsz(raw, o)
    out.append(title)
    if style & 0x40:                              # DS_SETFONT
        o += 2
        _face, o = _rd_wsz(raw, o)
    for _ in range(cdit):
        o = (o + 3) & ~3
        o += 18
        o, _cls = sz_or_ord(o)                    # 클래스명은 번역 대상이 아니다
        o, cap = sz_or_ord(o)
        if cap:
            out.append(cap)
        ec = _u16(raw, o)
        o += 2 + ec
    return out


def _stringtable_texts(raw):
    """STRINGTABLE 블록의 16개 항목."""
    out, o = [], 0
    for _ in range(16):
        if o + 2 > len(raw):
            break
        ln = _u16(raw, o)
        o += 2
        if ln:
            out.append(raw[o:o + ln * 2].decode("utf-16le", "replace"))
            o += ln * 2
    return out


def _cp932_run(d, i):
    """d[i:] 에서 cp932 로 읽히는 최대 런. 반환 (끝 인덱스, 텍스트)."""
    n, chars = len(d), []
    while i < n:
        b = d[i]
        if b == 0:
            break
        if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D):
            chars.append(chr(b))
            i += 1
            continue
        if 0xA1 <= b <= 0xDF:                     # 반각 가나
            chars.append(bytes(bytearray([b])).decode("cp932"))
            i += 1
            continue
        if (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC) and i + 1 < n:
            try:
                chars.append(bytes(bytearray(d[i:i + 2])).decode("cp932"))
            except UnicodeDecodeError:
                break
            i += 2
            continue
        break
    return i, "".join(chars)


def scan_exe_resources(path):
    """MENU/DIALOG/STRINGTABLE 리소스의 일본어 문자열. 반환 {그룹: [문자열]}."""
    import pefile
    group = {4: ("menu", _menu_texts, []), 5: ("dialog", _dialog_texts, []),
             6: ("string_table", _stringtable_texts, [])}
    pe = pefile.PE(path)
    try:
        res = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
        if res is not None:
            for te in res.entries:
                if te.id not in group:
                    continue
                _key, parse, bucket = group[te.id]
                for ne in te.directory.entries:
                    for le in ne.directory.entries:
                        d = le.data.struct
                        raw = pe.get_data(d.OffsetToData, d.Size)
                        try:
                            texts = parse(raw)
                        except Exception:         # 변종 리소스 하나로 전체를 버리지 않는다
                            continue
                        for s in texts:
                            s = s.strip("\x00")
                            if _JP.search(s) and s not in bucket:
                                bucket.append(s)
    finally:
        pe.close()
    return dict((key, bucket) for key, _p, bucket in group.values())


def _plausible(txt, min_jp):
    """일본어 텍스트로 볼 만한가. x86 코드가 우연히 cp932 로 읽히는 것을 걸러낸다."""
    njp = len(_JP.findall(txt))
    if njp < min_jp:
        return False
    if _HALFKANA.search(txt):                     # 반각 가나는 코드 바이트 오탐의 전형
        return False
    return njp * 5 >= len(txt) * 2                # 40% 이상이 일본어 문자


def scan_exe_inline(path, min_jp=2, max_len=200):
    """데이터 섹션의 널종료 cp932 문자열(일본어 포함). 반환 [(파일오프셋, 문자열)].

    실행(코드) 섹션은 제외한다 - x86 바이트가 우연히 cp932 로 읽혀 수백 건의
    오탐이 나온다. 화면에 나가는 문자열 상수는 .rdata/.data 에 있다.
    """
    import pefile
    pe = pefile.PE(path, fast_load=True)
    hits, seen = [], set()
    try:
        for sec in pe.sections:
            nm = sec.Name.rstrip(b"\x00").decode("latin1", "replace")
            if nm.startswith(".rsrc") or nm.startswith(".reloc"):
                continue                          # 리소스는 UTF-16, 재배치표는 데이터
            if sec.Characteristics & 0x20000000:   # IMAGE_SCN_MEM_EXECUTE
                continue
            d = sec.get_data()
            base = sec.PointerToRawData
            i, n = 0, len(d)
            while i < n:
                if d[i] == 0:
                    i += 1
                    continue
                end, txt = _cp932_run(d, i)
                if txt and end < n and d[end] == 0:
                    if len(txt) <= max_len and _plausible(txt, min_jp) and txt not in seen:
                        seen.add(txt)
                        hits.append((base + i, txt))
                    i = end + 1                   # 널종료 런은 통째로 넘어간다
                else:                             # 종료되지 않은 런 = 경계가 어긋난 것
                    i += 1
    finally:
        pe.close()
    return hits


def step_exe(ctx):
    """config.EXE_IN 의 사용자 노출 문자열로 config.UI_STRINGS 초안을 만든다."""
    exe = getattr(C, "EXE_IN", None)
    if not exe or not os.path.isfile(exe):
        raise SystemExit("원본 exe 가 없습니다: %s (config.EXE_IN 확인)" % exe)

    dest = ctx.dest(getattr(C, "UI_STRINGS", None), "ui_strings.json")
    ok, note = _guard(dest, ctx.force)
    if note:
        print("    %s" % note)
    if not ok:
        return "스킵 (기존 번역 보호)"

    res = scan_exe_resources(exe)
    inline = scan_exe_inline(exe)

    draft = {
        "_comment": (
            "ujyu extract 가 만든 초안. 키 = exe 원문, 값 = 번역문. "
            "값은 지금 원문 그대로여서 그대로 적용해도 화면이 바뀌지 않는다 - "
            "번역한 항목만 값을 한국어로 바꿔 나간다. "
            "menu/dialog/string_table 은 리소스(UTF-16)라 길이 제한이 없고, "
            "messagebox 는 인라인 ANSI 제자리 치환이라 CP949 바이트수가 원문 SJIS "
            "바이트수를 넘으면 그 항목은 건너뛴다. 줄바꿈은 \\n 두 글자. "
            "번역 대상이 아닌 항목(글꼴 이름·내부 문자열 등)은 지운다."),
        "menu": dict((s, s) for s in res["menu"]),
        "dialog": dict((s, s) for s in res["dialog"]),
        "string_table": dict((s, s) for s in res["string_table"]),
        "messagebox": dict((t.replace("\n", "\\n"), t.replace("\n", "\\n"))
                           for _off, t in inline),
    }
    _mkparent(dest)
    with io.open(dest, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # 인라인 문자열은 위치(파일오프셋)도 남긴다 - 어느 코드가 쓰는지 추적할 때 필요하다.
    off_path = ctx.tmp("_exe_strings.txt")
    _mkparent(off_path)
    with io.open(off_path, "w", encoding="utf-8") as f:
        f.write("# %s 의 인라인 cp932 문자열 (파일오프셋<TAB>원문)\n" % os.path.basename(exe))
        for off, t in inline:
            f.write("0x%06X\t%s\n" % (off, t.replace("\n", "\\n")))
    return ("리소스 menu %d / dialog %d / string_table %d, 인라인 %d -> %s (+%s)"
            % (len(res["menu"]), len(res["dialog"]), len(res["string_table"]),
               len(inline), _rel(dest), os.path.basename(off_path)))


# ─────────────────────────────────────────── 4. 이미지 텍스트 목록
def _png_size(head):
    """PNG IHDR 에서 (폭, 높이). 아니면 None."""
    if len(head) >= 24 and head[:4] == b"\x89PNG" and head[12:16] == b"IHDR":
        return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
    return None


def step_image(ctx):
    """config.CG_ARCHIVE 의 이미지 목록·치수로 manifest 골격을 만든다."""
    cg = getattr(C, "CG_ARCHIVE", None)
    if not cg:
        raise SystemExit("config.CG_ARCHIVE 가 비어 있습니다")
    arcs, missing = _archives([cg])
    if not arcs:
        raise SystemExit("이미지 아카이브가 없습니다: %s" % (missing[0] if missing else cg))

    dest = ctx.dest(getattr(C, "IMAGE_SPEC", None), "IMAGES.md")
    ok, note = _guard(dest, ctx.force)
    if note:
        print("    %s" % note)
    if not ok:
        return "스킵 (manifest 는 사람이 채우는 문서)"

    data, entries, tbl = axr.load(arcs[0])
    rows = []
    for name, off, sz in sorted(entries):
        wh = _png_size(axr.getfile(data, tbl, off, min(24, sz)))
        rows.append((name, wh, sz))
    n_png = sum(1 for _n, wh, _s in rows if wh)

    variant = getattr(C, "IMAGE_VARIANT", None) or "SampleSans"
    _mkparent(dest)
    with io.open(dest, "w", encoding="utf-8") as f:
        f.write("# 이미지 텍스트 manifest (골격)\n\n"
                "`ujyu extract` 가 `%s` 를 훑어 만든 **초안**이다. 아래 목록은 아카이브에\n"
                "실제로 든 이미지 엔트리와 치수뿐이다 - **원문·bbox·좌표·색은 사람이 채운다**\n"
                "(원본을 열어 재는 일이라 명령이 없다. 규칙은 engine/samples/images.sample.md).\n\n"
                "## 채우는 순서\n\n"
                "1. 아래 목록에서 **글자가 있는 파일만** 남기고 나머지 행은 지운다.\n"
                "2. 남긴 파일의 원문과 글자 바운딩 박스를 원본에서 재어 표에 적는다.\n"
                "3. 글자를 지운 베이스 이미지를 `config.IMAGE_TEXTLESS_DIR` 에 같은 파일명으로 둔다.\n"
                "4. 문서 끝 JSON 블록의 `operations` 에 항목을 추가한다(형식은 samples/images.sample.md).\n"
                "5. `ujyu image --check` 로 명세·입력을 검사한 뒤 `--variant <글꼴>` 로 렌더한다.\n\n"
                "경로는 이 문서에 적지 않는다 - 전부 config.py 의 `IMAGE_*` 에서 온다.\n\n"
                "## 이미지 엔트리 (%d개 중 PNG %d개)\n\n" % (os.path.basename(arcs[0]),
                                                            len(rows), n_png))
        f.write("| 파일 | 치수 | 바이트 | 원문(사람이 채운다) | bbox(사람이 채운다) |\n")
        f.write("|---|---|---|---|---|\n")
        for name, wh, sz in rows:
            f.write("| %s | %s | %d |  |  |\n"
                    % (name, ("%dx%d" % wh) if wh else "-", sz))
        f.write("\n## 명세 블록\n\n"
                "`operations` 가 비어 있으면 `ujyu image` 는 아무것도 렌더하지 않는다.\n\n")
        f.write("<!-- image-text-manifest:start -->\n```json\n")
        json.dump({"schema": 1,
                   "description": "이미지 텍스트 렌더 manifest (골격 - operations 를 채운다)",
                   "fonts": [{"name": variant,
                              "regular": "%s-Regular.ttf" % variant,
                              "bold": "%s-Bold.ttf" % variant}],
                   "operations": []},
                  f, ensure_ascii=False, indent=2)
        f.write("\n```\n<!-- image-text-manifest:end -->\n")
    return "엔트리 %d개 (PNG %d개) -> %s" % (len(rows), n_png, _rel(dest))


# ─────────────────────────────────────────── 5. 네임플레이트
def step_nameplates(ctx):
    """strings.json 의 speaker 를 집계해 NAMEPLATES.md 를 만든다 (gen_nameplates 재사용)."""
    from ujyu import gen_nameplates as GN

    src = ctx.dest(getattr(C, "STRINGS", None), "strings.json")
    if not os.path.isfile(src):
        src = getattr(C, "STRINGS", None)
    if not src or not os.path.isfile(src):
        raise SystemExit("strings.json 이 없습니다 (먼저 scenario 단계를 돌리세요)")

    names = GN.aggregate(json.load(io.open(src, encoding="utf-8")))
    names_path = ctx.tmp("_names.json")
    _mkparent(names_path)
    json.dump(names, io.open(names_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    table_path = getattr(C, "NAMEPLATES", None)
    table, missing_table = {}, False
    if table_path and os.path.isfile(table_path):
        table = GN.load_table(table_path)
    else:
        missing_table = True

    dest = ctx.dest(getattr(C, "NAMEPLATES_MD", None), "NAMEPLATES.md")
    _mkparent(dest)
    n, missing = GN.build_md(names, table, dest)
    msg = "화자 %d명 -> %s (+%s)" % (n, _rel(dest), os.path.basename(names_path))
    if missing_table:
        msg += " | 번역표 없음: %s 를 samples/nameplates.sample.json 으로 만들 것" \
               % _rel(table_path or "config.NAMEPLATES")
    elif missing:
        msg += " | 번역표 누락 %d명" % len(missing)
    return msg


_FN = {"scenario": step_scenario, "csv": step_csv, "exe": step_exe,
       "image": step_image, "nameplates": step_nameplates}


def _next_steps():
    print("다음에 할 일 (번역 착수 전 사전 작업)")
    print("  1. config.py 채우기: COMMON_CSV(번역 문구) / IMAGE_* / FONT_* ")
    print("     - COMMON_CSV 후보는 _common_csv_todo.txt, exe 문자열은 ui_strings.json")
    print("  2. translation/nameplates.json 을 채우고 'ujyu nameplates' 로 표 갱신")
    print("  3. CHARACTERS.md / GLOSSARY.md 작성 (인물·용어 표기 결정 = 사람 판단)")
    print("  4. IMAGES.md 의 원문·bbox 채우기 + 무문자 베이스 이미지 준비")
    print("  5. 'ujyu inspect' 로 config 채움 상태와 다음 할 일 확인")
    print("  6. 번역: 'ujyu filter stats' -> 'ujyu filter dump' -> 'ujyu filter apply'")


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu extract",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="번역에 필요한 모든 텍스트·정보를 추출한다 (시나리오·csv·exe·이미지·화자)",
        epilog="단계: %s\n"
               "예:\n"
               "  ujyu extract                        # 전 단계 (기존 산출물은 건너뜀)\n"
               "  ujyu extract --only exe,nameplates  # 일부 단계만\n"
               "  ujyu extract --out _draft           # config 경로 대신 이 폴더에 (미리보기)\n"
               "  ujyu extract --only exe --force     # 덮어쓰기 (.bak 을 남긴다)\n"
               "\n번역이 들어있을 수 있는 산출물(strings.json·ui_strings.json·IMAGES.md)은\n"
               "이미 있으면 덮어쓰지 않는다. --force 를 줘도 .bak 을 먼저 남긴다."
               % ",".join(STEPS))
    ap.add_argument("--only", metavar="목록", default=None,
                    help="이 단계만, 쉼표 구분 (%s)" % ",".join(STEPS))
    ap.add_argument("--force", action="store_true",
                    help="이미 있는 산출물을 덮어쓴다 (.bak 백업 후)")
    ap.add_argument("--out", metavar="DIR", default=None,
                    help="config 경로 대신 이 폴더에 쓴다 (기본: config 의 각 경로)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="각 단계 원본 도구의 출력까지 표시")
    a = ap.parse_args()

    order = STEPS
    if a.only:
        order = [s.strip() for s in a.only.split(",") if s.strip()]
        bad = [s for s in order if s not in _FN]
        if bad:
            raise SystemExit("알 수 없는 단계: %s (가능: %s)"
                             % (", ".join(bad), ", ".join(STEPS)))

    ctx = Ctx(a.out, a.force, a.verbose)
    if a.out:
        os.makedirs(a.out, exist_ok=True)

    print("ujyu extract - 번역용 텍스트·정보 추출")
    print("원본   : %s" % (ctx.orig or "(미설정)"))
    print("출력   : %s" % (a.out or "config 경로 (작업 파일은 %s)" % (ctx.work or "?")))
    print("단계   : %s" % ", ".join(order))
    print()

    fails = []
    for i, step in enumerate(order, 1):
        print("[%d/%d] %s (%s)" % (i, len(order), _TITLES[step], step))
        try:
            msg = _FN[step](ctx)
        except Exception as e:                    # 한 단계 실패로 나머지를 멈추지 않는다
            fails.append((step, "%s: %s" % (type(e).__name__, _safe(e))))
            print("    실패: %s" % fails[-1][1])
        else:
            print("    %s" % _safe(msg))
        print()

    if fails:
        print("실패 %d단계:" % len(fails))
        for step, why in fails:
            print("  %-10s %s" % (step, why))
        print()
    _next_steps()
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
