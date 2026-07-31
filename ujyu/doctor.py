#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config 채움 상태 점검 - 지금 무엇이 채워졌고 다음에 뭘 해야 하는지 보고한다.

docs/BOOTSTRAP.md 의 단계(0 셋업 -> 1 정찰 -> 2 common.csv -> 4 텍스트 추출 ->
6 exe -> 7 폰트 -> 8 번역 -> 9 이미지 -> 10 빌드 -> 11 해상도 -> 12 무비)에
config 키를 매핑해 단계별로 상태를 낸다.

  OK    값이 있고 (경로면) 실제로 존재한다
  미설정 아직 그 단계에 도달하지 않았을 수 있다 - 에러가 아니다
  문제  값이 있는데 잘못됐다 (경로 없음, 이름 불일치, 범위 밖 등)

사용
----
  ujyu inspect                전체 단계 요약
  ujyu inspect --verbose      채워진 항목의 실제 값까지
  ujyu inspect --stage 6      한 단계만

값을 보거나 고치는 것은 `ujyu config` (show / get / set).
"""
import argparse
import json
import os
import sys
import unicodedata

# 콘솔이 CP949 다. 표현 못 하는 문자(원문 마커 등)로 죽지 않게 한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

OK, TODO, BAD = "OK", "미설정", "문제"

TARGET_KINDS = ("dlg", "narr", "quote", "cstr")   # 번역 대상 kind (strings.json v2)


# ─────────────────────────────────────────── 표시 도우미
def _w(s):
    """콘솔 표시 폭 (전각=2)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s, n):
    return s + " " * max(0, n - _w(s))


def _safe(s):
    """콘솔 인코딩에 없는 문자를 물음표로 바꿔 준다."""
    enc = getattr(sys.stdout, "encoding", None) or "cp949"
    try:
        return str(s).encode(enc, "replace").decode(enc, "replace")
    except Exception:
        return str(s)


def _short(s, n=88):
    s = _safe(s)
    return s if len(s) <= n else s[:n - 3] + "..."


def _shortp(p, n=80):
    """경로는 뒤쪽(파일명)이 중요하니 앞을 자른다."""
    p = _safe(p)
    return p if len(p) <= n else "..." + p[-(n - 3):]


def _fmt(v):
    """값을 한 줄로."""
    if isinstance(v, int) and not isinstance(v, bool):
        return "0x%X (%d)" % (v, v) if v >= 0x1000 else str(v)
    if isinstance(v, (list, tuple, set, frozenset)):
        items = list(v)
        if items and all(isinstance(x, int) for x in items):
            return "[%s]" % ", ".join("0x%X" % x for x in items)
        return _short(repr(items))
    if isinstance(v, dict):
        return _short(repr(v))
    return _short(str(v))


def _empty(v):
    if v is None:
        return True
    if isinstance(v, (str, bytes, list, tuple, dict, set, frozenset)) and len(v) == 0:
        return True
    return False


class Row(object):
    __slots__ = ("status", "label", "detail", "hint")

    def __init__(self, status, label, detail="", hint=""):
        self.status = status
        self.label = label
        self.detail = detail
        self.hint = hint


