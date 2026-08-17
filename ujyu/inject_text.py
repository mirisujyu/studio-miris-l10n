#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CP949 직접 인코딩 파이프라인 (Path B 슬롯 치환을 대체)

배경
----
엔진 문자 추출기 `0x438D50`이 리드바이트 비트맵 `0x477468`(32B=256bit)로
"이 바이트가 2바이트 문자의 리드인가"를 판정한다. 원본은 0x81-9F, 0xE0-EF만 리드라
CP949 한글 리드(0xB0-C8)가 1바이트로 쪼개졌다.
비트맵을 0x81-0xFE로 교체하면 CP949 한글이 정상 2바이트로 소비된다.

따라서 한글을 **CP949로 그대로** 넣으면 된다. 슬롯 매핑/커스텀 cmap 불필요.
폰트는 실제 한글 cmap 을 가진 가공 폰트를 쓴다 (build_font.py 참조).
(렌더는 GetGlyphOutlineA(GGO_GRAY8) 경로이고, 시프트는 하단 잘림 보정용이라 계속 필요)

제약 (엔진 리더 0x408E00 기준)
-----------------------------
리더는 바이트 값으로 분기한다:

    <= 0x2F   제어코드 점프테이블 (0x409B54)
    0x30-0x4F 2바이트 커맨드      0x50-0x5F, 0x60-0x6F, 0x70-0x7F  각 핸들러
    >= 0x80   표시 문자 (2바이트 조립)

즉 **표시되는 텍스트의 모든 바이트는 0x80 이상**이어야 한다. ASCII는 전부 커맨드다.
→ 번역문의 ASCII(공백·영숫자·기호)는 **전각으로 정규화**한다 (전각 ASCII = CP949 0xA3xx,
  전각 공백 U+3000 = 0xA1A1).

**제어코드 보존**
원문(jp)에는 표시문자와 함께 제어코드가 섞여 있다. 특히 대사 끝의 `」%`에서
`%`(0x25)는 **대기/종료 제어코드**다 (전체 대사 46,513조각 중 25,889조각이 해당).
이걸 전각 `％`로 바꾸거나 빠뜨리면 스크립트가 깨진다.
→ `encode()`는 **0x30 미만 문자를 원본 바이트 그대로 통과**시키고, 나머지만 전각 정규화한다.
→ `verify()`로 번역문의 제어코드가 원문과 일치하는지 검사한다.

사용
----
  ujyu inject check              strings.json의 kr 전수 검증
  ujyu inject preview <idx>      한 조각 인코딩 미리보기
  ujyu inject build <out_dir>    전체 시나리오 아카이브 주입 생성
                                 (원본=C.ORIG_DIR, 유효본 arc별 주입+재매핑)
strings.json 은 v2 포맷: `ujyu scn extract` 산출
  [{arc, file, id, kind, off, bytelen, jp[, speaker], kr}]
