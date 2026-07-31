#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py 값 조회/변경 - 그룹별로 보고, 값 하나를 찍고, 검사한 뒤 고친다.

검사 규칙은 `ujyu inspect`(ujyu/doctor.py)의 검증기를 그대로 쓴다. 즉 여기서
통과한 값은 inspect 에서도 '문제'로 잡히지 않는다.

  ujyu config show                    그룹별 현재 값 (채워진 것 + 중요한 미설정)
  ujyu config show --all              미설정까지 전부 + 목록/표 전개
  ujyu config get SCALE               값만 출력 (스크립트용)
  ujyu config set SCALE 2             검사 후 config.py 의 그 줄만 교체

`set` 은 파이썬으로 재직렬화하지 않는다. `^KEY =` 줄의 우변만 바꾸고 줄 끝 주석과
나머지 줄(주석·공백·구조)은 그대로 둔다. 쓰기 전에 config.py.bak 을 남기고, 쓴 뒤
다시 import 해 로드되는지 확인한다(실패하면 백업으로 되돌린다).
"""
import argparse
import ast
import difflib
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ujyu.doctor import (BAD, OK, TODO, Doctor, Row, STAGES, _empty, _fmt,
                         _pad, _safe, _short, _shortp, extra_rows, load_config)

# 콘솔이 CP949 다. 표현 못 하는 문자로 죽지 않게 한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


# ─────────────────────────────────────────── 그룹·종류 표
# (그룹, 제목, [(키, 종류, 중요)])
#   종류는 doctor.py 의 검사 종류와 같다. 추가로:
#     int      정수      literal  파이썬 리터럴(목록/집합/표)
# 중요=1 이면 미설정이어도 show 기본 출력에 나온다.
GROUPS = [
    ("path", "경로", (
        ("ORIG_DIR", "dir", 1),
        ("GAME_DIR", "dir", 1),
        ("WORK_DIR", "dir", 1),
    )),
    ("archive", "아카이브", (
        ("ARCHIVES", "archives", 1),
        ("BASE_ARCHIVE", "val", 1),
        ("OUT_ARCHIVE", "val", 1),
        ("CG_ARCHIVE", "origname", 1),
        ("PASSTHROUGH_ARCHIVES", "literal", 0),
    )),
    ("text", "텍스트/번역", (
        ("STRINGS", "file", 1),
        ("UI_STRINGS", "file", 0),
        ("NAMEPLATES", "file", 0),
        ("NAMEPLATES_MD", "out", 0),
        ("RESOURCE_RE", "regex", 1),
        ("MARKERS", "literal", 0),
        ("CMD_SEQS", "literal", 0),
        ("QUOTE_LEAD_SPACE", "bool", 0),
    )),
    ("csv", "common.csv", (
        ("ORIG_W", "int", 1),
        ("ORIG_H", "int", 1),
        ("COMMON_CSV", "literal", 1),
    )),
    ("exe", "exe 패치", (
        ("EXE_IN", "file", 1),
        ("EXE_OUT", "out", 1),
        ("IMAGE_BASE", "int", 1),
        ("OFF_LEAD_BITMAP", "off", 1),
        ("OFF_CHARSET_BODY", "off", 1),
        ("OFF_CHARSET_ENUM", "off", 1),
        ("SJIS_IDIOM", "idiom", 1),
        ("INLINE_RECODE", "idiom", 0),
        ("OFF_FILTER_PITCH", "offs", 0),
        ("OFF_FILTER_PATTERN", "off", 0),
        ("OFF_FILTER_PUSH", "off", 0),
        ("OFF_FILTER_JCC", "off", 0),
        ("OFF_FONT_GOTHIC", "off", 0),
        ("OFF_FONT_MINCHO", "off", 0),
        ("OFF_FONT_FALLBACK", "off", 0),
        ("CAVE_VA", "va", 0),
        ("BUF_VA", "va", 0),
        ("NBYTES_VA", "va", 0),
        ("IAT_CreateFileA", "va", 0),
        ("IAT_ReadFile", "va", 0),
        ("IAT_CloseHandle", "va", 0),
        ("SAVE_REL_PATH", "val", 0),
        ("SAVE_NAME_OFF", "int", 0),
    )),
    ("font", "폰트", (
        ("FONT_WIDTH_MODE", "enum", 1),
        ("FONT_FACE", "val", 1),
        ("FONT_FALLBACK", "val", 0),
        ("FILTER_PREFIX", "val", 1),
        ("DLGFONT", "val", 1),
    )),
    ("image", "이미지 텍스트", (
        ("IMAGE_ASSET_DIR", "dir", 0),
        ("IMAGE_SPEC", "file", 1),
        ("IMAGE_VARIANT", "val", 1),
        ("IMAGE_ORIGINAL_DIR", "dir", 0),
        ("IMAGE_TEXTLESS_DIR", "dir", 0),
        ("IMAGE_FONT_DIR", "dir", 0),
        ("IMAGE_TEXTED_PREFIX", "val", 0),
        ("IMAGE_RENDERER", "file", 0),
        ("CG_ORIG_DIR", "dir", 0),
        ("CG_NOTEXT_DIR", "dir", 0),
        ("CG_TRANS_DIR", "dir", 0),
    )),
    ("scale", "해상도", (
        ("SCALE", "int", 1),
        ("OFF_SCREEN_W", "offs", 0),
        ("OFF_SCREEN_H", "offs", 0),
        ("SCN_DIMS", "literal", 0),
        ("SCN_DIMS_AUTO", "literal", 0),
        ("SCALE_DIALOG_1X", "literal", 0),
        ("SCALE_FS_WINDOWS", "literal", 0),
        ("COMMON_CENTER", "literal", 0),
        ("CG_UPSCALE_DIR", "dir", 0),
        ("CG_CONTENT_PREFIX", "literal", 0),
        ("CG_UI_1X_PREFIX", "literal", 0),
        ("CG_FORCE_1X", "literal", 0),
    )),
    ("movie", "무비", (
        ("MOVIE_NATIVE", "bool", 0),
        ("OFF_MOVIE_SCALE", "off", 0),
        ("MOVIE_ARCHIVE", "origname", 0),
        ("MOVIE_SRC_DIR", "dir", 0),
    )),
    ("build", "빌드·배포", (
        ("KR_BASE_DIR", "dir", 0),
        ("KR_EXE", "file", 0),
        ("RELEASE_EXCLUDE", "literal", 0),
    )),
]

OTHER = "other"        # 위 표에 없는 키를 담는 그룹
GROUP_NAMES = [g[0] for g in GROUPS] + [OTHER]

# 키 → (종류, 중요, 그룹) / 그룹 → 스펙
KIND = {}
GROUP_SPECS = {}
for _g, _t, _specs in GROUPS:
    GROUP_SPECS[_g] = _specs
    for _k, _kind, _imp in _specs:
        KIND[_k] = (_kind, _imp, _g)

# doctor.py 의 단계 정의에서 힌트·열거 허용값·단계를 가져온다 (중복 정의 금지).
DOC = {}
for _st, _title, _specs in STAGES:
    for _s in _specs:
        _extra = _s[3] if len(_s) > 3 else ()
        DOC[_s[0]] = {"kind": _s[1], "hint": _s[2], "stage": _st,
                      "allowed": _extra if _s[1] == "enum" else ()}

CONTAINER = (list, tuple, set, frozenset, dict)


def key_kind(key, cur=None, text=None):
    """키의 검사 종류. 표 → 이름 규칙 → 현재 값/입력 텍스트 순으로 판정한다."""
    if key in KIND:
        return KIND[key][0]
    if key in DOC:
        return DOC[key]["kind"]
    if key.endswith("_DIR"):
        return "dir"
    if key.endswith("_VA") or key.startswith("IAT_"):
        return "va"
    if key.endswith("_RE"):
        return "regex"
    if isinstance(cur, bool):
        return "bool"
    if isinstance(cur, int):
        return "int"
    if isinstance(cur, CONTAINER):
        return "literal"
    if isinstance(cur, str):
        return "val"
    if text is not None:                      # 새 키(--add): 입력 모양으로 추정
        try:
            v = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return "val"
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int):
            return "int"
        if isinstance(v, CONTAINER):
            return "literal"
    return "val"


def key_group(key):
    if key in KIND:
        return KIND[key][2]
    return OTHER


def hint_of(key):
    return DOC.get(key, {}).get("hint", "")


def allowed_of(key):
    return DOC.get(key, {}).get("allowed", ())


# ─────────────────────────────────────────── 검사 (doctor 검증기 재사용)
def validate(D, key, kind, val):
    """현재/새 값 하나를 검사해 Row 를 돌려준다. doctor 의 _k_* 를 그대로 쓴다."""
    hint = D.hint(hint_of(key)) if hint_of(key) else ""

    if kind == "bool":
        return Row(OK, key, "켜짐(True)" if val else "꺼짐(False)")

    if _empty(val):
        return Row(TODO, key, "", hint)

    if kind == "int":
        if isinstance(val, bool) or not isinstance(val, int):
            return Row(BAD, key, "정수가 아니다: %s" % _short(repr(val)), hint)
        if val < 0:
            return Row(BAD, key, "음수는 쓸 수 없다: %d" % val, hint)
        if key == "SCALE" and val < 1:
            return Row(BAD, key, "배율은 1 이상이어야 한다 (1=원본 해상도)", hint)
        return Row(OK, key, _fmt(val))

    if kind == "literal":
        return Row(OK, key, _fmt(val))

    fn = getattr(D, "_k_" + kind, None)
    if fn is None:
        return Row(OK, key, _fmt(val))
    return fn(key, val, hint, allowed_of(key))


def cross_warnings(C, key, new):
    """키를 새 값으로 바꿨을 때 doctor 의 교차 검사가 무엇을 잡는지 (경고용)."""
    stage = DOC.get(key, {}).get("stage")
    if stage is None:
        return []
    had = hasattr(C, key)
    old = getattr(C, key, None)
    setattr(C, key, new)                      # 메모리 안에서만 바꿔 본다
    try:
        rows = [r for r in extra_rows(stage, Doctor(C)) if r.status == BAD]
    finally:
        if had:
            setattr(C, key, old)
        else:
            delattr(C, key)
    return rows


# ─────────────────────────────────────────── 값 파싱·직렬화
def parse_value(key, kind, text, cur):
    """CLI 문자열을 그 종류의 파이썬 값으로. 실패하면 (None, 사유)."""
    if "\n" in text or "\r" in text:
        return None, "여러 줄 값은 받지 않는다 (config.py 를 직접 편집할 것)"

    if kind in ("off", "va", "int"):
        try:
            return int(text, 0), None
        except ValueError:
            return None, ("정수가 아니다: %s (10진 또는 0x16진으로 적을 것)"
                          % _short(text))

    if kind == "bool":
        t = text.strip().lower()
        if t in ("1", "true", "yes", "on", "y"):
            return True, None
        if t in ("0", "false", "no", "off", "n"):
            return False, None
        return None, "참/거짓이 아니다: %s (true 또는 false)" % _short(text)

    if kind in ("offs", "idiom", "literal", "archives"):
        try:
            v = ast.literal_eval(text)
        except (ValueError, SyntaxError) as e:
            return None, ("파이썬 리터럴로 읽을 수 없다: %s\n"
                          "  목록/표는 그대로 적는다. 예: \"[0x1234, 0x5678]\" / "
                          "\"{'select': 320}\"\n  (%s)" % (_short(text), _safe(e)))
        if kind in ("offs", "archives"):
            if not isinstance(v, (list, tuple)):
                return None, "목록이어야 한다: %s" % _short(text)
            v = list(v)                        # 표기가 어떻든 목록으로 쓴다
        if kind == "offs" and not all(isinstance(x, int) and not isinstance(x, bool)
                                      for x in v):
            return None, "오프셋 목록의 항목은 전부 정수여야 한다: %s" % _short(text)
        if kind in ("literal", "idiom") and text.strip()[:1] not in "([{":
            return None, ("목록·표는 괄호로 감싸 적을 것: %s\n"
                          "  예: \"['a', 'b']\" / \"('a',)\" / \"{'select': 320}\""
                          % _short(text))
        if isinstance(cur, CONTAINER) and not isinstance(v, type(cur)) \
                and not (isinstance(cur, (list, tuple)) and isinstance(v, (list, tuple))):
            return None, ("현재 값은 %s 인데 %s 를 주었다 (형태를 맞출 것)"
                          % (type(cur).__name__, type(v).__name__))
        return v, None

    # 문자열 계열 (dir/file/out/val/enum/regex/origname)
    s = text
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    if not s:
        return None, "빈 문자열은 받지 않는다 (지우려면 config.py 를 직접 편집)"
    if kind == "regex":
        import re as _re
        try:
            _re.compile(s)
        except _re.error as e:
            return None, "정규식이 컴파일되지 않는다: %s" % _safe(e)
    return s, None


def _pystr(s):
    """파이썬 문자열 리터럴로. 역슬래시가 있으면 raw 문자열로 읽기 쉽게."""
    if "\\" in s and '"' not in s and not s.endswith("\\"):
        return 'r"%s"' % s
    return repr(s)


def to_source(kind, val, text):
    """config.py 에 쓸 우변 소스. 리터럴 계열은 사용자가 적은 표기를 살린다."""
    if kind in ("off", "va"):
        return "0x%X" % val
    if kind == "int":
        return "0x%X" % val if text.strip().lower().startswith("0x") else "%d" % val
    if kind == "bool":
        return "True" if val else "False"
    if kind == "offs":
        return "[%s]" % ", ".join("0x%X" % x for x in val)
    if kind == "archives":
        return "[%s]" % ", ".join(_pystr(x) if isinstance(x, str) else repr(x)
                                 for x in val)
    if kind in ("idiom", "literal"):
        return text.strip()
    return _pystr(val)


# ─────────────────────────────────────────── config.py 줄 단위 편집
def split_comment(line):
    """따옴표 밖의 첫 '#' 에서 (코드, 주석) 으로 나눈다."""
    q, i = None, 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if line.startswith(q, i):
                i += len(q)
                q = None
                continue
            i += 1
            continue
        if c == "#":
            return line[:i], line[i:]
        for m in ('"""', "'''", '"', "'"):
            if line.startswith(m, i):
                q = m
                i += len(m)
                break
        else:
            i += 1
    return line, ""