# ─────────────────────────────────────────── 단계 정의
# (키, 종류, 힌트). 힌트의 <ORIG_DIR>/<GAME_DIR> 은 실제 값으로 치환된다.
#
# 종류:
#   dir      존재하는 폴더        file    존재하는 파일
#   out      출력 경로(상위 폴더만 있으면 됨)
#   archives ORIG_DIR 안에 있어야 하는 아카이브 목록
#   origname ORIG_DIR 안에 있어야 하는 파일 이름 하나
#   off      exe 파일 오프셋      offs   오프셋 목록
#   idiom    (오프셋, 교체바이트hex) 쌍 목록
#   va       가상 주소(IMAGE_BASE 이상)
#   regex    정규식 문자열        enum   정해진 값 중 하나
#   bool     참/거짓 (거짓도 결정된 값)
#   val      존재만 확인(이름·숫자·목록·표)
STAGES = [
    ("0", "리포 셋업", [
        ("ORIG_DIR", "dir",
         "무패치 원본 게임 폴더를 config.py 의 ORIG_DIR 에 적을 것 (읽기 전용)"),
        ("GAME_DIR", "dir",
         "배포(패치본) 폴더를 GAME_DIR 에 적고 없으면 만들 것"),
        ("WORK_DIR", "dir",
         "작업 파일(임시 산출물) 폴더를 WORK_DIR 에 적고 없으면 만들 것"),
    ]),
    ("1", "정찰", [
        ("ARCHIVES", "archives",
         "ujyu axr list <ORIG_DIR>/scenario.axr 로 .scn 이 든 시나리오 아카이브를 확인해 "
         "우선순위 오름차순으로 나열"),
        ("BASE_ARCHIVE", "val",
         "주입 기준 아카이브 이름 (보통 ARCHIVES 의 첫 항목)"),
        ("OUT_ARCHIVE", "val",
         "주입 결과로 쓸 아카이브 이름"),
        ("CG_ARCHIVE", "origname",
         "이미지가 든 아카이브 이름. ujyu axr list <ORIG_DIR>/cg.axr 로 PNG 뭉치인지 확인"),
        ("EXE_IN", "file",
         "무패치 원본 실행 파일 경로 (<ORIG_DIR> 의 .exe)"),
        ("EXE_OUT", "out",
         "패치본 exe 를 쓸 경로 (<GAME_DIR> 아래)"),
    ]),
    ("2", "common.csv", [
        ("ORIG_W", "val",
         "ujyu csv show <ORIG_DIR>/scenario.axr 의 layer,bg,0,0,W,H 에서 원본 폭"),
        ("ORIG_H", "val",
         "ujyu csv show <ORIG_DIR>/scenario.axr 의 같은 줄에서 원본 높이"),
        ("COMMON_CSV", "val",
         "ujyu csv todo <ORIG_DIR>/scenario.axr 로 번역 대상 키를 뽑아 {키: 번역문} 으로"),
    ]),
    ("4", "텍스트 추출", [
        ("STRINGS", "file",
         "ujyu scn extract <ORIG_DIR>/scenario.axr ... -o translation/strings.json"),
        ("RESOURCE_RE", "regex",
         "리소스명을 화자로 오인하지 않게 하는 정규식. 심볼 목록을 보고 조정 (SKILL.md 4절)"),
        ("MARKERS", "val",
         "대사 문맥 없이 반복되는 마커 문자 집합 (SKILL.md 5절). 없으면 빈 set 그대로 둔다"),
        ("CMD_SEQS", "val",
         "줄바꿈 등 여러 글자 커맨드 시퀀스 목록 (SKILL.md 2절)"),
    ]),
    ("6", "exe 패치", [
        ("IMAGE_BASE", "val",
         "ujyu exe scan <EXE> 또는 ujyu exe disasm <EXE> info 의 ImageBase (보통 0x400000)"),
        ("OFF_LEAD_BITMAP", "off",
         "32B 리드바이트 비트맵. ujyu exe scan <EXE> 후보 확인 (SKILL.md 6-1)"),
        ("OFF_CHARSET_BODY", "off",
         "본문 폰트 charset 바이트(0x80). ujyu exe scan <EXE> (SKILL.md 6-2)"),
        ("OFF_CHARSET_ENUM", "off",
         "글꼴 열거 charset 바이트(0x80). ujyu exe scan <EXE> (SKILL.md 6-2)"),
        ("SJIS_IDIOM", "idiom",
         "SJIS 리드 판정 idiom 들. ujyu exe scan <EXE> 후보를 ujyu exe disasm at 으로 확인 (SKILL.md 6-3)",
         ("OFF_SJIS_IDIOM",)),
        ("OFF_FILTER_PITCH", "offs",
         "글꼴 목록 비례/FIXED_PITCH 필터. SKILL.md 8-1 참조 / ujyu exe disasm 으로 분석"),
        ("OFF_FILTER_PATTERN", "off",
         "글꼴 목록 필터 패턴 문자열 자리(8B). SKILL.md 8-1 참조 / ujyu exe disasm 으로 분석"),
        ("OFF_FILTER_PUSH", "off",
         "그 패턴을 push 하는 imm32. SKILL.md 8-1 참조 / ujyu exe disasm 으로 분석"),
        ("OFF_FILTER_JCC", "off",
         "jne(제외) -> je(포함) 로 바꿀 6B. SKILL.md 8-1 참조 / ujyu exe disasm 으로 분석"),
        ("OFF_FONT_GOTHIC", "off",
         "GOTHIC 기본 글꼴 슬롯(16B). ujyu exe scan <EXE> (SKILL.md 8-2)"),
        ("OFF_FONT_MINCHO", "off",
         "MINCHO 기본 글꼴 슬롯(16B) - 본문이 쓰는 쪽. ujyu exe scan <EXE> (SKILL.md 8-2)"),
        ("OFF_FONT_FALLBACK", "off",
         "case-C 폴백 face 슬롯(16B). ujyu exe scan <EXE> (SKILL.md 8-3)"),
        ("CAVE_VA", "va",
         ".text 끝 패딩(200B 이상). ujyu exe scan <EXE> (SKILL.md 9절)"),
        ("BUF_VA", "va",
         ".data 여유 구간(읽기 버퍼 64B). ujyu exe scan <EXE> (SKILL.md 9절)"),
        ("NBYTES_VA", "va",
         "읽은 바이트 수 저장 자리(4B). ujyu exe scan <EXE> (SKILL.md 9절)"),
        ("IAT_CreateFileA", "va",
         "IAT 의 CreateFileA 슬롯 VA. ujyu exe scan <EXE> (SKILL.md 9절)"),
        ("IAT_ReadFile", "va",
         "IAT 의 ReadFile 슬롯 VA. ujyu exe scan <EXE> (SKILL.md 9절)"),
        ("IAT_CloseHandle", "va",
         "IAT 의 CloseHandle 슬롯 VA. ujyu exe scan <EXE> (SKILL.md 9절)"),
        ("SAVE_REL_PATH", "val",
         "글꼴명이 든 세이브 파일의 게임 폴더 상대 경로 (docs/formats/SAVE.md)"),
        ("SAVE_NAME_OFF", "val",
         "그 파일에서 글꼴명이 시작하는 오프셋 (docs/formats/SAVE.md)"),
        ("UI_STRINGS", "file",
         "engine/samples/ui_strings.sample.json 을 복사해 번역 (ujyu exe ui 가 읽는다)"),
        ("DLGFONT", "val",
         "다이얼로그 리소스 글꼴 이름(예: 맑은 고딕). 없으면 ujyu exe ui 가 실패한다"),
    ]),
    ("7", "폰트", [
        ("FONT_WIDTH_MODE", "enum",
         "전각 고정이면 fullwidth, 가변폭이면 proportional - 사용자에게 물어 정할 것",
         ("fullwidth", "proportional")),
        ("FONT_FACE", "val",
         "게임에 넣을 폰트 face 이름. FILTER_PREFIX 로 시작해야 글꼴 목록에 뜬다"),
        ("FONT_FALLBACK", "val",
         "case-C 폴백 face 이름 (SKILL.md 8-3)"),
        ("FILTER_PREFIX", "val",
         "글꼴 목록에 노출할 face 접두어 (최대 7자, SKILL.md 8-1)"),
    ]),
    ("8", "번역", [
        ("NAMEPLATES", "file",
         "ujyu nameplates 로 화자를 집계한 뒤 JP->KR 대응표 json 을 채울 것"),
        ("NAMEPLATES_MD", "out",
         "ujyu nameplates 가 생성할 대응표 문서 경로"),
    ]),
    ("9", "이미지 텍스트", [
        ("IMAGE_SPEC", "file",
         "manifest 가 든 Markdown 경로. 기존 타이틀의 IMAGES.md 를 베껴 시작"),
        ("IMAGE_VARIANT", "val",
         "렌더에 쓸 폰트 변형 이름. ujyu image --list 로 manifest 의 목록 확인"),
        ("IMAGE_ORIGINAL_DIR", "dir",
         "ujyu axr unpack <ORIG_DIR>/cg.axr _cg_orig 로 원본 이미지를 풀고 그 폴더를 지정",
         ("CG_ORIG_DIR",)),
        ("IMAGE_TEXTLESS_DIR", "dir",
         "원본에서 글자를 지우고 배경을 복원한 베이스 폴더 (명령 없음, 손으로 만든다)",
         ("CG_NOTEXT_DIR",)),
        ("CG_TRANS_DIR", "dir",
         "ujyu image --variant <폰트변형> 렌더 출력 중 빌드에 쓸 폴더"),
    ]),
    ("10", "빌드", []),
    ("11", "해상도", [
        ("SCALE", "val",
         "정수 배율 1/2/3 - 사용자에게 물어 정할 것 (1 이면 확대 안 함)"),
        ("OFF_SCREEN_W", "offs",
         "exe 안 화면 폭(=ORIG_W) dword 파일오프셋들. ujyu exe scan <EXE> --width (RESOLUTION.md 15-2)"),
        ("OFF_SCREEN_H", "offs",
         "exe 안 화면 높이(=ORIG_H) dword 파일오프셋들. ujyu exe scan <EXE> --height"),
        ("SCN_DIMS", "val",
         "명시 치수 씬 {scn: [(오프셋, 폭, 원본값)]}. RESOLUTION.md 15-5"),
        ("SCN_DIMS_AUTO", "val",
         "좌표가 전부 VNEG int 심볼인 씬 이름들 (빌드 때 자동 도출)"),
        ("SCALE_DIALOG_1X", "val",
         "크기 1× 유지 + 우하단 시프트할 창 이름들. RESOLUTION.md 15-6"),
        ("SCALE_FS_WINDOWS", "val",
         "풀스크린 textwindow 이름들 (w/h·패딩 ×N)"),
        ("CG_CONTENT_PREFIX", "val",
         "×N 할 콘텐츠 이미지 프리픽스 (배경·캐릭터·CG)"),
        ("CG_UI_1X_PREFIX", "val",
         "1× 로 유지할 UI 이미지 프리픽스 (대사창·메뉴·얼굴)"),
        ("CG_FORCE_1X", "val",
         "1× 를 강제할 개별 파일명들. 없으면 빈 튜플 그대로 둔다"),
        ("COMMON_CENTER", "val",
         "스케일 비대상 창을 가운데로 옮길 {창이름: 기준높이}. ujyu scale center 가 쓴다"),
    ]),
    ("12", "무비", [
        ("MOVIE_NATIVE", "bool",
         "무비 2배 확대를 끌지 여부 - 사용자에게 물어 정할 것 (RESOLUTION.md 6-1)"),
        ("OFF_MOVIE_SCALE", "off",
         "무비 ctor 의 `push 2` 즉치 파일오프셋. ujyu exe scan <EXE> (RESOLUTION.md 6-1)"),
    ]),
]