"""
import sys, os, json, io, re, argparse, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ujyu.titleconfig import config as C
STRINGS = C.STRINGS

# ---------------------------------------------------------------- 정규화

FW_SPACE = "　"          # 전각 공백 -> CP949 A1 A1

# ASCII -> 전각 매핑 (U+0021~U+007E -> U+FF01~U+FF5E)
def _to_fullwidth(ch):
    o = ord(ch)
    if o == 0x20:
        return FW_SPACE
    if 0x21 <= o <= 0x7E:
        return chr(o - 0x21 + 0xFF01)
    return ch

# 일본어 마침표/쉼표 → 한국어(반각형).
# 전각 마침표/쉼표(U+FF0E/U+FF0C)로 옮긴다. 폰트가 FF01-FF5E 를 비례 ASCII 글리프로
# 재매핑하므로(7절) 화면엔 좁은 . , 로 나온다. 뒤에 기타 문자가 이어지면 반각 폭
# 공백(전각공백 U+3000; 이 폰트가 반각으로 렌더)을 한 칸 넣는다. 번역/미번역 공통.
JP_PUNCT = {
    "。": "．",   # U+3002 → U+FF0E
    "、": "，",   # U+3001 → U+FF0C
}

# CP949 에 없는 대시류 → 전각 대시 U+2015(―, CP949 0xA1AA).
# 번역문의 늘임표(우쥬―…, 아―, 하카―안)는 em대시(U+2014)나 카타카나 장음(U+30FC ー)으로
# 자주 들어오는데 둘 다 CP949 인코딩이 안 된다. 한국어 표준 장음부호로 정규화한다.
_DASH_NORM = {
    0x2012: "―", 0x2013: "―", 0x2014: "―",   # figure/en/em dash
    0x30FC: "―",                              # 카타카나 장음 ー
    0x2500: "―",                              # 박스드로잉 ─ (오입력 방지)
}

def _is_content(ch):
    """기타 문자(문자·숫자)인가. 기호·구두점·공백은 아니다."""
    return unicodedata.category(ch)[0] in ("L", "N")

def sub_jp_punct(text):
    """config.FONT_WIDTH_MODE 를 따른다:
      · "proportional" : `。`/`、` → `．`/`，`, 뒤가 기타 문자면 반각 공백 한 칸(가변폭 간격 보정).
      · "fullwidth"    : 전각 `。`/`、` 그대로 둔다(고정폭이라 간격이 자연스러움).
    뒤가 기호이거나 문장 끝이면 공백을 넣지 않는다."""
    if not text or getattr(C, "FONT_WIDTH_MODE", "proportional") == "fullwidth":
        return text
    out = []
    n = len(text)
    for i, ch in enumerate(text):
        rep = JP_PUNCT.get(ch)
        if rep is None:
            out.append(ch); continue
        out.append(rep)
        nxt = text[i + 1] if i + 1 < n else ""
        if nxt and _is_content(nxt):
            out.append(FW_SPACE)
    return "".join(out)

# 반각으로 들어오기 쉬운 문장부호 → CP949 안전한 전각
# 및 CP949에 없는 유사문자 → 있는 것으로 치환
PUNCT = {
    "-": "―",
    "~": "～",
    '"': "“",
    "'": "‘",
    "・": "·",   # U+30FB 가타카나 중점은 CP949에 없다 → U+00B7 (A1 A4)
    "∙": "·",   # U+2219
    "〜": "～",   # U+301C → U+FF5E
    "−": "―",   # U+2212
    "･": "·",   # U+FF65 반각 중점
}

# ---------------------------------------------------------------- 커맨드 분리

TOKEN = re.compile(r"\{(\d+)\}")

# 여러 글자로 된 커맨드 시퀀스. 원문에 그대로 두어야 하는 것들.
CMD_SEQS = C.CMD_SEQS

def is_ctrl(ch):
    """**모호하지 않은** 제어코드인가.

    엔진 리더는 0x00-0x7F 를 전부 커맨드로 소비하지만, 그건 **본문 메시지 경로**
    기준이다. 스태프롤·크레딧 등 다른 화면은 ASCII 를 그대로 표시한다
    (원문에 `teamL` `-...-` 같은 ASCII 가 실제로 들어 있다).

    그래서 무조건 보존해야 하는 것은 **0x30 미만 제어코드**뿐이다.
    0x30-0x7F 는 표시 텍스트로 보고 전각 정규화한다 — 본문에서도 안전하고,
    다른 화면에서도 그대로 보인다.
    """
    return ord(ch) < 0x30

def segments(text):
    """원문 런을 [(kind, 문자열)] 로 쪼갠다. kind = 'cmd' | 'disp'.

    커맨드 = 0x30 미만 제어코드 + CMD_SEQS 의 시퀀스.
    """
    text = text or ""
    out = []; cur = ""; k = None
    i = 0
    while i < len(text):
        hit = next((c for c in CMD_SEQS if text.startswith(c, i)), None)
        if hit:
            if cur:
                out.append((k, cur)); cur = ""
            out.append(("cmd", hit)); k = None
            i += len(hit); continue
        kk = "cmd" if is_ctrl(text[i]) else "disp"
        if kk != k:
            if cur:
                out.append((k, cur))
            cur = ""; k = kk
        cur += text[i]; i += 1
    if cur:
        out.append((k, cur))
    return out

def to_template(jp):
    """원문 -> (커맨드를 {n} 토큰으로 바꾼 템플릿, [커맨드 문자열])

    토큰화 대상은 **확실한 커맨드만**:
      - 꼬리의 연속된 제어코드 (`0x30` 미만) — 대사 끝 `%` 등
      - CMD_SEQS 에 등록된 시퀀스 — `\n` 줄바꿈 등

    나머지 ASCII 는 표시 텍스트로 본다. 엔진 리더는 본문 경로에서 0x00-0x7F 를
    전부 커맨드로 소비하지만, 스태프롤·크레딧 등 다른 화면은 ASCII 를 그대로
    표시한다 (원문에 ` teamL` `-...-` 같은 ASCII 가 실제로 들어 있다).
    표시 텍스트로 두면 전각 정규화되어 **어느 경로에서든 안전**하다.

    중간에 커맨드가 있는 드문 경우는 번역문에 `{n}` 토큰을 직접 넣어 처리한다.
    """
    lead, rest = split_lead(jp or "")
    body, tail = split_tail(rest)
    cmds = []; parts = []
    if lead:
        parts.append("{%d}" % len(cmds)); cmds.append(lead)
    i = 0
    while i < len(body):
        hit = next((c for c in CMD_SEQS if body.startswith(c, i)), None)
        if hit:
            parts.append("{%d}" % len(cmds)); cmds.append(hit); i += len(hit)
        else:
            parts.append(body[i]); i += 1
    if tail:
        parts.append("{%d}" % len(cmds)); cmds.append(tail)
    return "".join(parts), cmds

def _legacy_to_template(kr, cmds):
    """토큰이 없는 구 번역문을 템플릿으로 변환 (하위호환).

    꼬리 커맨드를 먼저 처리하고, 나머지는 **앞에서부터** 첫 등장을 차례로 토큰화한다.
    (뒤에서부터 바꾸면 `\\n` 처럼 같은 커맨드가 여럿일 때 토큰 순서가 뒤집힌다.)
    """
    n = len(cmds)
    last = n
    if n and cmds[-1] and kr.endswith(cmds[-1]):
        kr = kr[: -len(cmds[-1])] + "{%d}" % (n - 1)
        last = n - 1
    for i in range(last):
        c = cmds[i]
        if c and c in kr:
            kr = kr.replace(c, "{%d}" % i, 1)
    return kr

# 여는 낫표 — 앞에 좁은 공백을 넣을 대상
OPEN_QUOTES = ("「", "『")

def space_before_quote(text):
    """문중의 여는 낫표(`「` `『`) 앞에 전각공백을 한 칸 넣는다.

    가변폭에서는 여는 낫표의 획이 칸 오른쪽에 정렬돼 있다(build_font 의 OPEN).
    문두에서는 그 왼쪽 여백이 들여쓰기 노릇을 하지만, 문장 **중간**에 오면 앞 글자에
    바짝 붙어 읽기 나쁘다. 그래서 앞에 한 칸 띄운다 — 전각공백은 폰트가 1/4 폭으로
    렌더하므로 좁은 사이가 된다.

    넣지 않는 경우:
      · 문두 (이미 들여쓰기 노릇을 한다)
      · 앞이 또 다른 여는 낫표 (`「『` 처럼 붙여 쓰는 표기)
      · 앞이 이미 공백
    """
    if not text:
        return text
    out = []
    for i, ch in enumerate(text):
        if ch in OPEN_QUOTES and i > 0:
            prev = text[i - 1]
            if prev not in OPEN_QUOTES and prev != FW_SPACE and prev != " ":
                out.append(FW_SPACE)
        out.append(ch)
    return "".join(out)

def space_after_ellipsis(text):
    """`…` 바로 뒤에 한글이 오면 전각공백을 한 칸 넣는다.

    `…가` 처럼 붙으면 말줄임표가 다음 글자에 먹혀 읽기 나쁘다. `。`/`、` 뒤에
    공백을 넣는 것(`sub_jp_punct`)과 같은 이유다 — 전각공백은 폰트가 1/4 폭으로
    렌더하므로 좁은 사이가 된다.

    `……` 처럼 말줄임표가 이어지는 경우는 **마지막 것 뒤에만** 들어간다(중간의
    `…` 는 뒤가 한글이 아니므로 걸리지 않는다).
    """
    if not text:
        return text
    out = []
    n = len(text)
    for i, ch in enumerate(text):
        out.append(ch)
        if ch == "…" and i + 1 < n and 0xAC00 <= ord(text[i + 1]) <= 0xD7A3:
            out.append(FW_SPACE)
    return "".join(out)

def normalize(text):
    """**표시 텍스트 전용** — ASCII 를 전부 전각으로 바꾼다.

    엔진은 0x80 미만을 전부 커맨드로 소비하므로 표시 텍스트에 ASCII 가 남으면 안 된다.
    커맨드는 이 함수에 오지 않는다 (encode_piece 가 토큰으로 분리해 raw 로 처리).
    """
    if not text:
        return text
    # 검수 마커(config.REVIEW_MARK)는 번역자가 "이 줄은 다시 보라"고 남긴 표시다.
    # strings.json 에는 남겨 두고(`ujyu filter review` 의 검토 열이 이걸 본다) **화면에
    # 나가기 직전에만** 뗀다. 안 떼면 그대로 글자로 찍힌다 — 실제로 147조각이
    # 아카이브까지 들어가 있었다.
    mark = getattr(C, "REVIEW_MARK", None)
    if mark:
        text = text.replace(mark, "")
        if not text:
            return text
    text = sub_jp_punct(text)          # 。、 → ．，(+뒤 문자에 따른 공백)
    if getattr(C, "FONT_WIDTH_MODE", "proportional") != "fullwidth":
        text = space_before_quote(text)     # 문중의 「『 앞에 좁은 공백
        text = space_after_ellipsis(text)   # … 뒤에 한글이 붙으면 좁은 공백

    text = text.translate(_DASH_NORM)  # em대시·장음 등 → 전각 대시 ―(CP949 안전)
    text = text.replace("...", "…")
    out = []
    for ch in text:
        if ch in PUNCT:
            out.append(PUNCT[ch])
        elif ord(ch) < 0x80:
            out.append(_to_fullwidth(ch))
        else:
            out.append(ch)
    return "".join(out)

# ---------------------------------------------------------------- 인코딩

class EncodeError(Exception):
    pass

def encode_body(text):
    """**표시 텍스트** -> CP949 바이트열. 전 바이트가 0x80 이상이어야 한다."""
    text = normalize(text)
    out = bytearray()
    for ch in text:
        try:
            b = ch.encode("cp949")
        except UnicodeEncodeError:
            raise EncodeError("CP949 인코딩 불가: %r (U+%04X)" % (ch, ord(ch)))
        if min(b) < 0x80:
            raise EncodeError("표시문자가 0x80 미만 바이트 생성: %r -> %s"
                              % (ch, " ".join("%02X" % x for x in b)))
        out += b
    return bytes(out)

def lead_space(kr, jp):
    """원문이 전각 공백으로 시작하면(나레이션 들여쓰기) 번역문도 공백으로 시작시킨다.

    번역기가 자주 빠뜨리는 부분이라 **주입 직전에 스크립트가 보장**한다.
    넣는 문자는 전각 공백 `U+3000`(CP949 `A1A1`) 하나 — 표시 바이트가 0x80 이상이어야
    하고(2절), 게임용 폰트가 `U+3000` 을 **반각 폭으로 렌더**하므로 화면에서는 반각
    들여쓰기가 된다 (build_font.py: `U+3000` advance = 한글 폭의 절반).
    """
    if not kr or not (jp or "").startswith(FW_SPACE):
        return kr
    if kr[0] in (FW_SPACE, " "):
        return kr
    return FW_SPACE + kr


def encode_value(kr):
    """`common.csv` 등 **설정 값** 전용 인코딩 — 평문 CP949, 전각 정규화 없음.

    본문 텍스트(encode_piece)와 달리 엔진 메시지 리더를 거치지 않는다. 창 제목은
    `SetWindowTextA` 로 OS 에 그대로 넘어가므로 ASCII 를 전각으로 바꾸면 안 된다.
    쉼표는 필드 구분자라 값에 들어가면 뒤 필드가 밀린다 — 막는다.
    """
    if "," in kr:
        raise EncodeError("common.csv 값에 쉼표를 쓸 수 없다: %r" % kr)
    try:
        return kr.encode("cp949")
    except UnicodeEncodeError as e:
        raise EncodeError("CP949 인코딩 불가: %s" % e)


def encode_piece(kr, jp):
    """번역문 + 원문 -> 주입용 바이트열.

    `{n}` 토큰은 원문 커맨드 바이트로 그대로 복원하고, 나머지 표시 텍스트만
    전각 정규화 후 CP949 로 인코딩한다. 커맨드가 런 어디에 있든 안전하다.
    원문의 선행 전각 공백(들여쓰기)은 `lead_space` 가 보장한다.
    """
    kr = lead_space(kr, jp)
    _tpl, cmds = to_template(jp)
    if not TOKEN.search(kr):
        kr = _legacy_to_template(kr, cmds)
    out = bytearray(); pos = 0
    for m in TOKEN.finditer(kr):
        out += encode_body(kr[pos:m.start()])
        i = int(m.group(1))
        if i >= len(cmds):
            raise EncodeError("원문에 없는 커맨드 토큰 {%d} (원문 커맨드 %d개)" % (i, len(cmds)))
        out += cmds[i].encode("latin1")
        pos = m.end()
    out += encode_body(kr[pos:])
    return bytes(out)

# 하위호환
def encode(text, do_normalize=True):
    return encode_body(text)

def split_lead(s):
    """(선두커맨드, 나머지). 문장 앞에 붙은 커맨드 문자를 분리.

    `*真部の顔の…` 처럼 **앞**에 커맨드가 붙는 조각이 있다. 꼬리만 떼면 이 `*`(0x2A)가
    표시 텍스트로 남고, `normalize` 가 전각 `＊`(A3AA)로 바꿔 **화면에 보인다** —
    커맨드도 잃는다. 꼬리와 같은 규칙으로 앞도 떼어 토큰으로 넘긴다.
    """
    n = len(s)
    k = 0
    while k < n:
        if is_ctrl(s[k]):
            k += 1
        else:
            break
    return s[:k], s[k:]


def split_tail(s):
    """(본문, 꼬리커맨드). 끝에 연속된 커맨드 문자를 분리.

    제어코드(0x30 미만)뿐 아니라 **`<제어><ASCII>` 쌍**도 꼬리로 본다. 실제 원문에
    `………それが、*v` / `手を……」*t` 처럼 `*`(0x2A) 뒤에 오퍼랜드 한 글자가 붙는
    형태가 237조각 있다. 뒤에서부터 제어코드만 벗기면 `v`(0x76)에서 멈춰 `*v` 가
    통째로 표시 텍스트로 남고, `normalize` 가 전각 `＊ｖ` 로 바꿔 **화면에 그대로
    보인다**. 오퍼랜드 앞이 제어코드라는 것이 판정 근거이므로 스태프롤의 순수
    ASCII(`teamL` 등)는 걸리지 않는다.
    """
    k = len(s)
    while k > 0:
        if is_ctrl(s[k - 1]):
            k -= 1
        elif k >= 2 and is_ctrl(s[k - 2]) and ord(s[k - 1]) < 0x80:
            k -= 2                       # <제어><오퍼랜드> 쌍
        else:
            break
    return s[:k], s[k:]

# 반각 가나(U+FF61~FF9F)는 SJIS 에서 1바이트 오퍼랜드다. 원본 바이트를 유지한다.
def _is_jp_glyph(ch):
    """화면에 나올 일본어 글자인가 (가나·한자·CJK 기호).

    커맨드 오퍼랜드가 SJIS 로 디코드되면 한자나 PUA 문자처럼 보인다. 그건 표시할
    글자가 아니라 원본 바이트로 남아야 하므로, 미매핑 감사에서 빼려고 쓴다.
    """
    o = ord(ch)
    return (0x3000 <= o <= 0x303F or 0x3040 <= o <= 0x30FF
            or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF
            or 0xF900 <= o <= 0xFAFF)


def _is_halfwidth_kana(ch):
    return 0xFF61 <= ord(ch) <= 0xFF9F

def verify(jp, kr, kind=None):
    """번역문 검증. 문제 목록 반환 (빈 리스트 = 통과).

    kind='csv' 는 설정 값이라 커맨드 토큰·전각 규칙이 적용되지 않는다."""
    errs = []
    if kind == "csv":
        try:
            encode_value(kr)
        except EncodeError as e:
            errs.append(str(e))
        return errs
    _tpl, cmds = to_template(jp)
    kr2 = kr if TOKEN.search(kr) else _legacy_to_template(kr, cmds)
    got = [int(m.group(1)) for m in TOKEN.finditer(kr2)]
    if got != list(range(len(cmds))):
        errs.append("커맨드 토큰 불일치: 원문 %d개%s, 번역문 %s"
                    % (len(cmds), cmds and " %r" % (cmds,) or "", got))
    try:
        enc = encode_piece(kr, jp)
    except EncodeError as e:
        errs.append(str(e))
        return errs
    # 커맨드 바이트가 주입 결과에서 사라지지 않았는가.
    # `*`(0x2A)·`%`(0x25)는 이 엔진의 실제 커맨드다. 토큰으로 넘기지 못하면
    # 표시 텍스트로 취급돼 `normalize` 가 전각(`＊`=A3AA)으로 바꿔 버린다 —
    # 화면에 기호가 보이고 커맨드는 실행되지 않는다. 실제로 선두 `*` 6건이
    # 그렇게 새고 있었다(split_lead 도입 전).
    ob = jp.encode("cp932", "ignore")
    for b, name in ((0x2A, "*"), (0x25, "%")):
        a, c = ob.count(b), enc.count(b)
        if c < a:
            errs.append("커맨드 %r 유실: 원문 %d개 -> 주입 %d개 "
                        "(전각으로 바뀌었을 수 있다)" % (name, a, c))
    return errs

def problems(text):
    try:
        encode_body(text)
        return []
    except EncodeError as e:
        return [str(e)]

def transcode_jp(jp):
    """**미번역 조각**을 SJIS -> CP949로 옮긴다 (번역이 아니라 인코딩 변환).

    번역이 없는 조각을 SJIS인 채로 두면, 리드 비트맵 패치 이후 CP949로 해석돼
    깨진 한글이 된다. 특히 대사 여는 괄호 `「`(SJIS 8175)가 별도 조각이라
    닫는 `」`만 CP949가 되는 비대칭이 생긴다.

    문자별 처리:
      - 0x80 미만          : 제어/커맨드 → 원본 바이트 그대로
      - 반각 가나           : 제어 오퍼랜드 → 원본 SJIS 1바이트 그대로
      - 그 외 표시문자      : CP949로 변환
      - CP949에 없는 글자   : `ujyu jpmap` 이 배정한 **사용자정의영역**(C9A1../FEA1..)
                             코드로 변환. 폰트 쪽은 build_font 의 merge_jp 가 같은 표를
                             보고 U+E000.. 에 글리프를 심어 둔다. 표가 없으면(=jpmap 을
                             안 돌렸으면) 예전처럼 원본 SJIS 바이트를 유지한다.

    반환: (바이트열, 변환된 표시문자 수, 변환실패 문자 수)
    """
    from ujyu import jpmap
    extra, _pua = jpmap.load()
    out = bytearray()
    conv = fail = 0
    # 여기서는 `sub_jp_punct` 를 쓰지 않는다 — **원문 표기를 그대로 둔다.**
    # 번역문은 한국어 조판에 맞춰 `。、` 를 `．，`(+공백)로 바꾸지만, 이 경로는
    # 일본어가 일본어인 채로 나오는 대목이라 온점·반점도 일본어 것을 유지한다.
    for ch in (jp or ""):
        o = ord(ch)
        if o < 0x80:
            out.append(o)
            continue
        try:
            sj = ch.encode("cp932")
        except UnicodeEncodeError:
            sj = None
        if _is_halfwidth_kana(ch):
            out += sj if sj else b"?"
            continue
        try:
            out += ch.encode("cp949")
            conv += 1
        except UnicodeEncodeError:
            if ch in extra:
                out += extra[ch]
                conv += 1
            else:
                out += sj if sj else b"?"
                fail += 1
    return bytes(out), conv, fail

# ---------------------------------------------------------------- 주입

def build(out_dir, strings_path=STRINGS, verbose=True):
    """v2 strings(vneg 구조적 추출, `scn.py extract`)를 원본 아카이브에 주입한다.

    - 레코드의 `arc` = 그 파일의 유효본(override 승자) 아카이브. 각 아카이브는
      자기 소유 .scn만 교체하므로 별도 '패치 아카이브 주입' 단계가 없다.
    - kr 있으면 encode_piece(커맨드 보존), 없으면 표시문자 조각만 SJIS→CP949 변환.
    - 같은 파일 내 조각은 **오프셋 내림차순**으로 교체해 앞쪽 오프셋이 밀리지 않게 한다.
    - 내용이 바뀐 .scn은 점프테이블 재매핑(miris.vneg.relocate_jumptable)까지 마친다.
    원본은 C.ORIG_DIR 에서 읽고, 결과 아카이브 전부를 out_dir 에 쓴다.
    """
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ujyu.formats import axr as A, vneg as V

    S = json.load(open(strings_path, encoding="utf-8"))
    todo = {}                                   # arc -> file -> [rows]
    for r in S:
        todo.setdefault(r["arc"], {}).setdefault(r["file"], []).append(r)

    os.makedirs(out_dir, exist_ok=True)
    _unmapped_seen = {}          # 미매핑 문자 -> 처음 본 곳
    tot_tl = tot_tc = 0
    errors = []
    for arc in C.ARCHIVES:
        src = C.orig(arc)
        data, entries, tbl = A.load(src)
        blk2 = int.from_bytes(open(src, "rb").read()[8:12], "little")
        byname = todo.get(arc, {})
        files = []
        n_tl = n_tc = n_rel = 0
        for name, off, sz in entries:
            orig_scn = A.getfile(data, tbl, off, sz)
            rows = byname.get(name)
            if not rows:
                files.append((name, orig_scn))
                continue
            buf = bytearray(orig_scn)
            edits = []                       # (원본오프셋, 원본길이, 새길이) — 재매핑에 그대로 쓴다
            for r in sorted(rows, key=lambda r: r["off"], reverse=True):
                kr = (r.get("kr") or "").strip()
                jp = r.get("jp") or ""
                try:
                    ob = jp.encode("cp932")
                except UnicodeEncodeError:
                    errors.append("%s/%s %s: jp가 cp932가 아님" % (arc, name, r["id"]))
                    continue
                if bytes(buf[r["off"]:r["off"] + r["bytelen"]]) != ob:
                    errors.append("%s/%s %s@0x%x: 원본 바이트 불일치"
                                  % (arc, name, r["id"], r["off"]))
                    continue
                if kr:
                    try:
                        enc = encode_value(kr) if r.get("kind") == "csv" \
                              else encode_piece(kr, jp)
                    except EncodeError as e:
                        errors.append("%s/%s %s: %s" % (arc, name, r["id"], e))
                        continue
                    # 주입하면서 감사한다 — 원문의 커맨드가 결과에서 빠지지 않았는지.
                    # 인코딩이 성공해도 커맨드가 표시문자로 오인돼 전각이 되는 수가
                    # 있다(선두 `*` 가 실제로 그랬다). 그건 여기서만 보인다.
                    for e in verify(jp, kr, r.get("kind")):
                        errors.append("%s/%s %s: %s" % (arc, name, r["id"], e))
                    n_tl += 1
                else:
                    # 미번역: 표시문자가 있는 조각만 SJIS -> CP949 변환
                    if not any(ord(c) >= 0x80 and not _is_halfwidth_kana(c) for c in jp):
                        continue
                    enc, _c, nfail = transcode_jp(jp)
                    if nfail and (jp or "").strip() not in C.MARKERS:
                        # CP949 에도 jp_charmap 에도 없는 글자 → 원본 SJIS 로 남아
                        # 화면에서 깨진다. `ujyu jpmap` 을 다시 돌려야 한다.
                        # **일본어 표시문자만** 본다 — 커맨드 오퍼랜드가 텍스트로
                        # 디코드되는 경우(config.MARKERS 의 `钁`, PUA 로 읽히는
                        # U+E036 등)는 원본 바이트로 남는 것이 정상이라 뺀다.
                        for ch in jp:
                            if ch in _unmapped_seen or not _is_jp_glyph(ch):
                                continue
                            try:
                                ch.encode("cp949"); continue
                            except UnicodeEncodeError:
                                pass
                            from ujyu import jpmap as _jm
                            if ch not in _jm.load()[0]:
                                _unmapped_seen[ch] = "%s/%s" % (arc, name)
                    if enc == ob:
                        continue
                    n_tc += 1
                # 문두 들여쓰기: 여는 괄호(「z/（z/『z = quote) 앞에 **전각 공백**(CP949 a1a1)을
                # 넣는다. 텍스트 블록은 2바이트 단위로 파싱되므로 반각 공백(0x20 1바이트)을
                # 넣으면 블록이 깨진다(실측). build_font 가 U+3000 폭을 반절로 줄여놨으므로
                # 화면엔 반각 크기로 보인다. config.QUOTE_LEAD_SPACE 가 켜진 타이틀만.
                if r.get("kind") == "quote" and getattr(C, "QUOTE_LEAD_SPACE", False) \
                        and not enc.startswith(b"\xa1\xa1"):
                    enc = b"\xa1\xa1" + enc
                buf[r["off"]:r["off"] + r["bytelen"]] = enc
                edits.append((r["off"], r["bytelen"], len(enc)))
            out = bytes(buf)
            if out != orig_scn and out[:4] == b"VNEG":
                out, fixed, fail = V.relocate_jumptable(out, orig_scn, edits)
                n_rel += fixed
                if fail:
                    errors.append("%s/%s: 점프테이블 재매핑 실패 %d건" % (arc, name, fail))
            files.append((name, out))
        dst = os.path.join(out_dir, arc)
        open(dst, "wb").write(A.pack(files, blk2))
        tot_tl += n_tl; tot_tc += n_tc
        if verbose:
            print("  %-14s 번역주입 %5d / SJIS→CP949 %6d / 점프오프셋수정 %5d -> %s"
                  % (arc, n_tl, n_tc, n_rel, dst))
    if verbose and _unmapped_seen:
        # 미번역 일본어를 CP949 로 옮기지 못한 글자. 원본 SJIS 로 남아 화면에서 깨진다.
        # 표(jp_charmap)와 폰트가 같이 갱신돼야 하므로 둘 다 다시 만들라고 안내한다.
        print("  ⚠ CP949 에도 jp_charmap 에도 없는 글자 %d종 — 화면에서 깨진다."
              % len(_unmapped_seen))
        print("    `ujyu jpmap` 으로 표를 다시 만들고 `ujyu font` 로 폰트도 다시 만들어라.")
        for ch, where in list(_unmapped_seen.items())[:20]:
            print("      %r  (%s)" % (ch, where))
    if verbose and errors:
        print("오류 %d건:" % len(errors))
        for e in errors[:20]:
            print("  " + e)
    return tot_tl, errors

# ---------------------------------------------------------------- CLI

def cmd_check():
    S = json.load(open(STRINGS, encoding="utf-8"))
    rows = [r for r in S if (r.get("kr") or "").strip()]
    bad = []
    for i, r in enumerate(S):
        kr = (r.get("kr") or "").strip()
        if not kr:
            continue
        for p in verify(r.get("jp"), kr):
            bad.append((i, r["file"], r.get("jp"), kr, p))
    out = io.open("_cp949_check.txt", "w", encoding="utf-8")
    out.write("번역 채워진 조각: %d / 전체 %d\n" % (len(rows), len(S)))
    out.write("문제 조각: %d\n\n" % len(bad))
    for i, f, jp, kr, p in bad[:200]:
        out.write("[%d] %s\n  JP: %s\n  KR: %s\n  -> %s\n\n" % (i, f, jp, kr, p))
    out.close()
    print("번역 %d조각, 문제 %d건 -> _cp949_check.txt" % (len(rows), len(bad)))

def cmd_preview(idx):
    S = json.load(open(STRINGS, encoding="utf-8"))
    r = S[idx]
    kr = r.get("kr") or ""
    out = io.open("_cp949_preview.txt", "w", encoding="utf-8")
    out.write("file=%s off=%d bytelen=%d\n" % (r["file"], r["off"], r["bytelen"]))
    out.write("JP: %s\n" % r["jp"])
    out.write("KR: %s\n" % kr)
    if kr:
        nz = normalize(kr)
        out.write("정규화: %s\n" % nz)
        try:
            b = encode(kr)
            out.write("CP949 %dB: %s\n" % (len(b), " ".join("%02X" % x for x in b)))
            out.write("최소바이트: 0x%02X (0x30 이상이어야 함)\n" % min(b))
        except EncodeError as e:
            out.write("오류: %s\n" % e)
    out.close()
    print("-> _cp949_preview.txt")

def main():
    ap = argparse.ArgumentParser(
        prog="ujyu inject",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="시나리오 텍스트 CP949 주입 + 점프테이블 재매핑",
        epilog="예:\n"
               "  ujyu inject check          # strings.json 의 kr 전수 검증\n"
               "  ujyu inject build _out     # 원본 아카이브에 주입해 _out 폴더로 생성\n")
    sub = ap.add_subparsers(dest="cmd", metavar="<명령>")

    sub.add_parser("check", help="strings.json 의 kr 전수 검증 (_cp949_check.txt)",
                   description="strings.json 의 번역문을 전수 검증해 _cp949_check.txt 로 쓴다")

    p_prev = sub.add_parser("preview", help="한 조각 인코딩 미리보기 (_cp949_preview.txt)",
                            description="조각 하나의 정규화·CP949 인코딩 결과를 미리 본다")
    p_prev.add_argument("idx", type=int, help="strings.json 안 조각 인덱스 (0부터)")

    p_build = sub.add_parser("build", help="전체 시나리오 아카이브 주입 생성",
                             description="원본(config.ORIG_DIR)에 텍스트를 주입해 아카이브를 새로 만든다")
    p_build.add_argument("out_dir", help="결과 아카이브를 쓸 폴더 (없으면 만든다)")

    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        return
    if a.cmd == "check":
        cmd_check()
    elif a.cmd == "preview":
        cmd_preview(a.idx)
    elif a.cmd == "build":
        build(a.out_dir)

if __name__ == "__main__":
    main()
