# -*- coding: utf-8 -*-
"""`.scn` 의 좌표·크기를 자동으로 찾아 ×N 한다 (메뉴·세이브·설정 화면).

명시 치수로 그리는 화면(RESOLUTION.md §4)의 좌표는 세 군데에 있다:

1. **객체 심볼 정의**의 앞 인자 — `<op> <원소수:2BE> <argc> x y w h …`
   layer/button 은 x,y,w,h / textwindow 는 x,y,w,h + 여백 L,T,R,B.
2. **객체 메서드 호출**의 인자 — `01 <객체ref:u16> <메서드> <인자수> <심볼ref:u16>*`
   어느 인자가 좌표인지는 (타입, 메서드)로 정해진다 → `RULES`.
3. **변수 대입 즉치** — `0a 10 <심볼> <int32 BE>`. 목록의 행 좌표처럼 런타임에
   넣는 값이 여기 있다. 그 변수가 좌표 자리에만 쓰이면 즉치도 좌표다.
4. **행 배열(좌표 테이블)** — 목록 화면(음악 감상·장면 회상)의 행 사각형은 flow 에서
   참조되지 않는 int 슬롯이 죽 늘어선 표다. flow 는 `0c 00 00 <base심볼ref>` +
   `38 00 00 <행인덱스var>` 로 "base + 행×stride" 심볼참조를 계산해 꺼낸다 → `_tables()`.
5. **격자 원점** — `38 <좌표변수> <상수심볼>`. 표에서 꺼낸 행 좌표는 창 안 상대값이라
   그리기 직전에 격자 원점을 더한다(장면 회상: 표의 (0,288) + 원점 #144/#145 = (47,82)).
   더해지는 상수도 좌표다 → `_origins()`. 안 키우면 목록만 좌상단으로 밀린다.

한 심볼이 좌표 자리와 **비좌표 자리**(버튼 id 등)에 같이 쓰이는 일이 있다 — 값이
같으면 컴파일러가 공유하기 때문이다. 그런 심볼은 그냥 ×N 하면 id 가 망가지므로,
**미사용 int 심볼을 재활용**해 좌표 쪽 참조만 그리로 돌린다(길이 불변). 여분이
없으면 건너뛰고 경고한다.

  python ujyu/scn_dims.py <파일.scn> [...]                 # 무엇을 바꿀지 보고만
  python ujyu/scn_dims.py --archive <아카이브> <이름.scn>    # 아카이브에서 꺼내 분석

실제 적용은 `ujyu scale dims` (config 의 `SCN_DIMS_AUTO` 목록) 가 한다.
"""
import argparse, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass
from ujyu.formats import vneg, axr

TYPE = vneg.OP_TYPES

# (객체 타입, 메서드) -> 좌표인 인자 번호. 실측(神無ノ鳥 save/load/config).
RULES = {
    ('button', 0x02):     (0, 1, 2, 3),               # x, y, w, h
    ('textwindow', 0x10): (0, 1, 2, 3, 4, 5, 6, 7),   # x, y, w, h, 여백 L/T/R/B
    ('textwindow', 0x41): (0, 1),                     # 텍스트 그리기 x, y
    ('textwindow', 0x18): (0, 1),                     # 선택지 버튼 치수
    ('textwindow', 0x07): (0, 1),                     # 글꼴 크기, 줄 높이(생략 가능)
    # 애니메이션 — **마지막 인자는 시간(ms)이라 건드리면 안 된다.**
    # 좌표만 ×N 하면 이동 거리와 창이 같이 커져 보이는 속도는 그대로다.
    ('layer', 0x05):      (0, 1),                     # (x, y, ms) 로 이동
    ('textwindow', 0x20): (0,),                       # 스크롤 위치 즉시 설정
    ('textwindow', 0x40): (0,),                       # (y, ms) 로 스크롤
}
# 객체 심볼 정의에서 ×N 할 앞쪽 int 인자 개수
DEF_RULES = {'layer': 4, 'button': 4, 'textwindow': 8, 'textwindow2': 8,
             'scroll': 4}