STAGE_IDS = [s[0] for s in STAGES]


# ─────────────────────────────────────────── 개별 검사
class Doctor(object):
    def __init__(self, C):
        self.C = C
        self.orig = self._path("ORIG_DIR")
        self.game = self._path("GAME_DIR")
        self.cfg_dir = os.path.dirname(os.path.abspath(getattr(C, "__file__", ".") or "."))
        self.exe_size = None
        exe = getattr(C, "EXE_IN", None)
        if exe and os.path.isfile(exe):
            try:
                self.exe_size = os.path.getsize(exe)
            except OSError:
                pass

    def _path(self, name):
        v = getattr(self.C, name, None)
        return v if isinstance(v, str) and v else None

    # ── 힌트의 자리표시자를 실제 경로로
    def hint(self, text):
        if self.orig:
            text = text.replace("<ORIG_DIR>", self.orig)
        if self.game:
            text = text.replace("<GAME_DIR>", self.game)
        exe = getattr(self.C, "EXE_IN", None)
        text = text.replace("<EXE>", exe if exe and os.path.isfile(exe)
                            else "<ORIG_DIR>/game.exe".replace("<ORIG_DIR>", self.orig or "<ORIG_DIR>"))
        return text

    def check(self, spec):
        key, kind, hint = spec[0], spec[1], spec[2]
        extra = spec[3] if len(spec) > 3 else ()
        alts = extra if kind != "enum" else ()
        allowed = extra if kind == "enum" else ()
        val = getattr(self.C, key, None)

        if kind == "bool":
            if not hasattr(self.C, key):
                return Row(TODO, key, "", self.hint(hint))
            return Row(OK, key, "켜짐(True)" if val else "꺼짐(False)")

        if _empty(val):
            for a in alts:                       # 이름만 다르게 쓴 경우를 잡는다
                if not _empty(getattr(self.C, a, None)):
                    return Row(BAD, key,
                               "config 에 %s 로 되어 있다 (도구는 %s 를 읽는다)" % (a, key),
                               "config.py 에서 이름을 %s 로 바꿀 것" % key)
            return Row(TODO, key, "", self.hint(hint))

        fn = getattr(self, "_k_" + kind, None)
        if fn is None:
            return Row(OK, key, "")
        return fn(key, val, self.hint(hint), allowed)

    # 각 검사는 Row 를 돌려준다. detail 은 사실 한 줄.
    def _k_dir(self, key, val, hint, _a):
        if os.path.isdir(val):
            return Row(OK, key, "")
        if os.path.exists(val):
            return Row(BAD, key, "폴더가 아니다: %s" % _shortp(val), hint)
        return Row(BAD, key, "폴더 없음: %s" % _shortp(val),
                   "폴더를 만들거나 config.py 의 %s 경로를 고칠 것" % key)

    def _k_file(self, key, val, hint, _a):
        if os.path.isfile(val):
            return Row(OK, key, "")
        return Row(BAD, key, "파일 없음: %s" % _shortp(val), hint)

    def _k_out(self, key, val, hint, _a):
        if os.path.exists(val):
            return Row(OK, key, "")
        parent = os.path.dirname(os.path.abspath(val))
        if os.path.isdir(parent):
            return Row(OK, key, "아직 없음 (출력 예정)")
        return Row(BAD, key, "상위 폴더가 없다: %s" % _shortp(parent),
                   "폴더를 만들거나 config.py 의 %s 경로를 고칠 것" % key)

    def _k_archives(self, key, val, hint, _a):
        if not self.orig or not os.path.isdir(self.orig):
            return Row(OK, key, "%d개 (ORIG_DIR 이 없어 존재 확인 못 함)" % len(val))
        miss = [n for n in val if not os.path.isfile(os.path.join(self.orig, n))]
        if miss:
            return Row(BAD, key, "ORIG_DIR 에 없다: %s" % _short(", ".join(miss)),
                       "ORIG_DIR 의 실제 파일 이름으로 고칠 것 (우선순위 오름차순)")
        return Row(OK, key, "%d개 전부 확인" % len(val))

    def _k_origname(self, key, val, hint, _a):
        if not self.orig or not os.path.isdir(self.orig):
            return Row(OK, key, "(ORIG_DIR 이 없어 존재 확인 못 함)")
        if os.path.isfile(os.path.join(self.orig, val)):
            return Row(OK, key, "")
        return Row(BAD, key, "ORIG_DIR 에 없다: %s" % _shortp(val), hint)

    def _off_bad(self, off):
        """exe 범위를 벗어난 오프셋이면 설명, 아니면 None."""
        if self.exe_size is None or not isinstance(off, int):
            return None
        if off < 0 or off >= self.exe_size:
            return "exe 크기(0x%X) 밖: 0x%X" % (self.exe_size, off)
        return None

    def _k_off(self, key, val, hint, _a):
        bad = self._off_bad(val)
        if bad:
            return Row(BAD, key, bad, "EXE_IN 과 같은 빌드에서 다시 찾을 것. " + hint)
        return Row(OK, key, "0x%X" % val if isinstance(val, int) else _fmt(val))

    def _k_offs(self, key, val, hint, _a):
        bads = [b for b in (self._off_bad(o) for o in val) if b]
        if bads:
            return Row(BAD, key, "; ".join(bads[:2]),
                       "EXE_IN 과 같은 빌드에서 다시 찾을 것. " + hint)
        return Row(OK, key, "%d개" % len(val))

    def _k_idiom(self, key, val, hint, _a):
        try:
            pairs = [(o, h) for o, h in val]
        except (TypeError, ValueError):
            return Row(BAD, key, "(오프셋, 교체바이트hex) 쌍 목록이 아니다", hint)
        for _o, h in pairs:
            try:
                bytes.fromhex(h)
            except (ValueError, TypeError):
                return Row(BAD, key, "교체바이트가 hex 문자열이 아니다: %s" % _short(repr(h)),
                           hint)
        bads = [b for b in (self._off_bad(o) for o, _h in pairs) if b]
        if bads:
            return Row(BAD, key, "; ".join(bads[:2]),
                       "EXE_IN 과 같은 빌드에서 다시 찾을 것. " + hint)
        return Row(OK, key, "%d개" % len(pairs))

    def _k_va(self, key, val, hint, _a):
        base = getattr(self.C, "IMAGE_BASE", None)
        if isinstance(val, int) and isinstance(base, int) and val < base:
            return Row(BAD, key, "IMAGE_BASE(0x%X) 보다 작다: 0x%X" % (base, val),
                       "가상 주소(VA)로 적을 것. " + hint)
        return Row(OK, key, "0x%X" % val if isinstance(val, int) else _fmt(val))

    def _k_regex(self, key, val, hint, _a):
        import re
        try:
            re.compile(val)
        except re.error as e:
            return Row(BAD, key, "정규식 오류: %s" % _safe(e), hint)
        return Row(OK, key, "")

    def _k_enum(self, key, val, hint, allowed):
        if allowed and val not in allowed:
            return Row(BAD, key, "허용값이 아니다: %s (%s 중 하나)"
                       % (_short(str(val)), " / ".join(allowed)), hint)
        return Row(OK, key, _safe(val))

    def _k_val(self, key, val, hint, _a):
        return Row(OK, key, "")


