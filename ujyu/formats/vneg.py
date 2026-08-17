# -*- coding: utf-8 -*-
"""VNEG(.scn) 디스어셈블러 (강화판) — 심볼 테이블 + 점프/라벨 테이블 + flow.

포맷 상세: docs/formats/VNEG.md
- 헤더: VNEG + symcount(4 BE)
- 심볼 테이블: <op> <slots:u16BE> <argc> + 인자(01 int32BE / 00 cstr).
- 점프/라벨 테이블: flow_start에 위치. 00 <N:2BE> 00*6 | entry[0]=00 3b(제어코드) | <off:2BE> 00 00 ×(N-1). 타겟=flow_start+off.
- flow: 마이크로 바이트코드. 심볼참조 u16=0x1000+idx. 검증 명령 op 02/03/07/08/0c.
어떤 바이트도 버리지 않는다.

재패킹 필수 단계인 **점프테이블 재매핑**(relocate_archive)도 포함: 번역으로 길이가
바뀐 flow에서 점프/라벨 테이블의 flow상대 오프셋을 치환 스팬으로 재계산하며,
치환 정보(edits)가 있으면 difflib 을 전혀 쓰지 않는다 — 구조적 매핑만으로 정확하다.
치환 정보가 없는 단독 실행(`ujyu scn relocate`)만 difflib(_build_map)을 쓴다."""
import argparse, sys, os, struct, io, re, difflib, bisect
from collections import Counter

def is_lead(b): return (0x81 <= b <= 0x9f) or (0xe0 <= b <= 0xfc)
def is_trail(b): return (0x40 <= b <= 0x7e) or (0x80 <= b <= 0xfc)

# 심볼 op = 객체 타입 id (exe 타입 레지스트리 va 0x475818 실측, docs/formats/VNEG.md).
# 전체 타입표(레퍼런스, 0x00~0x16). 이전 07=DRAW/08=RECT/0c=SCREEN은 오판.
# 0x0f~0x15는 엔진 내장 객체이고, 0x16 scroll은 실제 심볼 정의에도 등장한다.
OP_TYPES = {0x00: "object", 0x01: "bool", 0x02: "int", 0x03: "string", 0x04: "flag",
            0x05: "register", 0x06: "event", 0x07: "layer", 0x08: "textwindow",
            0x09: "textwindow2", 0x0a: "soundtrack", 0x0b: "file", 0x0c: "button",
            0x0d: "threadinfo", 0x0e: "stringflag", 0x0f: "thread",
            0x10: "scenario", 0x11: "engine", 0x12: "display",
            0x13: "soundsystem", 0x14: "timer", 0x15: "eventsystem",
            0x16: "scroll"}
# 심볼 테이블(파일 선두 구조화 영역)은 전체 23타입으로 식별한다(OP_TYPES) — 스트링 포인트
# 명확화. 단 FLOW 영역 명령 검출(parse_cmd)은 flow 바이트 오검출 억제 위해 확정 5개만.
OPNAME = OP_TYPES
WHITELIST = {0x02, 0x03, 0x07, 0x08, 0x0c}   # flow 명령 화이트리스트 (int/string/layer/textwindow/button)

# 분기 opcode(라벨 인덱스로 점프): exe 핸들러 실측
JUMPOP = {0x10:"GOTO", 0x16:"IF=0→", 0x17:"IF≠0→", 0x18:"IF>0→",
          0x19:"IF≥0→", 0x1a:"IF<0→", 0x1b:"IF≤0→"}

def sdec(raw):
    try: return raw.decode("cp932")
    except: return raw.decode("latin1")

def reskw(name):
    """리소스 심볼 이름 → 동작 키워드 (0x27/0x28 등 리소스op용)."""
    if name[:2]=="bg": return "DRAW_BG"     # 배경
    if name[:3]=="se_" or name[:2]=="m_" or name[:6]=="movie/": return "PLAY"  # 효과음/BGM/무비
    return None

def op_action(op, w1):
    """op0b/op0f의 앞2바이트 마커(w1) → 동작 (이름 무관, exe 실측)."""
    if op==0x0f and w1==0x0202: return "DRAW_FACE"     # 얼굴 아이콘
    if op==0x0b:
        return {0x0076:"VOICE",   # 음성 (RU/FM/RJ/k6/FK 전부)
                0x0202:"SE",       # 효과음 (ika_se 등)
                0x001e:"GOTO", 0x001f:"GOTO"}.get(w1)  # 씬 분기/엔딩
    return None

def is_resfile(name):
    """리소스 파일명(전용 라인에 나오므로 hex 주석에선 억제)."""
    return name.isascii() and bool(re.match(
        r'^([a-z]{1,4}_|bg|se|sg|f_|m_|k\d|movie|event|ru\d|[A-Z]{1,2}\d)', name))

def parse_cmd(d, pos, n):
    if pos + 4 > n or d[pos+1] != 0x00 or d[pos+2] != 0x01:
        return None
    op = d[pos]; argc = d[pos+3]
    if op not in WHITELIST or argc > 64:
        return None
    if argc < 1:  # 실제 명령은 항상 인자>=1. argc=0은 flow 바이트의 우연정렬 오검출.
        return None
    p2 = pos + 4; args = []
    for _ in range(argc):
        if p2 >= n: return None
        tag = d[p2]
        if tag == 0x01:
            if p2 + 5 > n: return None
            args.append(("i", struct.unpack(">i", d[p2+1:p2+5])[0], p2+1)); p2 += 5
        elif tag == 0x00:
            e = d.find(b"\x00", p2+1)
            if e < 0: return None
            args.append(("s", d[p2+1:e], p2+1)); p2 = e + 1
        else:
            return None
    return op, args, p2