# 갈래별 토글 — 어느 쪽이 문제인지 가릴 때 끈다(격리 테스트용)
DO_ASSIGNS = True     # 3. 변수 즉치(대입·더하기)
DO_REPOINT = True     # 공유 심볼을 새 심볼로 분리
DO_TABLES = True      # 4. 행 배열(좌표 테이블)
DO_ORIGINS = True     # 5. 좌표 변수에 더해지는 격자 원점


def calls(d, fs=None):
    """객체 메서드 호출 -> [(off, 객체심볼, 메서드, [(심볼idx, 바이트오프셋)])]

    `miris.vneg.walk()` 로 flow 를 **순차 파싱**해서 뽑는다(예전 바이트 패턴 스캔은
    명령 경계를 놓쳐 엉뚱한 값을 좌표로 잡았다). 단, 아직 피연산자가 미확정인
    scratch-object 메서드 뒤에서 walk 경계가 잠깐 어긋날 수 있으므로, 실제 객체 타입·
    알려진 메서드·모든 심볼 인자 타입까지 검증되는 명시 호출만 보수적으로 합친다.
    심볼이 아닌 인자는 -1 로 둔다.
    """
    syms, parsed_fs = vneg.parse_syms(d)
    if fs is None:
        fs = parsed_fs
    op_of = {i: op for i, _o, op, _v in syms}
    out = {}

    def add(off, obj, meth, args):
        out.setdefault(off, (off, obj, meth, args))

    for off, op, _ln, args, meth in vneg.walk(d):
        if op in (0x01, 0x02):
            ref = args[0][0]
            obj = vneg.symref_index(ref, len(syms))
            if obj is None:
                continue
            rest = []
            for value, arg_off in args[1:]:
                idx = vneg.symref_index(value, len(syms))
                rest.append((idx if idx is not None else -1, arg_off))
            add(off, obj, meth, rest)

    # 명시 호출 인코딩: 01/02 <객체:u16> <method:u8> <argc:u8> <args:u16...>.
    # 좌표 규칙이 있는 실제 객체/메서드이고 모든 인자가 유효한 심볼일 때만 채택하므로
    # 호출 인자나 텍스트 안의 우연한 바이트열은 들어오지 않는다.
    n = len(d)
    for off in range(fs, n - 4):
        if d[off] not in (0x01, 0x02):
            continue
        ref = struct.unpack_from(">H", d, off + 1)[0]
        obj = vneg.symref_index(ref, len(syms))
        if obj is None:
            continue
        typ = TYPE.get(op_of.get(obj))
        meth, argc = d[off + 3], d[off + 4]
        coord = RULES.get((typ, meth))
        end = off + 5 + argc * 2
        # 뒤쪽 인자가 생략 가능한 메서드가 있다(m07 은 줄 높이를 빼면 argc=1). 좌표
        # 자리가 하나도 없을 때만 거른다.
        if not coord or end > n or not any(c < argc for c in coord):
            continue
        rest = []
        valid = True
        for p in range(off + 5, end, 2):
            v = struct.unpack_from(">H", d, p)[0]
            idx = vneg.symref_index(v, len(syms))
            if idx is None or idx not in op_of:
                valid = False
                break
            rest.append((idx, p))
        if not valid or any(op_of.get(rest[k][0]) != 0x02 for k in coord if k < argc):
            continue
        add(off, obj, meth, rest)
    return [out[k] for k in sorted(out)]


# 변수에 즉치를 넣는 명령: 0a=대입, 30=더하기(열 이동에 쓴다 — 실측 save.scn)
VAR_IMM_OPS = (0x0A, 0x30)