# ─────────────────────────────────────────── 단계별 파생 검사
def extra_rows(stage, D):
    """키 하나로는 안 잡히는 교차 검사."""
    C = D.C
    rows = []

    if stage == "0":
        o, g = D.orig, D.game
        if o and g:
            if os.path.normcase(os.path.abspath(o)) == os.path.normcase(os.path.abspath(g)):
                rows.append(Row(BAD, "ORIG != GAME", "원본과 배포 폴더가 같다",
                                "빌드가 원본을 덮어쓴다. GAME_DIR 을 다른 폴더로 바꿀 것"))
            else:
                rows.append(Row(OK, "ORIG != GAME", "원본과 배포 폴더가 분리됨"))

    if stage == "7":
        face = getattr(C, "FONT_FACE", None)
        pre = getattr(C, "FILTER_PREFIX", None)
        if not _empty(face) and not _empty(pre):
            if not str(face).startswith(str(pre)):
                rows.append(Row(BAD, "FONT_FACE 접두",
                                "FONT_FACE 가 FILTER_PREFIX(%s) 로 시작하지 않는다" % _safe(pre),
                                "그 이름은 게임 글꼴 목록에 뜨지 않는다. 이름을 맞출 것"))
            else:
                rows.append(Row(OK, "FONT_FACE 접두", "FILTER_PREFIX 로 시작함"))
        if not _empty(pre) and len(str(pre)) > 7:
            rows.append(Row(BAD, "FILTER_PREFIX 길이",
                            "%d자 (최대 7자+널)" % len(str(pre)),
                            "접두어를 7자 이내로 줄일 것 (SKILL.md 8-1)"))
        for k in ("FONT_FACE", "FONT_FALLBACK"):
            v = getattr(C, k, None)
            if not _empty(v):
                try:
                    n = len(str(v).encode("cp949"))
                except UnicodeEncodeError:
                    n = 99
                if n >= 16:
                    rows.append(Row(BAD, k + " 길이", "%d바이트 (글꼴 슬롯은 16B)" % n,
                                    "이름을 15바이트 이내로 줄일 것"))
        if not _empty(face):
            spec = os.path.join(D.cfg_dir, "fonts", "%s.py" % face)
            if os.path.isfile(spec):
                rows.append(Row(OK, "폰트 스펙", "fonts/%s.py" % _safe(face)))
            else:
                rows.append(Row(TODO, "폰트 스펙", "fonts/%s.py 가 없다" % _safe(face),
                                "기존 타이틀 스펙을 베껴 만들고 "
                                "ujyu font fonts/<face>.py <원본>.ttf 실행 (SKILL.md 16절)"))

    if stage == "8":
        rows.extend(strings_rows(C))

    if stage == "10":
        need = []
        arcs = getattr(C, "ARCHIVES", None)
        if _empty(arcs) or not (D.orig and os.path.isdir(D.orig)
                                and all(os.path.isfile(os.path.join(D.orig, a)) for a in arcs)):
            need.append("1 정찰(ARCHIVES)")
        st = getattr(C, "STRINGS", None)
        if _empty(st) or not os.path.isfile(st):
            need.append("4 텍스트 추출(STRINGS)")
        ex = getattr(C, "EXE_IN", None)
        if _empty(ex) or not os.path.isfile(ex):
            need.append("6 exe(EXE_IN)")
        if need:
            rows.append(Row(TODO, "빌드 입력", "덜 채워짐: " + ", ".join(need),
                            "위 단계를 채운 뒤 ujyu build 로 조립 (exe -> scenario -> title -> cg)"))
        else:
            rows.append(Row(OK, "빌드 입력", "ujyu build 로 조립 가능"))

    if stage == "12":
        if getattr(C, "MOVIE_NATIVE", False) and _empty(getattr(C, "OFF_MOVIE_SCALE", None)):
            rows.append(Row(BAD, "MOVIE_NATIVE 짝",
                            "MOVIE_NATIVE 가 켜졌는데 OFF_MOVIE_SCALE 이 없다",
                            "ujyu exe disasm 으로 무비 ctor 의 `push 2` 즉치를 찾을 것"))
    return rows