def parse_syms(d, args_out=None):
    """심볼 테이블 파싱 -> ([(idx,off,op,val)], flow_start).

    심볼 문법은 `<op> <슬롯수:2BE> <인자수> <args>`. 슬롯수는 보통 1 이지만
    int/string 배열은 한 정의가 여러 **연속 런타임 심볼 인덱스**를 차지한다.
    예를 들어 config.scn 의 `02 00 06 06` 은 한 배열 심볼이 아니라 initializer
    6개가 각각 한 슬롯이므로, 뒤 정의의 런타임 인덱스가 5만큼 증가한다.
    `off` 는 정의의 **끝** 오프셋, `val` 은 해당 슬롯의 마지막 인자값이다.

    args_out 에 dict 를 주면 {심볼idx: [(종류, 값, 값오프셋), ...]} 로 인자를 다 받는다
    ('i'=int32BE, 's'=문자열). 좌표 심볼의 값 위치를 알아낼 때 쓴다(ujyu/scn_dims.py).
    """
    def fail():
        if args_out is not None:
            args_out.clear()
        return [], 0

    if d[:4] != b"VNEG" or len(d) < 8:
        return fail()
    # d[4:6] 은 정체 미상(시나리오 씬은 0, UI 씬은 비영). d[6:8] 은 정의 수이며,
    # 런타임 슬롯 수는 각 정의의 slots 를 합한 값이다.
    ndefs = struct.unpack(">H", d[6:8])[0]; i = 8; syms = []; s = 0
    for _ in range(ndefs):
        if i + 4 > len(d) or d[i] not in OP_TYPES:
            return fail()
        op = d[i]; slots = struct.unpack(">H", d[i+1:i+3])[0]; argc = d[i+3]
        if slots < 1:
            return fail()
        i += 4; args = []
        ok = True                       # argc=0 인 선언 전용 심볼(flag/int)도 있다
        for _ in range(argc):
            if i >= len(d): ok = False; break
            typ = d[i]; i += 1
            if typ == 0:
                e = d.find(b"\x00", i)
                if e < 0: ok = False; break
                val = sdec(d[i:e]); args.append(("s", val, i)); i = e + 1
            elif typ == 1:
                if i + 4 > len(d): ok = False; break
                val = struct.unpack(">i", d[i:i+4])[0]; args.append(("i", val, i)); i += 4
            else: ok = False; break
        if not ok:
            return fail()

        if slots == 1:
            slot_args = [args]
        elif argc == 0:
            slot_args = [[] for _ in range(slots)]
        elif argc == slots:
            slot_args = [[a] for a in args]
        else:
            # 확인된 VNEG 배열은 전부 argc=0 또는 argc=slots다. 모르는 배치를
            # 억지로 인덱싱하면 이후 모든 참조가 틀어지므로 파싱을 중단한다.
            return fail()

        for a in slot_args:
            val = a[-1][1] if a else None
            syms.append((s, i, op, val))
            if args_out is not None: args_out[s] = a
            s += 1
    return syms, i

# ───────────────────────── flow 순차 파서 (인터프리터 구조 그대로) ─────────────────────────
# exe 의 바이트코드 디스패처를 그대로 옮긴 것이다 (神無ノ鳥 `0x408E00`).
#   op < 0x30   : 점프테이블 `0x409B54` 48엔트리 — 피연산자 길이는 OPLEN
#   0x30..0x4F  : u16 객체참조            -> 객체.메서드(op-0x30) ()      인자 없음
#   0x50..0x5F  : u8 argc + argc×u16                 -> 직전 객체.메서드(op-0x50)(인자)
#   0x60..0x7F  : u8 argc + argc×u16                 -> 전역 호출
#   0x80..0xFF  : +1바이트 = 2바이트 표시 문자 (`shl ebx,8; or ebx,cl`)
# 피연산자 정수는 전부 **빅엔디안**이다(`mov dh,[p]; mov dl,[p+1]`).
# 심볼 참조는 u16 값 `0x1000 + idx` (#256부터 `0x11xx`).
#
# OPLEN[op] = 그 opcode 가 소비하는 고정 피연산자 바이트 수. -1은 count 기반 가변
# 길이(01/02는 u16+method+argc+args, 1c/1d/1e는 argc+args)다.
OPLEN = [
    0, -1, -1, 3, 0, 0, 0, 0, 0, 0, 6, 4, 4, 4, 2, 2,      # 00-0f
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, -1, -1, -1, 0,     # 10-1f
    4, 4, 1, 1, 2, 2, 6, 3, 6, 3, 0, 0, 0, 0, 0, 0,        # 20-2f
]

SYMREF = 0x1000
SYMREF_MASK = 0xF000


def symref_index(value, symbol_count=None):
    """u16 피연산자의 런타임 심볼 인덱스. 심볼 참조가 아니면 None.

    인코딩은 `0x1000 + index`라 #256부터 상위 바이트가 0x11이 된다. 마스크만
    검사하면 0x1xxx 리터럴도 잡히므로 symbol_count가 있으면 실제 슬롯 범위도 확인한다.
    """
    if not 0 <= value <= 0xFFFF or (value & SYMREF_MASK) != SYMREF:
        return None
    idx = value & ~SYMREF_MASK
    if symbol_count is not None and idx >= symbol_count:
        return None
    return idx


def jumptable_end(d, flow_start):
    """flow 앞의 점프/라벨 테이블 끝(=실제 코드 시작) 오프셋."""
    if flow_start + 10 > len(d):
        return flow_start
    N = struct.unpack(">H", d[flow_start:flow_start + 2])[0]
    if not (0 < N < 64):
        return flow_start
    return flow_start + 8 + N * 4


def walk(d, start=None):
    """flow 를 명령 단위로 순차 파싱 -> [(오프셋, op, 길이, [(값, 값오프셋), ...])].

    `args` 는 u16 피연산자 목록(값, 파일오프셋). u32 피연산자는 값만 담고 길이로
    구분한다. 스트림 끝을 넘어가면 거기서 멈춘다(반환 리스트 마지막을 보면 안다).
    """
    _syms, fs = parse_syms(d)
    i = jumptable_end(d, fs) if start is None else start
    n = len(d)
    out = []
    while i < n:
        op = d[i]
        p = i + 1
        args = []
        if op in (0x01, 0x02):
            if p + 4 > n: break
            args.append((struct.unpack_from(">H", d, p)[0], p)); p += 2
            meth = d[p]; argc = d[p + 1]; p += 2
            if p + argc * 2 > n: break
            for _ in range(argc):
                args.append((struct.unpack_from(">H", d, p)[0], p)); p += 2
            out.append((i, op, p - i, args, meth)); i = p; continue
        if op in (0x1C, 0x1D, 0x1E):
            if p >= n: break
            argc = d[p]; p += 1
            if p + argc * 2 > n: break
            for _ in range(argc):
                args.append((struct.unpack_from(">H", d, p)[0], p)); p += 2
            out.append((i, op, p - i, args, None)); i = p; continue
        if op < 0x30:
            ln = OPLEN[op]
            if p + ln > n: break
            if op in (0x22, 0x23):          # u8
                args.append((d[p], p))
            elif op in (0x27, 0x29):        # u8 + u16
                args.append((d[p], p))
                args.append((struct.unpack_from(">H", d, p + 1)[0], p + 1))
            elif op in (0x26, 0x28):        # u32 + u16
                args.append((struct.unpack_from(">I", d, p)[0], p))
                args.append((struct.unpack_from(">H", d, p + 4)[0], p + 4))
            elif op == 0x03:                # u16 + u8
                args.append((struct.unpack_from(">H", d, p)[0], p))
                args.append((d[p + 2], p + 2))
            elif ln >= 2:
                args.append((struct.unpack_from(">H", d, p)[0], p))
            if ln == 6 and op == 0x0A:       # u16 + u32
                args.append((struct.unpack_from(">I", d, p + 2)[0], p + 2))
            elif ln == 4 and op in (0x0b, 0x0c, 0x0d):
                args.append((struct.unpack_from(">H", d, p + 2)[0], p + 2))
            elif ln == 4:                   # u32 단독 (0x20/0x21)
                args = [(struct.unpack_from(">I", d, p)[0], p)]
            p += ln
        elif op < 0x50:                     # 인자 없는 메서드 (+ 타입별 추가 즉치)
            if p + 2 > n: break
            ref = struct.unpack_from(">H", d, p)[0]
            args.append((ref, p)); p += 2
            # m00(대입)·m01(더하기)는 int32 즉치를 하나 더 먹는다. 대상이 심볼이든
            # 내장 객체(`31 00 29 …`)든 마찬가지다 — 실측.
            if (op - 0x30) in (0x00, 0x01):
                if p + 4 > n: break
                args.append((struct.unpack_from(">I", d, p)[0], p)); p += 4
        elif op < 0x80:                     # 0x50-0x7F: u8 argc + argc×u16
            # 객체참조가 없다 — 대상은 직전에 해석된 객체다(핸들러가 바이트 하나를
            # 먼저 읽어 argc 로 쓴다. 0x409906 / 0x4099a1 / 0x409a3d 모두 동일).
            if p + 1 > n: break
            argc = d[p]; p += 1
            if p + argc * 2 > n: break
            for _ in range(argc):
                args.append((struct.unpack_from(">H", d, p)[0], p)); p += 2
        else:                               # 2바이트 표시 문자
            p += 1
        out.append((i, op, p - i, args, None))
        i = p
    return out