def assigns(d, fs):
    """`<op> <심볼ref:u16> <int32 BE>` 변수 즉치 -> [(심볼, 값, 값오프셋)]"""
    out, i, n = [], fs, len(d)
    while i < n - 7:
        if d[i] in VAR_IMM_OPS:
            idx = vneg.symref_index(struct.unpack_from(">H", d, i + 1)[0])
        else:
            idx = None
        if idx is not None:
            v = struct.unpack_from(">i", d, i + 3)[0]
            out.append((idx, v, i + 3)); i += 7; continue
        i += 1
    return out


# ─────────────────────────────────────────── 4. 행 배열(좌표 테이블)
# 목록 화면(음악 감상·장면 회상)의 행 사각형은 flow 에서 직접 참조되지 않는 int 슬롯이
# 죽 늘어선 표다. flow 는 행마다 이렇게 필드 포인터를 만든다:
#
#   0c 00 00 <base:u16>            누산기 = base 심볼참조
#   38 00 00 <행인덱스var:u16>      누산기 += 행인덱스 (행인덱스 = 행 × stride)
#   0d <필드0var:u16> 00 00         필드 0 포인터 저장
#   30 00 00 <int32 = 1>            누산기 += 1
#   0d <필드1var:u16> 00 00         필드 1 …           (필드 수 = 행 폭 stride)
#
# 그러니 **stride 를 추측할 필요가 없다** — 필드 포인터 변수의 개수가 곧 stride 이고,
# 그 변수가 어느 인자 자리에 쓰이는지(RULES)가 곧 좌표 여부다. 다만 컴파일러가 같은
# 임시 변수를 여기저기 재활용하므로, 쓰임은 **그 사이트에서 다음 대입 전까지**만 본다
# (음악 감상의 #337 은 어떤 구간에선 곡별 글꼴 크기, 다른 구간에선 버튼 id 다).
IDX_LOAD, IDX_ADD = 0x0C, 0x38          # 즉치적재 / 변수더하기
FLD_STORE, FLD_NEXT = 0x0D, 0x30        # 변수저장 / 누산기 += 즉치
VAR_SET = (0x0A, 0x0B, 0x0D)            # 변수를 덮어쓰는 명령 (대입 / 복사 / 저장)

TBL_MIN_ROWS = 3        # 이보다 적으면 표로 보지 않는다


def _table_fields(d, fs, nsyms):
    """위 관용구를 찾아 -> [(base심볼, [(필드var, 그 필드가 유효한 구간 끝)], 사이트끝)]"""
    out, i, n = [], fs, len(d)
    while i < n - 8:
        if d[i] != IDX_LOAD or d[i + 1] or d[i + 2]:
            i += 1; continue
        base = vneg.symref_index(struct.unpack_from(">H", d, i + 3)[0], nsyms)
        p = i + 5
        while base is not None and p + 5 <= n and d[p] == IDX_ADD and not d[p+1] and not d[p+2]:
            if vneg.symref_index(struct.unpack_from(">H", d, p + 3)[0], nsyms) is None:
                break
            p += 5
        if base is None or p == i + 5:
            i += 1; continue
        fields = []
        while p + 5 <= n and d[p] == FLD_STORE:
            v = vneg.symref_index(struct.unpack_from(">H", d, p + 1)[0], nsyms)
            if v is None:
                break
            fields.append(v); p += 5
            if not (p + 7 <= n and d[p] == FLD_NEXT
                    and struct.unpack_from(">i", d, p + 3)[0] == 1):
                break
            p += 7
        if fields:
            out.append((base, fields, p))
        i += 1
    return out


def _var_dead(d, var, start, nsyms):
    """`var` 가 start 이후 처음 덮어써지는 오프셋 (없으면 파일 끝)."""
    ref = (vneg.SYMREF | var).to_bytes(2, "big")
    i = start
    while i < len(d) - 3:
        if d[i] in VAR_SET and d[i + 1:i + 3] == ref:
            return i
        i += 1
    return len(d)