def depth_after(code, depth=0):
    """괄호 깊이 (따옴표 안은 무시)."""
    q, i = None, 0
    while i < len(code):
        c = code[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if code.startswith(q, i):
                i += len(q)
                q = None
                continue
            i += 1
            continue
        matched = False
        for m in ('"""', "'''", '"', "'"):
            if code.startswith(m, i):
                q = m
                i += len(m)
                matched = True
                break
        if matched:
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        i += 1
    return depth


_EOL_RE = re.compile(r"(\r\n|\r|\n)$")


def _eol(line):
    m = _EOL_RE.search(line)
    return m.group(1) if m else ""


def _lhs_targets(code):
    """`a = ...` / `a, b = ...` 의 좌변 이름들. 대입문이 아니면 None."""
    i, q = 0, None
    while i < len(code):
        c = code[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if code.startswith(q, i):
                q = None
            i += 1
            continue
        if c in "\"'":
            q = c
            i += 1
            continue
        if c in "([{":
            return None                        # 호출·첨자 = 대입문 아님
        if c == "=":
            if i + 1 < len(code) and code[i + 1] == "=":
                return None
            if i and code[i - 1] in "=!<>+-*/%&|^":
                return None                    # 비교·복합대입
            lhs = code[:i]
            names = [t.strip() for t in lhs.split(",")]
            if not names or not all(re.match(r"^[A-Za-z_]\w*$", n) for n in names):
                return None
            return names, i
        i += 1
    return None


def find_assign(lines, key):
    """config.py 에서 KEY 대입을 찾는다. (kind, 정보) 를 돌려준다.

    kind: "ok"(단독·한 줄) / "multiline" / "multitarget" / "indented" / None
    """
    for i, raw in enumerate(lines):
        line = raw[:len(raw) - len(_eol(raw))]
        code, _cmt = split_comment(line)
        stripped = code.strip()
        if not stripped:
            continue
        t = _lhs_targets(code)
        if not t:
            continue
        names, eq = t
        if key not in names:
            continue
        indent = len(code) - len(code.lstrip())
        if indent:
            return "indented", {"line": i}
        if len(names) > 1:
            return "multitarget", {"line": i, "names": names}
        # 여러 줄로 이어지는 값인지
        n, d = i, depth_after(code)
        while d > 0 and n + 1 < len(lines):
            n += 1
            nxt = lines[n]
            d = depth_after(split_comment(nxt[:len(nxt) - len(_eol(nxt))])[0], d)
        if n != i:
            return "multiline", {"line": i, "end": n}
        return "ok", {"line": i, "eq": eq}
    return None, {}


def replace_rhs(raw, eq, new_rhs):
    """대입 줄의 우변만 바꾼다. `=` 뒤 공백·줄끝 주석·줄바꿈은 그대로."""
    eol = _eol(raw)
    line = raw[:len(raw) - len(eol)]
    code, cmt = split_comment(line)
    head = code[:eq + 1]                       # "KEY    ="
    rest = code[eq + 1:]
    lead = rest[:len(rest) - len(rest.lstrip())] or " "
    tail_ws = rest[len(rest.rstrip()):] if cmt else ""
    return head + lead + new_rhs + tail_ws + cmt + eol


def insert_line(lines, key, new_rhs, group):
    """--add: 같은 그룹 마지막 키 뒤에 새 줄을 넣는다. 없으면 파일 끝."""
    last = None
    for k, _kind, _imp in GROUP_SPECS.get(group, ()):
        st, info = find_assign(lines, k)
        if st in ("ok", "multiline", "multitarget"):
            last = max(last if last is not None else 0, info.get("end", info["line"]))
    text = "%s = %s\n" % (key, new_rhs)
    if last is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(text)
        return len(lines) - 1
    lines.insert(last + 1, text)
    return last + 1


# ─────────────────────────────────────────── 쓰기 + 되돌리기
def purge_pycache(cfg_dir):
    """config 의 캐시된 바이트코드를 지운다.

    파이썬은 (mtime초, 크기) 로만 무효화를 판단한다. 같은 초에 같은 크기로 고치면
    (예: 320 -> 400) 낡은 .pyc 가 그대로 쓰여 도구들이 옛 값을 읽는다.
    """
    d = os.path.join(cfg_dir, "__pycache__")
    if not os.path.isdir(d):
        return
    for n in os.listdir(d):
        if n.startswith("config."):
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass


def reload_check(cfg_dir, key):
    """config.py 를 새 프로세스에서 import 해 값을 읽어 온다. (ok, 값repr, 메시지)"""
    code = ("import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import config\n"
            "print(repr(getattr(config, sys.argv[2], '<없음>')))\n")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run([sys.executable, "-c", code, cfg_dir, key],
                           capture_output=True, env=env, cwd=cfg_dir)
    except OSError as e:
        return True, None, "재로드 확인을 건너뛴다: %s" % _safe(e)
    out = p.stdout.decode("utf-8", "replace").strip()
    err = p.stderr.decode("utf-8", "replace").strip()
    if p.returncode != 0:
        return False, None, err
    return True, out, ""


def write_config(path, lines, key, expect_repr, verbose=True):
    """백업 → 쓰기 → 재로드 확인 → 실패면 되돌리기. 성공 여부를 돌려준다."""
    bak = path + ".bak"
    shutil.copy2(path, bak)
    if verbose:
        print("  백업 : %s" % _shortp(bak))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(lines))
    purge_pycache(os.path.dirname(path))

    ok, got, msg = reload_check(os.path.dirname(path), key)
    if not ok:
        shutil.copy2(bak, path)
        purge_pycache(os.path.dirname(path))
        print("config.py 가 로드되지 않아 백업으로 되돌렸습니다.")
        for ln in (msg or "").splitlines()[-6:]:
            print("  %s" % _safe(ln))
        return False
    if got is not None and expect_repr is not None and got != expect_repr:
        print("  경고: 로드된 값이 적은 값과 다릅니다 (환경변수·파생 계산 때문일 수 있음)")
        print("        적음 : %s" % _short(expect_repr))
        print("        로드 : %s" % _short(got))
    elif verbose:
        print("  확인 : 다시 import 해 %s = %s 로 로드됨" % (key, _short(got or "?")))
    return True


# ─────────────────────────────────────────── show
def _iter_keys(C):
    """config 모듈의 설정 키 (대문자로 시작하는 값. 함수·모듈 제외)."""
    import types
    out = []
    for n in dir(C):
        if not re.match(r"^[A-Z][A-Za-z0-9_]*$", n):
            continue
        v = getattr(C, n)
        if isinstance(v, types.ModuleType) or callable(v):
            continue
        out.append(n)
    return out


def _item(x):
    """목록/표의 항목 한 줄. 정수는 오프셋일 확률이 높아 16진으로."""
    if isinstance(x, int) and not isinstance(x, bool):
        return _fmt(x)
    return _short(repr(x), 90)


def _expand(val):
    """--all 에서 목록/표를 한 항목씩."""
    if isinstance(val, dict):
        return ["%s: %s" % (_safe(repr(k)), _short(repr(v), 70)) for k, v in val.items()]
    if isinstance(val, (list, tuple, set, frozenset)):
        return [_item(x) for x in val]
    return []


def cmd_show(args):
    C = load_config()
    D = Doctor(C)
    known = set(KIND)
    extras = sorted(k for k in _iter_keys(C) if k not in known)

    groups = [(g, t, list(s)) for g, t, s in GROUPS]
    if extras:
        groups.append((OTHER, "기타 (표에 없는 키)",
                       [(k, key_kind(k, getattr(C, k, None)), 0) for k in extras]))
    if args.group:
        groups = [g for g in groups if g[0] == args.group]
        if not groups:
            raise SystemExit("그룹 '%s' 에 해당하는 키가 없습니다." % args.group)

    print("ujyu config - 현재 설정 (%s)" % ("전부" if args.all else "채워진 것 + 중요 항목"))
    print("config : %s" % _short(getattr(C, "__file__", "?"), 120))
    print()

    n_ok = n_todo = n_bad = n_hidden = 0
    for gname, title, specs in groups:
        shown = []
        for key, kind, imp in specs:
            val = getattr(C, key, None)
            missing = not hasattr(C, key)
            r = Row(TODO, key, "", hint_of(key)) if missing \
                else validate(D, key, kind, val)
            if r.status == OK:
                n_ok += 1
            elif r.status == BAD:
                n_bad += 1
            else:
                n_todo += 1
            if not args.all and r.status == TODO and not imp:
                n_hidden += 1
                continue
            shown.append((key, kind, val, missing, r))
        if not shown:
            continue
        print("[%s] %s" % (gname, title))
        for key, kind, val, missing, r in shown:
            line = "  %s %s" % (_pad(r.status, 6), _pad(key, 21))
            if missing:
                line += "(config.py 에 없음)"
            elif _empty(val) and not isinstance(val, bool):
                line += "(미설정)"
            elif r.status == BAD:
                line += r.detail                     # 사유에 값이 들어 있다
            elif kind == "bool":
                line += "= " + r.detail
            elif kind in ("dir", "file", "out", "origname") and isinstance(val, str):
                line += "= " + _shortp(val, 84)      # 경로는 뒤쪽(파일명)을 살린다
            else:
                line += "= " + _fmt(val)
            print(line.rstrip())
            if args.all and isinstance(val, CONTAINER) and not _empty(val):
                for item in _expand(val):
                    print("%s%s" % (" " * 32, item))
        print()

    print("합계: OK %d / 미설정 %d / 문제 %d" % (n_ok, n_todo, n_bad))
    if n_hidden:
        print("  미설정 %d개 생략 - 전부 보려면 --all" % n_hidden)
    if n_bad:
        print("  '문제' 항목은 ujyu inspect 로 사유를 자세히 볼 수 있습니다.")
    print("값 변경: ujyu config set <KEY> <VALUE>")
    return 0


# ─────────────────────────────────────────── get
def cmd_get(args):
    C = load_config()
    key = args.key
    if not hasattr(C, key):
        near = difflib.get_close_matches(key, _iter_keys(C), 3, 0.6)
        raise SystemExit("config.py 에 없는 키: %s%s"
                         % (key, ("\n  비슷한 키: " + ", ".join(near)) if near else ""))
    val = getattr(C, key)
    print(val if isinstance(val, str) else repr(val))
    return 0


# ─────────────────────────────────────────── set
def cmd_set(args):
    C = load_config()
    key, text = args.key, args.value
    path = os.path.abspath(getattr(C, "__file__", "") or "")
    if not path or not os.path.isfile(path):
        raise SystemExit("config.py 의 경로를 알 수 없습니다. 타이틀 리포 루트에서 "
                         "실행하거나 MIRIS_CONFIG_DIR 를 지정하세요.")
    if path.endswith(".pyc"):
        path = path[:-1]

    with open(path, "r", encoding="utf-8", newline="") as f:
        lines = f.read().splitlines(keepends=True)

    st, info = find_assign(lines, key)
    exists = hasattr(C, key)

    if st is None and not args.add:
        near = difflib.get_close_matches(key, sorted(set(_iter_keys(C)) | set(KIND)), 3, 0.6)
        msg = "config.py 에 없는 키: %s" % key
        if exists:
            msg += ("\n  모듈에는 있지만 최상위 대입이 아닙니다 (파생값이거나 조건 블록 안)."
                    "\n  config.py 를 직접 편집하세요.")
        if near:
            msg += "\n  비슷한 키: " + ", ".join(near)
        msg += "\n  새 키를 정말 추가하려면 --add 를 주세요."
        raise SystemExit(msg)
    if st == "indented":
        raise SystemExit("%s 는 조건 블록 안(%d번째 줄, 들여쓰기)에서 정의됩니다.\n"
                         "  구조를 깨지 않으려면 config.py 를 직접 편집하세요."
                         % (key, info["line"] + 1))
    if st == "multitarget":
        raise SystemExit("%s 는 %d번째 줄에서 %s 와 함께 대입됩니다.\n"
                         "  한 줄 다중 대입은 자동 편집하지 않습니다. 직접 편집하세요."
                         % (key, info["line"] + 1, ", ".join(
                             n for n in info["names"] if n != key)))
    if st == "multiline":
        raise SystemExit("%s 의 값이 %d~%d번째 줄에 걸쳐 있습니다 (줄 안의 주석이 지워질 "
                         "수 있어 건드리지 않습니다).\n  config.py 를 직접 편집하세요."
                         % (key, info["line"] + 1, info["end"] + 1))

    cur = getattr(C, key, None)
    kind = key_kind(key, cur, text)
    new, why = parse_value(key, kind, text, cur)
    if why:
        print("값이 올바르지 않습니다 (%s, 종류=%s):" % (key, kind))
        for ln in why.splitlines():
            print("  %s" % ln)
        if hint_of(key):
            print("  참고: %s" % _short(Doctor(C).hint(hint_of(key)), 150))
        return 1

    D = Doctor(C)
    r = validate(D, key, kind, new)
    if r.status == BAD:
        print("값이 올바르지 않습니다 (%s, 종류=%s):" % (key, kind))
        print("  %s" % r.detail)
        if r.hint:
            print("  참고: %s" % _short(r.hint, 150))
        print("아무것도 쓰지 않았습니다.")
        return 1

    warns = cross_warnings(C, key, new)
    new_rhs = to_source(kind, new, text)

    # 무엇을 어떻게 바꾸는지
    print("config : %s" % _shortp(path, 100))
    print("키     : %s (종류=%s, 그룹=%s)" % (key, kind, key_group(key)))
    print("현재   : %s" % (_fmt(cur) if exists else "(없음)"))
    print("변경   : %s" % _short(new_rhs, 100))
    if st is None:
        print("추가   : --add (%s 그룹 끝에 새 줄)" % (args.group or key_group(key)))
    else:
        old_line = lines[info["line"]]
        print("  - %4d: %s" % (info["line"] + 1, _short(old_line.rstrip("\r\n"), 110)))
        old_rhs = split_comment(old_line.rstrip("\r\n"))[0][info["eq"] + 1:].strip()
        try:
            ast.literal_eval(old_rhs)
        except (ValueError, SyntaxError):       # os.environ.get(...) 등 계산식
            warns.insert(0, Row(BAD, "기존 정의",
                                "우변이 표현식이다: %s" % _short(old_rhs, 70),
                                "리터럴로 덮어쓰면 환경변수·파생 계산이 사라진다"))

    if st is None:
        idx = insert_line(lines, key, new_rhs, args.group or key_group(key))
    else:
        idx = info["line"]
        lines[idx] = replace_rhs(lines[idx], info["eq"], new_rhs)
    print("  + %4d: %s" % (idx + 1, _short(lines[idx].rstrip("\r\n"), 110)))

    for w in warns:
        print("  경고: %s - %s" % (w.label, w.detail))
        if w.hint:
            print("        %s" % _short(w.hint, 130))

    if args.dry_run:
        print("--dry-run 이라 쓰지 않았습니다.")
        return 0

    if not write_config(path, lines, key, repr(new)):
        return 1
    print("완료. 상태 점검은 ujyu inspect.")
    return 0


# ─────────────────────────────────────────── main
def main():
    ap = argparse.ArgumentParser(
        prog="ujyu config",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="타이틀 config.py 값 조회/변경 (검사 규칙은 ujyu inspect 와 동일)",
        epilog="예시:\n"
               "  ujyu config show                     그룹별 현재 값\n"
               "  ujyu config show --group exe --all   exe 그룹 전부 (목록 전개)\n"
               "  ujyu config get SCALE                값만 출력 (스크립트용)\n"
               "  ujyu config set SCALE 2              검사 후 그 줄만 교체\n"
               "  ujyu config set OFF_MOVIE_SCALE 0x58EE8 --dry-run\n"
               "\nset 은 config.py 를 재직렬화하지 않는다. `KEY =` 줄의 우변만 바꾸고 줄 끝\n"
               "주석과 나머지 줄은 그대로 둔다. 쓰기 전 config.py.bak 을 만들고, 쓴 뒤 다시\n"
               "import 해 로드되는지 확인한다(실패하면 백업으로 되돌린다).\n"
               "종료 코드: 값이 올바르지 않거나 쓰기에 실패하면 1")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("show", help="현재 값을 그룹별로 출력",
                       description="현재 값을 그룹별로 출력 (기본: 채워진 것 + 중요한 미설정)")
    p.add_argument("--group", metavar="G", choices=GROUP_NAMES,
                   help="이 그룹만 (%s)" % " ".join(GROUP_NAMES))
    p.add_argument("--all", action="store_true",
                   help="미설정 키까지 전부 + 목록/표를 한 항목씩 전개")

    p = sub.add_parser("get", help="값 하나를 그대로 출력 (스크립트용)",
                       description="값 하나를 그대로 출력한다 (문자열은 날값, 그 외 repr)")
    p.add_argument("key", metavar="KEY", help="config 키 이름 (예: SCALE)")

    p = sub.add_parser("set", help="값을 검사한 뒤 config.py 의 그 줄을 교체",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="값을 검사한 뒤 config.py 의 해당 줄 우변만 교체한다",
                       epilog="검사: 경로(존재)·출력경로(상위 폴더)·정수·오프셋(EXE_IN 크기 "
                              "범위)·VA(IMAGE_BASE 이상)\n"
                              "      열거값·정규식 컴파일·리스트/딕트 리터럴 파싱")
    p.add_argument("key", metavar="KEY", help="config 키 이름 (config.py 에 있는 것)")
    p.add_argument("value", metavar="VALUE",
                   help="새 값. 정수는 10진/0x16진, 목록·표는 파이썬 리터럴 그대로")
    p.add_argument("--add", action="store_true",
                   help="config.py 에 없는 키를 그룹 끝에 새로 추가 (기본은 거부)")
    p.add_argument("--group", metavar="G", choices=GROUP_NAMES,
                   help="--add 로 넣을 그룹 (기본: 키가 속한 그룹, 모르면 파일 끝)")
    p.add_argument("--dry-run", action="store_true",
                   help="쓰지 않고 무엇을 어떻게 바꿀지만 보여준다")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 0
    if args.cmd == "show":
        return cmd_show(args)
    if args.cmd == "get":
        return cmd_get(args)
    return cmd_set(args)


if __name__ == "__main__":
    raise SystemExit(main())