# ───────────────────────── 구조적 텍스트 추출 (번역 파이프라인 입력) ─────────────────────────
# 기존 휴리스틱(_sjis_runs, SJIS로 디코드되는 런 전부)을 대체한다. VNEG를 디스어셈블한
# 상태에서 **표시 텍스트 위치만** 뽑으므로 1바이트 제어코드/오퍼랜드 오탐이 없다.

def _has_disp(s):
    """화면에 그려질 문자가 있는가.

    엔진 리더는 **0x80 이상 바이트를 전부 표시 문자로** 본다(TEXT_RENDER §1).
    그러니 비ASCII 문자가 하나라도 있으면 그 조각은 화면에 나온다.

    예전 기준은 `U+3000 초과`였는데, 그러면 U+3000 **아래**에 있는 전각 기호가
    통째로 빠진다 — `…`(U+2026) · `―`(U+2015) · `♪`(U+266A) · `※`(U+203B) 등.
    그런 문자만으로 된 조각(`……*v` 같은 것)은 추출이 안 되니 번역도 주입도 안 되고,
    리드 비트맵을 CP949 로 바꾼 뒤에는 SJIS `81 63`(…)이 `갷`으로 나온다.
    실측: 神無ノ鳥 스태프롤 앞 물수제비 장면이 `통，통，통갷갷풍덩．`으로 보였다.
    """
    return any(ord(c) > 0x7F for c in s)

# 일본어 표시문자: 가나·한자·반복부호·전각영숫자/구두점 (반각가나·PUA 제외)
_JP_DISP = re.compile(r"[぀-ゟ゠-ヿ一-鿿々〆ー、-〕！-｠]")