def _uses(d, syms, sargs, fs, refs_out=None):
    """심볼이 어느 인자 자리에 쓰이는지 -> (scal, scal_refs, other)

    scal  = {심볼: {(타입, 메서드, 인자번호)}}  좌표 자리
    other = 같은 꼴, 좌표가 아닌 자리 (버튼 id 등)
    scal_refs = {심볼: [좌표 자리 참조의 바이트오프셋]}

    `refs_out` 에 dict 를 주면 {심볼: [(바이트오프셋, 좌표인가)]} 로 모든 인자 참조를
    받는다 — 같은 변수가 구간마다 다른 뜻으로 재활용될 때 가르는 데 쓴다(_tables).
    """
    op_of = {i: op for i, _o, op, _v in syms}
    scal, scal_refs, other = {}, {}, {}
    # 객체·인자 인덱스가 실제 심볼 범위 안이고 객체 타입이 그릴 수 있는 것일 때만
    # 받는다. 알 수 없는 오버로드는 좌표 자리가 int가 아닐 때 아래에서 제외한다.
    OBJ_T = ('layer', 'button', 'textwindow', 'textwindow2')
    for _off, obj, meth, args in calls(d, fs):
        t = TYPE.get(op_of.get(obj))
        if t not in OBJ_T or any(a >= len(syms) for a, _b in args):
            continue
        idxs = RULES.get((t, meth), ())
        # 좌표 자리에 int 심볼이 아닌 것(문자열·리터럴)이 하나라도 있으면 이 호출은
        # 우리가 아는 시그니처가 아니다(오버로드). 손대면 이미지 인자를 망가뜨린다.
        if any(op_of.get(a) != 0x02 for k, (a, _b) in enumerate(args) if k in idxs):
            continue
        for k, (a, boff) in enumerate(args):
            if k in idxs:
                scal.setdefault(a, set()).add((t, meth, k))
                scal_refs.setdefault(a, []).append(boff)
            else:
                other.setdefault(a, set()).add((t, meth, k))
            if refs_out is not None:
                refs_out.setdefault(a, []).append((boff, k in idxs))
    return scal, scal_refs, other


def tables(d):
    """`_tables()` 를 파일 하나에 대해 부르는 겉면 (보고용)."""
    sargs = {}
    syms, fs = vneg.parse_syms(d, args_out=sargs)
    if not syms or not DO_TABLES:
        return []
    refs = {}
    scal, _sr, other = _uses(d, syms, sargs, fs, refs_out=refs)
    return _tables(d, syms, sargs, fs, scal, other, refs)


def _tables(d, syms, sargs, fs, scal, other, refs):
    """-> [(base, stride, rows, 좌표필드들, [(값오프셋, 값), ...])]

    표의 몸통은 base 부터 이어지는 "인자가 int 하나뿐인 슬롯"이다. 원소는 포인터를
    계산해 꺼내므로 **메서드 인자로 직접 쓰인 슬롯이나 다른 표의 base 를 만나면 거기서
    끝난다** (원시 u16 바이트 스캔은 즉치·텍스트에서 오탐이 나 쓰지 않는다). 남는
    꼬리는 행 단위로 자른다.

    좌표 필드는 그 필드 포인터 변수가 **그 사이트 구간 안에서** 좌표 자리에만 쓰인
    것으로 한정한다. 임시 변수는 재활용되므로 전역으로 보면 거의 다 섞여 버린다.
    """
    one_int = {}
    for i, _o, op, _v in syms:
        a = sargs.get(i, [])
        if op == 0x02 and len(a) == 1 and a[0][0] == "i":
            one_int[i] = (a[0][1], a[0][2])       # (값, 값오프셋)
    sites = _table_fields(d, fs, len(syms))
    bases = set(b for b, _f, _e in sites)
    used = set(scal) | set(other)

    out = []
    for base, fields, site_end in sites:
        if base not in one_int:
            continue
        st = len(fields)
        run, k = [], base
        while k in one_int and (k == base or (k not in used and k not in bases)):
            run.append(one_int[k]); k += 1
        rows = len(run) // st
        if rows < TBL_MIN_ROWS:
            continue
        coord = []
        for f, v in enumerate(fields):
            end = _var_dead(d, v, site_end, len(syms))
            here = [ok for boff, ok in refs.get(v, []) if site_end <= boff < end]
            if here and all(here):
                coord.append(f)
        if not coord:
            continue
        ents = [run[r * st + f] for r in range(rows) for f in coord]
        out.append((base, st, rows, coord, [(o, v) for v, o in ents]))
    return out