# ─────────────────────────────────────────── strings.json 통계
def strings_rows(C):
    path = getattr(C, "STRINGS", None)
    if _empty(path) or not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            recs = json.load(f)
    except Exception as e:
        return [Row(BAD, "strings.json", "읽을 수 없다: %s" % _safe(e),
                    "ujyu scn extract 로 다시 만들 것")]
    if not isinstance(recs, list):
        return [Row(BAD, "strings.json", "목록이 아니다 (v2 포맷이 아님)",
                    "ujyu migrate 로 v2 로 이관할 것")]

    def done(r):
        return bool((r.get("kr") or "").strip())

    total = len(recs)
    kr = sum(1 for r in recs if done(r))
    tgt = [r for r in recs if r.get("kind") in TARGET_KINDS]
    tgt_kr = sum(1 for r in tgt if done(r))

    def pct(a, b):
        return (100.0 * a / b) if b else 0.0

    rows = [Row(OK, "번역 진행", "조각 %d개 중 %d개 번역 (%.1f%%)" % (total, kr, pct(kr, total)))]
    label = "/".join(TARGET_KINDS)
    if tgt:
        st = OK if tgt_kr == len(tgt) else TODO
        rows.append(Row(st, "번역 대상",
                        "%s %d개 중 %d개 (%.1f%%)"
                        % (label, len(tgt), tgt_kr, pct(tgt_kr, len(tgt))),
                        "" if tgt_kr == len(tgt) else
                        "ujyu filter dump 로 뽑아 번역하고 ujyu filter apply 로 반영"))
    return rows