def extract(d, resource_re=None):
    """VNEG .scn 구조적 텍스트 추출 → [{"id","kind","off","len","jp"[,"speaker"]}].

    표시 텍스트만 잡는다 (docs/formats/VNEG.md §4-2):
      - sym   : 심볼 테이블 op03 문자열 (화자명·선택지 옵션·타이틀 등). id="sym:<심볼idx>"
      - narr/dlg : flow 텍스트 오프너 `<symref:u16>` (symval[idx]가 **int 모드값**:
                   0=나레이션, 1=대사) 뒤 텍스트. 모드 심볼 인덱스는 씬마다 다르다
                   (`10 04`/`10 02`는 그 한 예). dlg는 직전
                   `0d 33 01 <symref:u16>` 화자 태깅.
      - quote : flow `0d 34 00` 뒤 여는 괄호 블록 (`「z` 등)
      - cmd   : flow 내 구조화 명령(op03)의 문자열 인자
      - cstr  : 오프너 텍스트가 전혀 없는 데이터 화면(.scn: music/scene 등)의
                널종료 문자열 배열 폴백
    flow 레코드 id="flow:<n>" (n=디코드 순서). off/len은 원본 파일 내 바이트 스팬.

    런 = 연속 2바이트 SJIS 쌍 + 인라인 printable(0x20–0x7e)/반각가나(0xa1–0xdf).
    엔진 리더가 텍스트 조립 중 만나는 1바이트 커맨드(`z` `%` `\\n` 등)를 포함하는데,
    주입 시 encode_piece 가 이를 원본 바이트 그대로 보존한다.
    resource_re: 리소스명 패턴(화자 오인 방지용, 문자열 또는 컴파일된 re).
    """
    res = re.compile(resource_re) if isinstance(resource_re, str) else resource_re
    recs = []
    if d[:4] != b"VNEG":
        return recs
    n = len(d)
    # flow의 모든 참조는 정의 순서가 아니라 **런타임 슬롯 인덱스**다. 아래 레거시
    # 심볼 추출 루프는 DB의 sym:<definition-id>만 보존하고, 모드/화자 해석과 실제
    # flow 경계는 전체 23타입·다원소 slots를 아는 parse_syms 결과를 쓴다.
    runtime_syms, parsed_flow_start = parse_syms(d)
    if parsed_flow_start == 0:
        return recs
    symval = {s: v for s, _off, _op, v in runtime_syms}

    # 심볼 테이블 (번역 DB의 기존 id를 보존하는 레거시 추출 규칙).
    # 다원소 정의를 런타임 슬롯으로 펼치는 parse_syms와 달리, 배열 데이터 화면은
    # 계속 아래 cstr 폴백으로 추출한다. 이 동작을 바꾸면 music/scene의 flow:* id가
    # sym:*로 대량 변경되므로 별도 DB 마이그레이션 작업에서만 손댄다.
    cnt = struct.unpack(">H", d[6:8])[0]; i = 8
    for s in range(cnt):
        if i+4 > n or d[i+1] != 0 or d[i+2] != 1: break
        op = d[i]; argc = d[i+3]; i += 4; ok = True
        for _ in range(argc):
            if i >= n: ok = False; break
            typ = d[i]; i += 1
            if typ == 0:
                e = d.find(b"\x00", i)
                if e < 0: ok = False; break
                raw = d[i:e]
                try: v = raw.decode("cp932")
                except Exception: v = None
                if v is not None and op == 0x03 and _has_disp(v):
                    recs.append({"id": "sym:%d" % s, "kind": "sym",
                                 "off": i, "len": len(raw), "jp": v})
                i = e + 1
            elif typ == 1:
                if i + 4 > n: ok = False; break
                i += 4
            else: ok = False; break
        if not ok: break
    legacy_scan_start = i
    flow_start = jumptable_end(d, parsed_flow_start)
    # 다원소 정의가 있는 music/scene류 데이터 화면에는 0x11xx 워드와 값 0/1이
    # 우연히 이어지는 배열 데이터가 있다. 이를 텍스트 모드 오프너로 보면 cstr
    # 폴백이 꺼져 기존 번역 레코드가 사라진다. 고인덱스 모드 오프너는 실제 확인된
    # 일반 스크립트(정의 수 == 런타임 슬롯 수)에서만 허용한다.
    allow_high_mode_ref = len(runtime_syms) == cnt

    # flow 1패스: 구조화 명령 영역 분리(문자열 인자는 cmd 레코드), 나머지 위치 수집
    pos = flow_start; buf = []
    while pos < n:
        r = parse_cmd(d, pos, n)
        if r is not None:
            op, args, p2 = r
            for tag, v, aoff in args:
                if tag != "s": continue
                try: sv = v.decode("cp932")
                except Exception: continue
                if op == 0x03 and _has_disp(sv):
                    recs.append({"id": None, "kind": "cmd",
                                 "off": aoff, "len": len(v), "jp": sv})
            pos = p2
        else:
            buf.append(pos); pos += 1

    # flow 2패스: 텍스트 블록 상태기계 + 화자 추적.
    #   블록 오픈/전환: `<symref:u16>` (symval이 int 0=나레이션 / 1=대사),
    #                    `0d 34 00`(quote)
    #   블록 유지(통과): 00/01(글루)·0x80(글리치)·printable(1바이트 커맨드)·
    #                    `0d XX`(네임윈도우류)·`<symref:u16>`·`04 XX 00`(클릭대기류)
    #   블록 종료: 그 외 제어 op, END(04 7f 00 74), `%`로 끝난 런(대사 종료)
    # 런(레코드)은 2바이트 SJIS 리드로 시작해야 한다 — 선행 1바이트는 오퍼랜드로 간주.
    m = len(buf); i = 0; spk = None; block = None
    def ref_at(k):
        if k < 0 or k + 1 >= m or buf[k + 1] != buf[k] + 1:
            return None
        return symref_index(struct.unpack_from(">H", d, buf[k])[0], len(runtime_syms))

    while i < m:
        p = buf[i]; b = d[p]
        # 화자 설정: 0d 33 01 <symref:u16>
        if b == 0x0d and i+4 < m and d[buf[i+1]] == 0x33 and d[buf[i+2]] == 0x01 \
           and ref_at(i+3) is not None:
            v = symval.get(ref_at(i+3))
            if isinstance(v, str) and v and not (res and res.match(v)):
                spk = v
            i += 5; continue
        # 여는 괄호 블록: 0d 34 00 (네임윈도우 닫기 직후)
        if b == 0x0d and i+2 < m and d[buf[i+1]] == 0x34 and d[buf[i+2]] == 0x00:
            block = "quote"; i += 3; continue
        ref = ref_at(i)
        if ref is not None and (ref < 0x100 or allow_high_mode_ref):
            v = symval.get(ref)
            if v == 0 or v == 1:                  # 모드 참조 → 블록 오픈/전환
                block = "narr" if v == 0 else "dlg"
                i += 2; continue
        if block is not None and ref is not None:
            i += 2; continue                      # 유효한 런타임 심볼 참조 — 블록 유지
        if block is not None:
            # 텍스트 런: printable 접두(≤4B, 인라인 1바이트 커맨드) 뒤 2바이트 리드로 시작
            w = 0
            while w < 4 and i+w < m and buf[i+w] == p+w and 0x20 <= d[p+w] < 0x7f:
                w += 1
            j = i + w
            if w < 4 and j+1 < m and buf[j] == p+w and buf[j+1] == p+w+1 \
               and is_lead(d[p+w]) and is_trail(d[p+w+1]):
                start = p; raw = bytearray(d[p:p+w]); i = j
                while i < m:
                    q = buf[i]; c = d[q]
                    if q != start + len(raw): break     # 명령 영역이 끼면 런 종료
                    if is_lead(c) and i+1 < m and buf[i+1] == q+1 and is_trail(d[q+1]):
                        raw += d[q:q+2]; i += 2
                    elif 0x20 <= c < 0x7f or 0xa1 <= c <= 0xdf:
                        raw.append(c); i += 1
                    else: break
                try: sv = raw.decode("cp932")
                except Exception: sv = None
                if sv and _has_disp(sv):
                    rec = {"id": None, "kind": block, "off": start, "len": len(raw), "jp": sv}
                    if block in ("dlg", "quote") and spk:
                        rec["speaker"] = spk
                    recs.append(rec)
                if raw and raw[-1] == 0x25:             # `%` 대사 종료(클릭 대기) → 블록 끝
                    block = None
                continue
            # 인라인 통과 바이트
            if b in (0x00, 0x01) or b == 0x80 or 0x20 <= b < 0x7f or 0x26 <= b <= 0x29:
                i += 1; continue
            if b == 0x0d and i+1 < m:                    # 네임윈도우류 0d XX
                i += 2; continue
            if b in (0x04, 0x12, 0x13, 0x14, 0x15) and i+2 < m \
               and d[buf[i+2]] == 0x00 and d[buf[i+1]] != 0x7f:  # 04 XX 00 클릭대기류 (END 제외)
                i += 3; continue
            block = None                                 # 그 외 제어 op → 블록 종료
        i += 1

    # 폴백: 오프너 텍스트가 전혀 없는 데이터 화면(.scn: music/scene/load 등)은
    # flow가 널종료 문자열 배열이다. 00 경계의 cp932 완전 디코드 + 표시문자 문자열만.
    # ⚠️ 이미 cmd(구조화 명령의 문자열 인자)로 잡힌 스팬과 겹치면 건너뛴다 —
    #    같은 문자열을 두 번 뽑으면 주입 때 둘째가 원본 불일치로 스킵된다.
    if not any(r["kind"] in ("narr", "dlg", "quote") for r in recs):
        taken = [(r["off"], r["off"] + r["len"]) for r in recs]
        p = legacy_scan_start
        while p < n:
            if d[p] == 0:
                p += 1; continue
            e = d.find(b"\x00", p)
            if e < 0: e = n
            raw = d[p:e]
            if 2 <= len(raw):
                try: sv = raw.decode("cp932")
                except Exception: sv = None
                # 제어문자 없는 완전한 문자열 + 일본어 표시문자(가나/한자/전각) 필수 —
                # 반각가나·PUA로만 디코드되는 오퍼랜드 뭉치를 배제한다.
                if sv and all(ord(c) >= 0x20 for c in sv) and _JP_DISP.search(sv)                    and not any(a < e and p < b for a, b in taken):     # 겹침 제외
                    recs.append({"id": None, "kind": "cstr",
                                 "off": p, "len": len(raw), "jp": sv})
            p = e + 1

    # 디코드(=오프셋) 순 정렬, flow 레코드에 순번 id 부여
    recs.sort(key=lambda r: r["off"])
    k = 0
    for r in recs:
        if r["id"] is None:
            r["id"] = "flow:%d" % k
        if r["kind"] != "sym":
            k += 1
    return recs