def plan(d):
    """-> (entries, repoint, skipped)

    entries  = [(오프셋, 4, 원본값)]           그대로 ×N 할 int32
    repoint  = [(참조바이트오프셋, 새심볼idx, 새값오프셋, 원본값)]  공유 심볼 분리
    skipped  = [(심볼, 값, 좌표사용, 비좌표사용)]  여분이 없어 못 고친 것
    """
    sargs = {}
    syms, fs = vneg.parse_syms(d, args_out=sargs)
    op_of = {i: op for i, _o, op, _v in syms}
    refs = {}
    scal, scal_refs, other = _uses(d, syms, sargs, fs, refs_out=refs)

    entries, repoint, skipped = [], [], []
    # 여분(재활용 가능) 심볼 판정은 **보수적**으로 한다. 메서드 인자 말고도 참조되는
    # 곳이 있어서(대입·비교 등 다른 opcode), flow 안에서 유효한 심볼 참조가 한 번도
    # 안 나오는 심볼만 여분으로 본다. 이걸 느슨하게 잡으면 값을 덮어써 게임이 죽는다.
    refd = set()
    for i in range(fs, len(d) - 1):
        idx = vneg.symref_index(struct.unpack_from(">H", d, i)[0], len(syms))
        if idx is not None:
            refd.add(idx)
    spare = [i for i, _o, op, _v in syms
             if op == 0x02 and i not in refd and len(sargs.get(i, [])) == 1
             and sargs[i][0][0] == 'i']

    for a in sorted(scal):
        args = sargs.get(a, [])
        if op_of.get(a) != 0x02:
            continue
        if len(args) == 0:
            # 값이 없는 변수 — flow 의 인라인 대입 즉치가 좌표다
            if a in other:
                skipped.append((a, None, sorted(scal[a]), sorted(other[a]))); continue
            if not DO_ASSIGNS:
                continue
            for s, v, off in assigns(d, fs):
                if s == a and v > 0:
                    entries.append((off, 4, v))
            continue
        if len(args) != 1 or args[0][0] != 'i':
            skipped.append((a, "배열%d" % len(args), sorted(scal[a]), [])); continue
        # 0 은 ×N 해도 0 이고, -1 은 "자동" 센티널이라 건드리면 뜻이 바뀐다.
        # 그 밖의 음수는 진짜 좌표다 — 스태프롤은 화면 위(-360)에서 스크롤을
        # 시작하고, 가림막은 화면 위(-480)에서 내려온다. 실측(神無ノ鳥) 결과
        # 좌표 자리의 0 이하 값은 0·-1·이 셋뿐이다.
        if args[0][1] == 0 or args[0][1] == -1:
            continue
        if a in other:
            if DO_REPOINT and len(syms) + len(repoint) < 0x1000:
                # 여분이 없으면 심볼 테이블 **끝에 새 int 심볼을 붙여** 가른다.
                # 인덱스는 뒤에 붙으므로 기존 참조가 밀리지 않고, 점프테이블은
                # flow 상대라 flow 가 통째로 밀려도 그대로 맞는다.
                repoint.append((args[0][1], list(scal_refs[a])))
            else:
                skipped.append((a, args[0][1], sorted(scal[a]), sorted(other[a])))
            continue
        entries.append((args[0][2], 4, args[0][1]))

    for i, _o, op, _v in syms:              # 객체 심볼 정의의 치수 인자
        k = DEF_RULES.get(TYPE.get(op))
        if not k:
            continue
        for t, v, off in sargs.get(i, [])[:k]:
            # 음수도 좌표다 — 스태프롤의 위쪽 가림막은 화면 밖(y=-60)에서 시작해
            # 내려온다. 안 키우면 2× 에서 절반만 숨어 시작 프레임에 띠가 보인다.
            if t == 'i' and (v > 0 or (v < 0 and v != -1)):
                entries.append((off, 4, v))

    if DO_TABLES:                           # 목록 화면의 행 사각형 표
        for _b, _st, _rows, _coord, ents in _tables(d, syms, sargs, fs, scal, other, refs):
            for off, v in ents:
                if v > 0:
                    entries.append((off, 4, v))
    if DO_TABLES:                           # 포인터로 훑는 좌표 배열
        for _b, _len, ents in _ptr_tables(d, syms, sargs, fs, scal, other):
            for off, v in ents:
                if v > 0:
                    entries.append((off, 4, v))
    if DO_ORIGINS:                          # 행 좌표에 더해지는 격자 원점
        for off, v in _origins(d, syms, sargs, fs, scal, other):
            entries.append((off, 4, v))
    return sorted(set(entries)), repoint, skipped