# ─────────────────────────────────────────── 출력
def print_stage(stage, title, rows, verbose, C):
    n_ok = sum(1 for r in rows if r.status == OK)
    print("[%s] %s  (%d/%d)" % (stage, title, n_ok, len(rows)))
    if not rows:
        print("     config 키 없음")
    for r in rows:
        line = "  %s %s" % (_pad(r.status, 6), _pad(r.label, 20))
        detail = r.detail
        if verbose and hasattr(C, r.label) and not _empty(getattr(C, r.label, None)):
            v = _fmt(getattr(C, r.label))
            if not (detail and (v in detail or detail in v)):   # 이미 보여 준 값은 생략
                detail = "%s  = %s" % (detail, v) if detail else "= " + v
        if detail:
            line += " " + detail
        if r.hint and not detail:
            line += " -> " + r.hint
            print(line.rstrip())
            continue
        print(line.rstrip())
        if r.hint:
            print("  %s -> %s" % (" " * 6, r.hint))
    print()


def next_action(all_rows):
    """다음에 할 일 한 줄."""
    for stage, title, r in all_rows:
        if r.status == BAD:
            return "[%s %s] %s: %s" % (stage, title, r.label, r.hint or r.detail)
    for stage, title, r in all_rows:
        if r.status == TODO:
            return "[%s %s] %s: %s" % (stage, title, r.label, r.hint or r.detail)
    return "전부 채워졌다. ujyu build 로 배포본을 조립하고 실게임에서 확인할 것."