def fmt_args(args):
    parts = []
    for tag, v, _off in args:
        parts.append(str(v) if tag == "i" else '"%s"' % sdec(v))
    return ", ".join(parts)

def flush_flow(out, buf, symval=None, nlabels=0):
    if not buf: return
    i = 0; m = len(buf); pend = []; spk = [None]
    def word_at(k):
        if k < 0 or k + 1 >= m or buf[k + 1][0] != buf[k][0] + 1:
            return None
        return (buf[k][1] << 8) | buf[k + 1][1]
    def ref_at(k):
        value = word_at(k)
        return None if value is None else symref_index(value, len(symval or {}))
    def dump_hex():
        while pend:
            seg = pend[:24]
            hexs = " ".join("%02x" % b for _, b in seg)
            # 심볼 참조 힌트(휴리스틱): 0x1000+idx 중 '의미있는' 심볼(비어있지 않은
            # 문자열=이름·리소스·함수·씬)만 표시. 순차 파싱 밖의 raw view라 완벽하진 않음.
            # 심볼 참조를 앞 opcode로 함수콜/미디어 라벨링(휴리스틱).
            OPFN = {0x27:"MEDIA",0x28:"MEDIA",0x29:"MEDIA",0x13:"CALL",0x0e:"CALL",
                    0x24:"CALL",0x01:"ref",0x0b:"op0b",0x0d:"op0d"}
            refs = []
            for k in range(len(seg)-1):
                value = (seg[k][1] << 8) | seg[k + 1][1]
                idx = symref_index(value, len(symval or {}))
                if idx is not None and symval is not None:
                    v = symval.get(idx)
                    if isinstance(v, str) and v != "":
                        if is_resfile(v): continue  # 리소스는 전용 라인에 나옴(중복 억제)
                        opb = None
                        if k >= 1 and seg[k-1][1] in OPFN: opb = seg[k-1][1]
                        elif k >= 3 and seg[k-3][1] in (0x0b, 0x0d): opb = seg[k-3][1]
                        tag = (OPFN.get(opb, "op%02x" % opb) + " ") if opb is not None else ""
                        refs.append("%s%s(#%d)" % (tag, v, idx))
            cm = ("   ; " + ", ".join(refs)) if refs else ""
            out.write("        %06x  .   %s%s\n" % (seg[0][0], hexs, cm))
            del pend[:24]
    while i < m:
        off, b = buf[i]
        # 화자 추적: 0d 33 01 10 <화자심볼> 패턴
        # 화자/네임윈도우 설정 블록: 0d 32 00 01 00 0d 33 01 10 <화자> 01 00 0d 34 00
        if b==0x0d and i+14<m and buf[i+1][1]==0x32 and buf[i+5][1]==0x0d and buf[i+6][1]==0x33 \
           and ref_at(i+8) is not None and buf[i+12][1]==0x0d and buf[i+13][1]==0x34:
            spk[0]=ref_at(i+8)
            nm=symval.get(spk[0]) if symval else None
            dump_hex(); out.write("        %06x  NAME  %s\n"%(off,nm if isinstance(nm,str) and nm else "#%d"%spk[0])); i+=15; continue
        if b == 0x0d and i+4 < m and buf[i+1][1]==0x33 and buf[i+2][1]==0x01 \
           and ref_at(i+3) is not None:
            spk[0] = ref_at(i+3)
        # 분기 opcode: <jumpop> 00 <idx>  (idx=점프테이블 라벨 인덱스). op0x17 00 05 → IF≠0→L5
        if b in JUMPOP and i+2<m and buf[i+1][1]==0x00 and 0<buf[i+2][1]<nlabels:
            dump_hex(); out.write("        %06x  %-9s L%d\n"%(off,JUMPOP[b],buf[i+2][1])); i+=3; continue
        # IMM: 31 00 00 <int32:4 BE> = 표현식 파서 정수 리터럴(select 비교값 등). 심볼49(10 31)와 구분: 앞이 0x10 아님
        if b==0x31 and i+6<m and buf[i+1][1]==0 and buf[i+2][1]==0 \
           and ref_at(i-1) is None:
            val=(buf[i+3][1]<<24)|(buf[i+4][1]<<16)|(buf[i+5][1]<<8)|buf[i+6][1]
            dump_hex(); out.write("        %06x  %-9s %d\n"%(off,"IMM",val)); i+=7; continue
        # 함수콜/재생을 별도 라인으로.
        if symval is not None:
            def named(x): v=symval.get(x); return v if isinstance(v,str) and v else None
            # CALL func(arg): 0b <w1:2> 10 <arg> 13 10 <func>
            if b==0x0b and i+7<m and ref_at(i+3) is not None and buf[i+5][1]==0x13 \
               and ref_at(i+6) is not None and named(ref_at(i+6)):
                arg=ref_at(i+3); func=ref_at(i+6); av=named(arg)
                dump_hex(); out.write("        %06x  CALL  %s(#%d)%s\n"%(off,symval[func],arg,"  # "+av if av else "")); i+=8; continue
            # op0b/op0f 동작: 앞2바이트 마커(w1)로 판별 (VOICE/DRAW_FACE/SE/GOTO)
            if b in (0x0b, 0x0f) and i+4<m and ref_at(i+3) is not None \
               and named(ref_at(i+3)):
                idx = ref_at(i+3)
                kw=op_action(b, (buf[i+1][1]<<8)|buf[i+2][1])
                if kw:
                    dump_hex(); out.write("        %06x  %-9s %s(#%d)\n"%(off,kw,symval[idx],idx)); i+=5; continue
            # CALL func(): 13 10 <func>
            if b==0x13 and i+2<m and ref_at(i+1) is not None and named(ref_at(i+1)):
                idx = ref_at(i+1)
                dump_hex(); out.write("        %06x  CALL  %s()\n"%(off,symval[idx])); i+=3; continue
            # 리소스op: <op> <b1> <b2> 10 <res>  (심볼 접두사로 DRAW_BG/PLAY/DRAW_FACE/VOICE)
            if b in (0x12,0x14,0x15,0x27,0x28,0x29) and i+4<m \
               and ref_at(i+3) is not None and named(ref_at(i+3)):
                idx=ref_at(i+3); rn=symval[idx]; kw=reskw(rn) or "PLAY"
                dump_hex(); out.write("        %06x  %-9s %s(#%d)\n"%(off,kw,rn,idx)); i+=5; continue
        # WAIT(클릭대기 %, op0x25=2B오퍼랜드) / END(텍스트 프레임 종료)
        if b == 0x25:
            dump_hex(); out.write("        %06x  WAIT\n" % off); i += 3; continue
        if b == 0x04 and i+3 < m and buf[i+1][1]==0x7f and buf[i+2][1]==0x00 and buf[i+3][1]==0x74:
            dump_hex(); out.write("        %06x  END\n" % off); i += 4; continue
        if is_lead(b) and i+1 < m and is_trail(buf[i+1][1]):
            # 오프너 검사: 진짜 텍스트는 모드 심볼(값 0/1) 참조나 0d 34 00 뒤에만.
            # 그 외 SJIS로 읽히는 건 flow 오퍼랜드 오탐(钁 등) → hex 처리.
            p1 = buf[i-1][1] if i >= 1 else -1
            p2 = buf[i-2][1] if i >= 2 else -1
            p3 = buf[i-3][1] if i >= 3 else -1
            label = None
            mode = ref_at(i-2)
            if mode is not None and symval and symval.get(mode) == 0: label = "NARR"
            elif mode is not None and symval and symval.get(mode) == 1: label = "DLG"
            elif p3 == 0x0d and p2 == 0x34 and p1 == 0x00: label = "QUOTE"
            if label:
                run = bytearray(); start = off
                while i+1 < m and is_lead(buf[i][1]) and is_trail(buf[i+1][1]):
                    run += bytes([buf[i][1], buf[i+1][1]]); i += 2
                sp = ""
                if label != "NARR" and symval and isinstance(symval.get(spk[0]), str) and symval.get(spk[0]):
                    sp = "  <화자 #%d=%s>" % (spk[0], symval[spk[0]])
                dump_hex(); out.write("        %06x  %-5s %s%s\n" % (start, label, sdec(run), sp)); continue
            # 오프너 없음 → 텍스트 아님. 아래로 떨어져 hex(pend)로 처리.
        if 0x30 <= b <= 0x7a:
            j = i; run = bytearray()
            while j < m:
                c = buf[j][1]
                if 0x30<=c<=0x39 or 0x41<=c<=0x5a or 0x61<=c<=0x7a or c in (0x5f,0x2e):
                    run.append(c); j += 1
                else: break
            if len(run) >= 3:
                dump_hex(); out.write("        %06x  id  %s\n" % (off, run.decode("latin1"))); i = j; continue
        pend.append((off, b)); i += 1
    dump_hex()