# ─────────────────────────────────────────── 4b. 포인터로 훑는 좌표 배열
# 4 의 표는 "base + 행×폭"을 그때그때 계산하지만, CG 감상의 캐릭터별 썸네일 격자는
# **포인터를 하나 잡아 한 칸씩 늘리며** 훑는다:
#
#   0c <포인터var> <base심볼ref>     포인터 = 배열 첫 칸
#   0d <좌표var>   <포인터var>       꺼내서
#   01 <button> 02 .. <좌표var> ..   그리고
#   30 <포인터var> <int32 = 1>       다음 칸
#
# 포인터가 (복사를 따라가) 좌표 인자 자리에 닿고 실제로 증가하면, base 부터 이어지는
# int 슬롯이 통째로 좌표 배열이다.
PTR_COPY = (0x0B, 0x0C, 0x0D)           # 변수 <- 변수/심볼
PTR_INC = 0x30                          # 변수 += 즉치


def _ptr_tables(d, syms, sargs, fs, scal, other):
    """-> [(base, 길이, [(값오프셋, 값)])]"""
    n = len(syms)
    one_int = {}
    for i, _o, op, _v in syms:
        a = sargs.get(i, [])
        if op == 0x02 and len(a) == 1 and a[0][0] == "i":
            one_int[i] = (a[0][1], a[0][2])
    inc = set()
    for i in range(fs, len(d) - 6):
        if d[i] == PTR_INC:
            v = vneg.symref_index(struct.unpack_from(">H", d, i + 1)[0], n)
            if v is not None:
                inc.add(v)

    root = {}                       # 변수 -> base 심볼 (복사 사슬을 flow 순서로 따라간다)
    for i in range(fs, len(d) - 4):
        if d[i] not in PTR_COPY:
            continue
        a = vneg.symref_index(struct.unpack_from(">H", d, i + 1)[0], n)
        b = vneg.symref_index(struct.unpack_from(">H", d, i + 3)[0], n)
        if a is None or b is None:
            continue
        if b in one_int and b not in scal and b not in other and a in inc:
            root[a] = b             # 배열 첫 칸을 가리키는 포인터
        elif b in root:
            root[a] = root[b]       # 포인터에서 꺼낸 값도 같은 배열

    bases = set()
    for v, b in root.items():
        if v in scal and v not in other:
            bases.add(b)

    out = []
    used = set(scal) | set(other)
    for base in sorted(bases):
        run, k = [], base
        while k in one_int and (k == base or (k not in used and k not in bases)):
            run.append(one_int[k]); k += 1
        if len(run) < TBL_MIN_ROWS:
            continue
        out.append((base, len(run), [(o, v) for v, o in run]))
    return out


# ─────────────────────────────────────────── 5. 격자 원점
COORD_ADD = (0x38, 0x39)                # 변수 += 변수