# ─────────────────────────────────────────── main
def load_config():
    try:
        from ujyu.titleconfig import config as C
    except SystemExit:
        raise SystemExit(
            "config.py 를 찾을 수 없습니다.\n"
            "  - 타이틀 리포 루트(= config.py 가 있는 폴더)에서 실행하거나\n"
            "  - MIRIS_CONFIG_DIR=<config.py 가 있는 폴더> 를 지정하세요.\n"
            "  새 타이틀이면 engine/samples/config.py 를 리포 루트로 복사해 채웁니다"
            " (docs/BOOTSTRAP.md 0단계).")
    except Exception as e:
        raise SystemExit("config.py 를 읽다가 실패했습니다: %s\n"
                         "  파일 문법과 참조하는 경로를 확인하세요." % _safe(e))
    return C


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu inspect",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="config 채움 상태를 부트스트랩 단계별로 점검하고 다음 할 일을 알려준다",
        epilog="예시:\n"
               "  ujyu inspect                전체 단계 요약\n"
               "  ujyu inspect -v             채워진 항목의 실제 값까지\n"
               "  ujyu inspect --stage 6      exe 단계만\n"
               "\n값 조회/변경은 ujyu config (show / get / set)\n"
               "종료 코드: '문제'가 있으면 1, 없으면 0 ('미설정'은 0)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="채워진 항목의 실제 값까지 표시")
    ap.add_argument("--stage", metavar="STAGE",
                    help="이 단계만 검사 (%s)" % " ".join(STAGE_IDS))
    args = ap.parse_args()

    if args.stage is not None and args.stage not in STAGE_IDS:
        raise SystemExit("알 수 없는 단계: %s (가능: %s)" % (args.stage, " ".join(STAGE_IDS)))

    C = load_config()
    D = Doctor(C)

    cfg = getattr(C, "__file__", "?")
    print("ujyu inspect - config 채움 상태 (docs/BOOTSTRAP.md 단계 기준)")
    print("config : %s" % _short(cfg, 120))
    print("ORIG   : %s" % _short(D.orig or "(미설정)", 120))
    print("GAME   : %s" % _short(D.game or "(미설정)", 120))
    print()

    scale = getattr(C, "SCALE", 1)
    progress, all_rows, n_bad = [], [], 0

    for stage, title, specs in STAGES:
        if args.stage is not None and stage != args.stage:
            continue
        rows = [D.check(s) for s in specs] + extra_rows(stage, D)

        # SCALE=1 이면 해상도 키는 필요 없다. 요청하지 않는 한 접어 둔다.
        folded = (stage == "11" and (not isinstance(scale, int) or scale <= 1)
                  and args.stage is None and not args.verbose)

        n_ok = sum(1 for r in rows if r.status == OK)
        progress.append((stage, title, n_ok, len(rows)))
        for r in rows:
            all_rows.append((stage, title, r))
            if r.status == BAD:
                n_bad += 1

        if folded:
            print("[%s] %s  (%d/%d)" % (stage, title, n_ok, len(rows)))
            print("     SCALE=1 (원본 해상도) - 확대하지 않으면 아래 키는 필요 없다."
                  " 보려면 --stage 11")
            print()
        else:
            print_stage(stage, title, rows, args.verbose, C)

    print("요약")
    for stage, title, n_ok, n in progress:
        print("  %s %s %d/%d" % (_pad(stage, 3), _pad(title, 14), n_ok, n))
    tot_ok = sum(p[2] for p in progress)
    tot = sum(p[3] for p in progress)
    n_todo = tot - tot_ok - n_bad
    print("  합계: OK %d / 미설정 %d / 문제 %d (전체 %d)" % (tot_ok, n_todo, n_bad, tot))
    print()
    print("다음 할 일: %s" % next_action(all_rows))
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