def disasm(d, opstat=None):
    out = io.StringIO(); n = len(d)
    is_vneg = d[:4] == b"VNEG"
    syms, flow_start = parse_syms(d)
    symval = {s: v for s, _, _, v in syms}
    labels = {}  # 점프 타겟 주소 -> [라벨명]
    nlabels = 0  # 점프테이블 엔트리 수 (분기 op의 라벨 인덱스 범위)
    if is_vneg:
        ndefs = struct.unpack(">H", d[6:8])[0] if n >= 8 else 0
        out.write("# VNEG  size=%d  defs=%d  runtime_symbols=%d  flow_start=0x%x\n\n"
                  % (n, ndefs, len(syms), flow_start))
        if flow_start == 0:
            out.write("!!! SYMBOL TABLE PARSE FAILED !!!\n")
            return out.getvalue()
        # 심볼 테이블
        out.write("=== SYMBOLS (%d) ===\n" % len(syms))
        for s, st, op, v in syms:
            tag = ""
            if isinstance(v, str) and v[:5].isascii() and ("_0" in v or v.endswith(("a","b","c"))) and len(v)<=10 and any(ch.isdigit() for ch in v):
                pass
            out.write("  #%-3d @0x%-4x %-11s %r\n" % (s, st, OP_TYPES.get(op, "OP%02x" % op), v))
        # 점프/라벨 테이블
        if flow_start + 10 <= n:
            N = struct.unpack(">H", d[flow_start:flow_start+2])[0]
            if 0 < N < 64:
                nlabels = N
                out.write("\n=== JUMP TABLE @0x%x (N=%d, 타겟=flow_start+off) ===\n" % (flow_start, N))
                for e in range(N):
                    off = struct.unpack(">H", d[flow_start+8+e*4:flow_start+8+e*4+2])[0]
                    if e == 0:
                        out.write("  [0] 0x%x  (제어코드/상수)\n" % off); continue
                    tgt = flow_start + off; lbl = "L%d" % e
                    labels.setdefault(tgt, []).append(lbl)
                    hint = ""
                    idx = None
                    if tgt + 2 <= n:
                        idx = symref_index(struct.unpack_from(">H", d, tgt)[0], len(syms))
                    if idx is not None:
                        hint = "-> SYM#%d %r" % (idx, symval.get(idx))
                    else:
                        j = tgt
                        while j < min(tgt+40, n-1):
                            if is_lead(d[j]) and is_trail(d[j+1]):
                                run = bytearray()
                                while j+1 < n and is_lead(d[j]) and is_trail(d[j+1]): run += d[j:j+2]; j += 2
                                hint = 'text:"%s"' % sdec(run)[:24]; break
                            j += 1
                    out.write("  [%d] %-5s off=0x%-4x -> @0x%-4x  %-24s | %s\n" % (
                        e, lbl, off, tgt, hint, " ".join("%02x" % x for x in d[tgt:tgt+8])))
        out.write("\n=== FLOW ===\n")
    # flow_start에는 점프테이블이 있고 실제 명령은 그 뒤에서 시작한다. 예전 구현은
    # 점프테이블을 위에서 해석한 뒤 FLOW에서 다시 hex로 출력했다.
    pos = jumptable_end(d, flow_start) if is_vneg else 0
    flow = []
    while pos < n:
        if pos in labels:
            flush_flow(out, flow, symval, nlabels); flow = []
            out.write("%06x  %s:\n" % (pos, "/".join(labels[pos])))
        r = parse_cmd(d, pos, n)
        if r is not None:
            op, args, p2 = r
            flush_flow(out, flow, symval, nlabels); flow = []
            out.write("%06x  %-6s %s\n" % (pos, OPNAME.get(op, "OP%02x" % op), fmt_args(args)))
            if opstat is not None: opstat[op] += 1
            pos = p2
        else:
            flow.append((pos, d[pos])); pos += 1
    flush_flow(out, flow, symval, nlabels)
    return out.getvalue()