def _origins(d, syms, sargs, fs, scal, other):
    """`38/39 <좌표변수> <상수심볼>` 의 상수 -> [(값오프셋, 값)]

    표에서 꺼낸 행 좌표는 목록 창 안의 상대값이라, 그리기 직전에 격자 원점을 더한다.
    그 원점 심볼은 메서드 인자로 직접 넘어가지 않아 다른 규칙에 안 걸린다 — 안 키우면
    행 좌표만 ×N 되고 원점은 1× 라 목록 전체가 좌상단으로 밀린다(장면 회상 실측).

    더하는 **대상**이 좌표 자리에만 쓰인 변수이고, 더해지는 상수가 다른 데서 비좌표로
    쓰이지 않을 때만 받는다.
    """
    out, n, i = [], len(syms), fs
    while i < len(d) - 5:
        if d[i] in COORD_ADD:
            a = vneg.symref_index(struct.unpack_from(">H", d, i + 1)[0], n)
            b = vneg.symref_index(struct.unpack_from(">H", d, i + 3)[0], n)
            if (a is not None and b is not None
                    and a in scal and a not in other and b not in other):
                ar = sargs.get(b, [])
                if len(ar) == 1 and ar[0][0] == "i" and ar[0][1] > 0:
                    out.append((ar[0][2], ar[0][1]))
        i += 1
    return out


def remap_values(d, rules):
    """×N **하기 전에** 심볼 값을 갈아끼운다 -> (바이트열, 바꾼 개수)

    rules = [((시작심볼, 끝심볼), {옛값: 새값}), ...]   끝 심볼은 미포함.

    표 원소처럼 flow 에서 개별 참조되지 않는 값은 이름이 없어 다른 손잡이가 없다.
    심볼 인덱스는 번역으로 문자열 길이가 바뀌어도 안 밀리므로 안전하게 가리킬 수 있다
    (바이트 오프셋과 달리). 예: 음악 감상의 곡별 제목 글꼴 크기.
    """
    sargs = {}
    syms, _fs = vneg.parse_syms(d, args_out=sargs)
    b = bytearray(d)
    n = 0
    for (lo, hi), mapping in rules:
        for i in range(max(0, lo), min(hi, len(syms))):
            a = sargs.get(i, [])
            if len(a) != 1 or a[0][0] != "i":
                continue
            new = mapping.get(a[0][1])
            if new is None:
                continue
            b[a[0][2]:a[0][2] + 4] = int(new).to_bytes(4, "big", signed=True)
            n += 1
    return bytes(b), n


def repoint_refs(d, rules, N):
    """지정한 참조만 **새 int 심볼**로 돌린다 -> (바이트열, 바꾼 참조 수)

    rules = [((참조오프셋, ...), 원본값), ...]   새 심볼 값 = 원본값 × N

    한 심볼이 ×N 할 자리와 1× 로 둘 자리에 같이 쓰일 때 쓴다. 자동 도출은 같은 상황을
    `plan()` 의 repoint 로 스스로 처리하지만, 객체를 생짜 번호로 부르는 씬(system.scn)은
    도출이 안 되므로 어느 참조를 가를지 config 로 준다.

    새 심볼은 테이블 **끝에 붙으므로** 기존 인덱스가 밀리지 않고, 점프테이블은 flow
    상대라 flow 가 통째로 밀려도 그대로 맞는다 (plan() 의 repoint 와 같은 이유).
    """
    syms, fs = vneg.parse_syms(d)
    if not syms:
        return d, 0
    b = bytearray(d)
    base_idx = len(syms)
    ndefs = int.from_bytes(b[6:8], "big")
    add = bytearray()
    n = 0
    for k, (offs, val) in enumerate(rules):
        idx = base_idx + k
        for r in offs:
            assert r >= fs, "참조 오프셋 0x%x 가 flow(0x%x) 앞이다" % (r, fs)
            assert vneg.symref_index(int.from_bytes(b[r:r + 2], "big"),
                                     len(syms)) is not None, "0x%x 는 심볼 참조가 아니다" % r
            b[r:r + 2] = (vneg.SYMREF | idx).to_bytes(2, "big")
            n += 1
        add += bytes([0x02, 0x00, 0x01, 0x01, 0x01]) + (val * N).to_bytes(4, "big")
    b[fs:fs] = add
    b[6:8] = (ndefs + len(rules)).to_bytes(2, "big")
    return bytes(b), n