DEFAULT_ARCHIVES = ["scenario.axr", "scenario.ax2", "scenario.ax3", "scenario.ax4"]


def load_axr_tool(tools_dir=None):
    """AXRe 모듈(miris.axr) 임포트 — 패키지/단독실행 모두 지원."""
    try:
        from . import axr as A            # 패키지로 import된 경우
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import axr as A                   # 단독 실행 (python miris/vneg.py)
    return A


def run_one(A, path, outdir, opstat):
    """한 .scn 파일 또는 아카이브를 디스어셈블. 파일이면 그거만, 폴더 아카이브면 전 .scn."""
    total = 0
    if path.lower().endswith(".scn"):        # 단일 .scn 파일 직접
        with open(path, "rb") as src:
            scn = src.read()
        txt = disasm(scn, opstat)
        dst = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".txt")
        os.makedirs(outdir, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as out:
            out.write(txt)
        return 1
    d, e, t = A.load(path)                    # 아카이브
    # scenario.axr/ax2/ax3/ax4는 stem이 모두 scenario라 예전에는 같은 폴더를
    # 덮어썼다. 확장자까지 보존해 네 레이어의 569개 씬을 모두 남긴다.
    vd = os.path.basename(path).replace(".", "_")
    odir = os.path.join(outdir, vd); os.makedirs(odir, exist_ok=True)
    for name, off, sz in e:
        if not name.endswith(".scn"):
            continue
        txt = disasm(A.getfile(d, t, off, sz), opstat)
        with open(os.path.join(odir, os.path.splitext(name)[0] + ".txt"),
                  "w", encoding="utf-8") as out:
            out.write(txt)
        total += 1
    return total


def main():
    ap = argparse.ArgumentParser(prog="ujyu scn disasm",
                                 description="VNEG(.scn) 디스어셈블러")
    ap.add_argument("inputs", nargs="+",
                    help="입력: 아카이브(.axr/.ax2..) 또는 .scn 파일들")
    ap.add_argument("-o", "--out", default="scn_disasm_out",
                    help="출력 디렉토리 (기본: ./scn_disasm_out)")
    ap.add_argument("--tools-dir", default=None, help="archive.py 위치 (기본: 스크립트 폴더)")
    ap.add_argument("--src-dir", default=None,
                    help="입력을 파일명만 준 경우 붙일 디렉토리 (예: 원본 게임 폴더)")
    args = ap.parse_args()
    A = load_axr_tool(args.tools_dir)
    opstat = Counter(); total = 0
    for inp in args.inputs:
        path = inp if (os.path.isabs(inp) or args.src_dir is None) else os.path.join(args.src_dir, inp)
        if not os.path.isfile(path):
            print("  없음, 스킵:", path); continue
        total += run_one(A, path, args.out, opstat)
    print("완료: %d개 .scn 디스어셈블 -> %s" % (total, args.out))
    for op, c in opstat.most_common():
        print("  OP%02x %-11s : %d" % (op, OP_TYPES.get(op, ""), c))


# ───────────────────────── 점프테이블 재매핑 (재패킹 필수 단계) ─────────────────────────
# 근거: docs/formats/VNEG.md (점프/라벨 테이블 절). 번역 패처가 텍스트만 치환하고 flow
# 시작부 점프/라벨 테이블의 flow상대 오프셋을 안 갱신 → 길이 밀림 → 무비/선택지 hang.

def jt_flowstart(scn):
    """심볼 테이블 끝(=flow 시작) 오프셋. parse_syms와 동일 규칙의 경량판."""
    if scn[:4] != b"VNEG": return None
    ndefs = int.from_bytes(scn[6:8], "big"); i = 8
    for _ in range(ndefs):
        if i + 4 > len(scn) or scn[i] not in OP_TYPES \
           or int.from_bytes(scn[i+1:i+3], "big") < 1:
            return None
        argc = scn[i+3]; i += 4
        for a in range(argc):
            if i >= len(scn): return None
            typ = scn[i]; i += 1
            if typ == 0:
                e = scn.find(b"\x00", i)
                if e < 0: return None
                i = e + 1
            elif typ == 1:
                if i + 4 > len(scn): return None
                i += 4
            else: return None
    return i

def _tokenize(buf, lead, trail):
    toks = []; i = 0; n = len(buf)
    while i < n:
        if lead(buf[i]) and i+1 < n and trail(buf[i+1]):
            s = i
            while i+1 < n and lead(buf[i]) and trail(buf[i+1]): i += 2
            toks.append((s, 'T'))
        else:
            toks.append((i, buf[i])); i += 1
    return toks

def _build_map(jp, kr):
    """원본 오프셋 → 번역 오프셋 매핑 (SJIS/CP949 토큰 difflib 정렬)."""
    sj = lambda b: 0x81<=b<=0x9f or 0xe0<=b<=0xfc; st = lambda b: 0x40<=b<=0x7e or 0x80<=b<=0xfc
    cl = lambda b: 0x81<=b<=0xfe; ct = lambda b: (0x41<=b<=0x5a)or(0x61<=b<=0x7a)or(0x81<=b<=0xfe)
    jt = _tokenize(jp, sj, st); kt = _tokenize(kr, cl, ct)
    sm = difflib.SequenceMatcher(None, [t[1] for t in jt], [t[1] for t in kt], autojunk=False)
    tokmap = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2-i1): tokmap[i1+k] = j1+k
    jstart = [t[0] for t in jt]
    def m(P):
        ti = bisect.bisect_right(jstart, P) - 1
        if ti in tokmap: return kt[tokmap[ti]][0] + (P - jt[ti][0])
        return None
    return m

def _edit_span(edits):
    """치환 목록 -> `P 가 치환 구간 안이면 (새 시작, 새 끝), 아니면 None` 함수.

    구간 내부를 가리키는 타겟은 JP 바이트 대조로 검증할 수 없다(번역으로 바이트가
    바뀌었으니 원리적으로 불일치다). 대신 매핑 결과가 **그 구간의 새 범위 안**에
    있는지를 본다.
    """
    if not edits:
        return lambda P: None
    es = sorted(edits)
    starts = [e[0] for e in es]
    cum = []; acc = 0
    for off, ol, nl in es:
        acc += nl - ol
        cum.append(acc)
    def span(P):
        i = bisect.bisect_right(starts, P) - 1
        if i >= 0 and P < es[i][0] + es[i][1]:
            lo = es[i][0] + (cum[i - 1] if i else 0)
            return lo, lo + es[i][2]
        return None
    return span


def _map_from_edits(edits):
    """치환 목록 [(원본오프셋, 원본길이, 새길이)] -> 원본오프셋 → 번역오프셋 함수.

    주입기가 **어디를 몇 바이트로 바꿨는지 이미 알고 있으므로** difflib 정렬이 필요 없다.
    새 오프셋 = 원본 오프셋 + (그 앞에서 일어난 길이 변화의 합). 정확하고 O(log n).
    """
    if not edits:
        return lambda P: P
    es = sorted(edits)
    starts = [e[0] for e in es]
    cum = []; acc = 0
    for off, ol, nl in es:
        acc += nl - ol
        cum.append(acc)
    def m(P):
        i = bisect.bisect_right(starts, P) - 1
        if i >= 0 and P < es[i][0] + es[i][1]:
            # 치환 구간 **내부**를 가리키는 타겟: 실측상 이런 항목은 텍스트 뒤에 오는
            # END 명령에 붙은 앵커다(조사 130건 전부 문장 끝 1~15바이트 이내).
            # 그래서 시작이 아니라 **끝에서의 거리를 보존**한다 — difflib 폴백과 동일 규칙.
            old_end = es[i][0] + es[i][1]
            return old_end + cum[i] - (old_end - P)
        return P + (cum[i] if i >= 0 else 0)
    return m


def relocate_jumptable(kr, jp, edits=None):
    """번역 씬 kr의 점프/라벨 테이블 오프셋을 원본 jp 기준으로 재매핑. -> (bytes, fixed, fail).

    edits 를 주면 **정확 매핑**(_map_from_edits)을 쓴다 — 주입기가 아는 치환 정보라
    difflib 정렬이 아예 필요 없다. 없으면 difflib 경로(_build_map, 느린 레거시).

    검증 규칙이 타겟 위치에 따라 다르다:
      · 치환 구간 **밖** — KR 바이트가 JP 와 같아야 한다. 8바이트 대조로 확인.
      · 치환 구간 **안** — 번역으로 바이트가 바뀌었으니 대조가 원리적으로 불가능하다.
        구조적 규칙(끝에서의 거리 보존)이 답이고, 결과가 그 구간의 새 범위 안에
        있는지만 본다.
    예전에는 후자에서 difflib(raw 바이트 SequenceMatcher)로 폴백했는데, 실측 결과
    폴백 110건 중 108건이 이 경우였고 빌드 시간의 99.5%를 먹었다. 갈리는 10건을
    조사하니 구조적 매핑은 10/10 이 유효한 명령 경계였고 difflib 은 7/10 이었다
    (나머지는 2바이트 문자 중간에 착지 — 바이트 정렬이 우연히 맞은 것). 그래서 폴백을
    없앴다.
    """
    if kr[:4] != b"VNEG" or jp[:4] != b"VNEG": return kr, 0, 0
    fk = jt_flowstart(kr); fj = jt_flowstart(jp)
    if fk is None or fj is None or fk+10 > len(kr) or fj+10 > len(jp):
        return kr, 0, 0
    nk = struct.unpack(">H", kr[fk:fk+2])[0]
    nj = struct.unpack(">H", jp[fj:fj+2])[0]
    if not (0 < nk < 64) or not (0 < nj < 64):
        return kr, 0, 0
    if nk != nj or fk + 8 + nk * 4 > len(kr) or fj + 8 + nj * 4 > len(jp):
        return kr, 0, max(nk, nj) - 1
    N = nj
    if edits is not None:
        mp = _map_from_edits(edits); in_edit = _edit_span(edits)
    else:
        mp = _build_map(jp, kr); in_edit = None
    krb = bytearray(kr); fixed = fail = 0
    for e in range(1, N):
        pos = fk + 8 + e*4
        # 기준 오프셋은 언제나 JP 원본 테이블에서 읽는다. KR 테이블은 이미 한 번
        # 재매핑됐을 수 있으므로 여기서 읽으면 재실행할 때 이동량이 누적된다.
        jpos = fj + 8 + e*4
        O = struct.unpack(">H", jp[jpos:jpos+2])[0]
        tj = fj + O
        if not 0 <= tj < len(jp):
            fail += 1
            continue
        tk = mp(tj)
        if tk is None or not (0 <= tk - fk < 0x10000) or not (0 <= tk <= len(krb)):
            fail += 1
            continue
        if in_edit is not None:
            # 치환 정보가 있으면 매핑이 **산술적으로 정확**하다(앞선 길이 변화의 합).
            # 구간 안을 가리키는 타겟만 새 범위에 들어오는지 확인한다.
            sp = in_edit(tj)
            if sp is None or sp[0] <= tk <= sp[1]:
                krb[pos:pos+2] = struct.pack(">H", tk-fk); fixed += 1
            else:
                fail += 1
            continue
        # difflib 경로(치환 정보 없음)는 정렬이 우연히 맞을 수 있어 원본 8바이트를 대조한다.
        span = min(8, len(jp) - tj)
        if span > 0 and tk + span <= len(krb) and jp[tj:tj+span] == bytes(krb[tk:tk+span]):
            krb[pos:pos+2] = struct.pack(">H", tk-fk); fixed += 1
        else:
            fail += 1
    return bytes(krb), fixed, fail

def relocate_archive(archive, jp_dir):
    """번역 아카이브의 모든 .scn 점프테이블을 원본(jp_dir 동명 아카이브) 기준으로 in-place 재매핑."""
    A = load_axr_tool()
    jp_arc = os.path.join(jp_dir, os.path.basename(archive))
    if not os.path.exists(jp_arc):
        print("  원본 아카이브 없음(%s) → 건너뜀" % jp_arc); return
    dj, ej, tj = A.load(jp_arc); byj = {n: (o, s) for n, o, s in ej}
    dk, ek, tk = A.load(archive)
    files = []; tot = fixt = failt = 0
    for n, o, s in ek:
        kr = A.getfile(dk, tk, o, s)
        if n.endswith(".scn") and n in byj:
            nk, f, fa = relocate_jumptable(kr, A.getfile(dj, tj, *byj[n])); files.append((n, nk))
            tot += 1; fixt += f; failt += fa
        else:
            files.append((n, kr))
    blk2 = int.from_bytes(open(archive, "rb").read()[8:12], "little")
    open(archive, "wb").write(A.pack(files, blk2))
    print("  %s: 씬 %d, 오프셋수정 %d, 검증실패 %d" % (os.path.basename(archive), tot, fixt, failt))


if __name__ == "__main__":
    main()