def apply(d, N):
    """계획대로 ×N 한 바이트열을 돌려준다."""
    entries, repoint, skipped = plan(d)
    syms, fs = vneg.parse_syms(d)
    b = bytearray(d)
    for off, w, base in entries:
        # 스크롤 시작 위치처럼 음수인 좌표가 있다(스태프롤 -360 = 화면 위).
        # int32 는 부호 있는 값이라 signed 로 써야 한다.
        b[off:off + w] = (base * N).to_bytes(w, "big", signed=True)
    if repoint:
        # parse_syms 의 len은 다원소 정의를 펼친 **런타임 슬롯 수**라 새 참조의
        # 시작 인덱스로 맞다. 반면 헤더 [6:8]은 정의 수이므로 서로 섞지 않는다.
        base_idx = len(syms)
        ndefs = int.from_bytes(b[6:8], "big")
        add = bytearray()
        for k, (val, refs) in enumerate(repoint):
            idx = base_idx + k
            for r in refs:
                assert vneg.symref_index(int.from_bytes(b[r:r + 2], "big"),
                                        len(syms)) is not None
                b[r:r + 2] = (vneg.SYMREF | idx).to_bytes(2, "big")
            add += bytes([0x02, 0x00, 0x01, 0x01, 0x01]) + (val * N).to_bytes(4, "big")
        b[fs:fs] = add                                  # 심볼 테이블 끝 = flow 앞
        b[6:8] = (ndefs + len(repoint)).to_bytes(2, "big")
    return bytes(b), len(entries), len(repoint), skipped


def report(name, d, N=2):
    entries, repoint, skipped = plan(d)
    tbl = tables(d)
    print("%-16s 좌표 %d개, 공유분리 %d개, 미해결 %d개%s"
          % (name, len(entries), len(repoint), len(skipped),
             ", 표 %d개" % len(tbl) if tbl else ""))
    for base, st, rows, coord, ents in tbl:
        vals = [v for _o, v in ents]
        print("    표 #%d: %d행 × %d열, 좌표열 %s (%d개)  예: %s"
              % (base, rows, st, coord, len(ents), vals[:len(coord) * 2]))
    sargs = {}
    syms, fs = vneg.parse_syms(d, args_out=sargs)
    if DO_TABLES and syms:
        scal, _sr, other = _uses(d, syms, sargs, fs)
        for base, ln, ents in _ptr_tables(d, syms, sargs, fs, scal, other):
            print("    포인터 배열 #%d: %d개  %s" % (base, ln, [v for _o, v in ents]))
    for a, v, s, o in skipped:
        print("    ⚠ #%s = %s : 좌표 %s / 비좌표 %s"
              % (a, v, ' '.join('%s.m%02x[%d]' % x for x in s),
                 ' '.join('%s.m%02x[%d]' % x for x in o)))


def main():
    ap = argparse.ArgumentParser(description=".scn 좌표 자동 도출")
    ap.add_argument("names", nargs="+")
    ap.add_argument("--archive")
    a = ap.parse_args()
    if a.archive:
        data, ents, table = axr.load(a.archive)
        pool = {n: (o, s) for n, o, s in ents}
        for n in a.names:
            if n in pool:
                report(n, axr.getfile(data, table, *pool[n]))
            else:
                print("  %s: 아카이브에 없음" % n)
    else:
        for p in a.names:
            report(os.path.basename(p), open(p, "rb").read())


if __name__ == "__main__":
    main()
