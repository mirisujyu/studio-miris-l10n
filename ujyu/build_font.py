#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Studio Miris 엔진용 텍스트 폰트 빌더 (스펙 주도 범용 엔진).

타이틀 리포의 **폰트 스펙 파일**(예: kannagi `fonts/KannagiKR-Noto.py`)을 받아 빌드한다.
스펙에서 SOURCE/FACE/TARGET_BOTTOM_EM/SAMPLE 와, 가변폭용 SPACE/OPEN/OPEN_RSB/CLOSE/
FIXED/CMAP_ALIAS 를 읽는다. 손보는 것:

1. **세로 시프트** — 렌더가 GetGlyphOutlineA(GGO_GRAY8) 라 글자 아래가 잘린다.
   한글 최저점을 spec.TARGET_BOTTOM_EM 위치로 올린다.
2. **전각 ASCII → 비례 ASCII 글리프**(U+FF01~FF5E) + spec.CMAP_ALIAS(글리프 없는 문자 별칭).
   이 둘은 모드와 무관하게 항상 적용한다(글자가 화면에 나오게 하는 것이라).
3. **가변폭 조정**(mode=="proportional" 일 때만) — spec.SPACE/OPEN/CLOSE/FIXED 로
   전각공백·여는/닫는 괄호·두꺼운 괄호의 advance 와 획 위치를 손본다.
   mode=="fullwidth" 면 이 조정을 건너뛴다(모든 글자 전각 고정폭).

glyf/CFF 양쪽 소스를 처리한다(CFF 는 glyf 로 변환 — 게임이 OpenType 을 목록에서 걸러냄).

사용:
  ujyu font <스펙.py> <출력.ttf> [--mode proportional|fullwidth] [--shift-em EM]
예:
  ujyu font ../kannagi-no-tori/fonts/KannagiKR-Noto.py out.ttf --mode proportional
"""
import sys, os, argparse, importlib.util

from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen


def load_spec(path):
    s = importlib.util.spec_from_file_location("fontspec", path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def hangul_bottom(f, sample):
    """표본 한글의 최저 y (폰트 유닛)."""
    gs, cm = f.getGlyphSet(), f.getBestCmap()
    lows = []
    for cp in sample:
        gn = cm.get(cp)
        if not gn:
            continue
        bp = BoundsPen(gs)
        gs[gn].draw(bp)
        if bp.bounds:
            lows.append(bp.bounds[1])
    return min(lows) if lows else None


def shift_glyf(f, dy):
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    glyf, gs = f["glyf"], f.getGlyphSet()
    for gn in f.getGlyphOrder():
        pen = TTGlyphPen(gs)
        gs[gn].draw(TransformPen(pen, (1, 0, 0, 1, 0, dy)))
        glyf[gn] = pen.glyph()


def cff_to_glyf(f, dy):
    """CFF(OpenType) -> glyf(TrueType) 변환 + 세로 시프트를 한 번에.

    게임의 글꼴 목록 콜백이 OpenType 을 걸러내므로(TRUETYPE_FONTTYPE 검사) TrueType
    아웃라인으로 바꾼다 (3차 베지어 -> 2차, cu2qu).
    """
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib.tables._g_l_y_f import table__g_l_y_f
    from fontTools.ttLib.tables._l_o_c_a import table__l_o_c_a

    gs = f.getGlyphSet()
    order = f.getGlyphOrder()
    upm = f["head"].unitsPerEm
    glyf = table__g_l_y_f()
    glyf.glyphOrder = order
    glyf.glyphs = {}
    for gn in order:
        ttpen = TTGlyphPen(None)
        pen = Cu2QuPen(ttpen, max_err=upm / 1000.0)
        gs[gn].draw(TransformPen(pen, (1, 0, 0, 1, 0, dy)))
        glyf.glyphs[gn] = ttpen.glyph()

    del f["CFF "]
    for t in ("VORG", "CFF2"):
        if t in f:
            del f[t]
    f["glyf"] = glyf
    f["loca"] = table__l_o_c_a()
    f.setGlyphOrder(order)
    # sfnt 헤더를 TrueType 으로 (안 바꾸면 Windows 가 설치 거부).
    f.sfntVersion = "\x00\x01\x00\x00"
    f["maxp"].tableVersion = 0x00010000
    for attr, val in (("maxZones", 1), ("maxTwilightPoints", 0),
                      ("maxStorage", 0), ("maxFunctionDefs", 0),
                      ("maxInstructionDefs", 0), ("maxStackElements", 0),
                      ("maxSizeOfInstructions", 0), ("maxComponentElements", 0),
                      ("maxComponentDepth", 0)):
        setattr(f["maxp"], attr, val)
    f["head"].indexToLocFormat = 0
    f["head"].glyphDataFormat = 0
    f.recalcBBoxes = True


def remap_fullwidth_ascii(f):
    """전각 ASCII(U+FF01~FF5E) → 비례 ASCII 글리프. 바이트는 CP949 A3xx 그대로."""
    cm = f.getBestCmap()
    tables = [t for t in f["cmap"].tables if t.isUnicode() or t.platformID == 3]
    n = 0
    for cp in range(0xFF01, 0xFF5F):
        g = cm.get(cp - 0xFF01 + 0x21)
        if not g:
            continue
        for t in tables:
            if cp in t.cmap:
                t.cmap[cp] = g
        n += 1
    print("  전각 ASCII 재매핑 %d자" % n)


def apply_cmap_alias(f, spec):
    """spec.CMAP_ALIAS {cp: 기존_cp} — 소스에 글리프 없는 문자를 있는 글리프로 매핑."""
    alias = getattr(spec, "CMAP_ALIAS", {})
    if not alias:
        return
    cm = f.getBestCmap()
    tables = [t for t in f["cmap"].tables if t.isUnicode() or t.platformID == 3]
    n = 0
    for cp, src in alias.items():
        g = cm.get(src)
        if not g:
            continue
        for t in tables:
            t.cmap[cp] = g
        n += 1
    if n:
        print("  CMAP_ALIAS %d자 (예: ～→~)" % n)


def apply_proportional(f, spec):
    """가변폭 글리프 조정: 전각공백/여는·닫는 괄호/두꺼운 괄호."""
    cm, hmtx, glyf = f.getBestCmap(), f["hmtx"], f["glyf"]

    def _xmax(g):
        gl = glyf[g]
        if getattr(gl, "numberOfContours", 0) > 0:
            gl.recalcBounds(glyf)
            return gl, gl.xMax
        return gl, None

    for cp, adv in getattr(spec, "SPACE", {}).items():          # 전각공백 등
        g = cm.get(cp)
        if g:
            hmtx[g] = (adv, hmtx[g][1])

    rsb = getattr(spec, "OPEN_RSB", 55)
    for cp, adv in getattr(spec, "OPEN", {}).items():           # 여는: advance + 획 오른정렬
        g = cm.get(cp)
        if not g:
            continue
        gl, xmax = _xmax(g)
        if xmax is not None:
            dx = (adv - rsb) - int(xmax)
            gl.coordinates.translate((dx, 0)); gl.recalcBounds(glyf)
            hmtx[g] = (adv, hmtx[g][1] + dx)
        else:
            hmtx[g] = (adv, hmtx[g][1])

    for cp, gap in getattr(spec, "CLOSE", {}).items():          # 닫는: 획 오른끝 + 트인여백
        g = cm.get(cp)
        if not g:
            continue
        gl, xmax = _xmax(g)
        if xmax is not None:
            hmtx[g] = (int(xmax) + gap, hmtx[g][1])

    for cp, adv in getattr(spec, "FIXED", {}).items():          # 고정 advance (두꺼운 괄호)
        g = cm.get(cp)
        if not g:
            continue
        gl, xmax = _xmax(g)
        if xmax is not None and int(xmax) > adv:                # 획이 셀 넘으면 왼쪽으로
            dx = adv - int(xmax) - 5
            gl.coordinates.translate((dx, 0)); gl.recalcBounds(glyf)
            hmtx[g] = (adv, hmtx[g][1] + dx)
        else:
            hmtx[g] = (adv, hmtx[g][1])
    print("  가변폭 조정: SPACE/OPEN/CLOSE/FIXED 적용")


def fit_vertical(f, spec):
    """셀 밖으로 나가는 글리프를 세로로만 밀어 넣는다.

    GGO 경로는 글리프를 `gmptGlyphOrigin.y`(=yMax) 기준으로 **1 em 짜리 셀**에 얹기
    때문에, 아웃라인이 `0..upm` 밖으로 나가면 그 부분이 그대로 잘린다. 전역 시프트
    (`TARGET_BOTTOM_EM`)는 **한글 표본만** 보고 정하므로 그보다 더 내려앉는 글자는
    거기서 못 걸러진다 — 폰트마다 디센더 깊이가 달라 한쪽에 맞춘 값이 다른 쪽에서
    깨진다.

        Noto Sans KR   ( ) , / \\ { } [ ]  → yMin −62..−88  (40px 기준 아래 4px 잘림)
        IBM Plex Sans KR                   → yMin ≥ 0       (안 잘림)

    특히 `）`(마음속 대사 닫는 괄호)는 본문에 6천 번 넘게 나와 눈에 잘 띈다.

    올려서 위가 넘치면 **세로로만** 눌러 맞춘다. 가로를 건드리면 advance 와
    `hmtx` lsb(=xMin) 를 다시 잡아야 하는데, 자간이 곧 advance 라(TEXT_RENDER §3)
    글자 폭이 흔들린다. 세로 압축은 괄호 기준 3% 남짓이라 눈에 띄지 않는다.
    """
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    lo_em, hi_em = getattr(spec, "FIT_WINDOW_EM", (0.01, 0.99))
    upm = f["head"].unitsPerEm
    floor, ceil_ = round(lo_em * upm), round(hi_em * upm)
    glyf = f["glyf"]
    stat = {"moved": 0, "squashed": 0, "worst": (1.0, None), "decomposed": 0}

    def fit(gn):
        """창 밖이면 맞추고 True. 세로만 건드리므로 advance/lsb 는 그대로다."""
        gl = glyf[gn]
        if getattr(gl, "numberOfContours", 0) == 0:      # 빈 글리프
            return False
        gl.recalcBounds(glyf)
        lo, hi = gl.yMin, gl.yMax
        if lo >= floor and hi <= ceil_:
            return False
        if gl.numberOfContours < 0:
            # 합성 글리프는 좌표를 직접 못 만진다 — 윤곽으로 풀어서 고친다.
            # (부품이 이미 고쳐졌어도 offset 때문에 창을 넘을 수 있다)
            gs = f.getGlyphSet()
            rec = DecomposingRecordingPen(gs)
            gs[gn].draw(rec)
            pen = TTGlyphPen(None)
            rec.replay(pen)
            glyf[gn] = gl = pen.glyph()
            gl.recalcBounds(glyf)
            lo, hi = gl.yMin, gl.yMax
            stat["decomposed"] += 1
            if gl.numberOfContours <= 0 or (lo >= floor and hi <= ceil_):
                return True
        s = 1.0
        if hi - lo > ceil_ - floor:                      # 창보다 크면 눌러서 맞춘다
            s = (ceil_ - floor) / float(hi - lo)
            stat["squashed"] += 1
            if s < stat["worst"][0]:
                stat["worst"] = (s, gn)
        new_hi = lo + (hi - lo) * s
        dy = floor - lo if lo < floor else (ceil_ - new_hi if new_hi > ceil_ else 0)
        c = gl.coordinates
        for i in range(len(c)):
            x, y = c[i]
            c[i] = (x, int(round((y - lo) * s + lo + dy)))
        gl.recalcBounds(glyf)
        return True

    order = f.getGlyphOrder()
    # 윤곽 글리프 먼저 — 부품이 제자리로 가면 합성 글리프가 저절로 들어오는 경우가 많다.
    for gn in order:
        if getattr(glyf[gn], "numberOfContours", 0) > 0 and fit(gn):
            stat["moved"] += 1
    for gn in order:
        if getattr(glyf[gn], "numberOfContours", 0) < 0 and fit(gn):
            stat["moved"] += 1
    moved, squashed, worst = stat["moved"], stat["squashed"], stat["worst"]
    if moved:
        note = ""
        if squashed:
            note += " / 눌러 맞춤 %d자 (최대 %s ×%.3f)" % (squashed, worst[1], worst[0])
        if stat["decomposed"]:
            note += " / 합성 푼 것 %d자" % stat["decomposed"]
        print("  세로 맞춤: %d자를 %d..%d 안으로%s" % (moved, floor, ceil_, note))
    else:
        print("  세로 맞춤: 셀(%d..%d) 밖으로 나간 글리프 없음" % (floor, ceil_))


def _box(f, cps, cm=None):
    """주어진 코드포인트들의 (최저 y, 최고 y, advance 중앙값). 글리프 없으면 건너뛴다."""
    import statistics
    cm = cm or f.getBestCmap()
    gs, hm = f.getGlyphSet(), f["hmtx"]
    los, his, advs = [], [], []
    for cp in cps:
        gn = cm.get(cp)
        if not gn:
            continue
        bp = BoundsPen(gs)
        gs[gn].draw(bp)
        if not bp.bounds:
            continue
        los.append(bp.bounds[1]); his.append(bp.bounds[3]); advs.append(hm[gn][0])
    if not los:
        return None
    return min(los), max(his), statistics.median(advs)


# 한글 폰트에 있어도 **일본어 쪽 글리프로 덮어쓸** 범위 (기본값).
#
# Noto·본고딕 계열 한글 폰트는 CJK 통합한자를 다 갖고 있지만 **한국식 자형**이다.
# 일본어 문장에 섞이면 획이 다르게 보인다(`直`·`骨`·`海` 등). 가나·한자는 통째로
# 일본어 쪽을 쓰는 게 맞다.
#
# 온점·반점은 자형이 아니라 **위치** 문제다 — 한글 폰트는 칸 가운데에 놓고
# advance 도 넓게 잡지만 일본어 조판은 왼쪽 아래에 붙인다. 그대로 두면 일본어
# 문장에서 앞뒤가 벌어진다. 번역문은 주입 때 `．，` 로 바뀌므로 이 둘은 미번역
# 일본어 전용이라 덮어써도 안전하다.
JP_OVERRIDE_DEFAULT = [
    (0x3040, 0x309F),   # 히라가나
    (0x30A0, 0x30FF),   # 가타카나
    (0x3400, 0x4DBF),   # 한자 확장 A
    (0x4E00, 0x9FFF),   # 한자
    (0xF900, 0xFAFF),   # CJK 호환 한자
    0x3005,             # 々  반복 기호
    0x3001,             # 、  반점
    0x3002,             # 。  온점
]


def _override_set(spec_value):
    """스펙의 JP_OVERRIDE 를 코드포인트 집합으로 편다.

    항목은 정수(코드포인트) 또는 `(시작, 끝)` 범위. 스펙에 없으면 기본값을 쓴다
    — 가나·한자를 일본어 자형으로 쓰는 것이 이 파이프라인의 **기본 동작**이다.
    스펙에서 `JP_OVERRIDE = ()` 로 두면 끌 수 있다.
    """
    items = JP_OVERRIDE_DEFAULT if spec_value is None else spec_value
    out = set()
    for it in items:
        if isinstance(it, (tuple, list)):
            out.update(range(it[0], it[1] + 1))
        else:
            out.add(it)
    return out


def merge_jp(f, spec):
    """일본어 글리프를 한글 폰트에 심는다 (미번역 일본어 표시용).

    `ujyu jpmap` 이 만든 표를 그대로 따른다. 두 경로가 있다:

      · CP949 에 있는 글자(가나 전부·한자 다수) → **원래 유니코드 위치**에 심는다.
        주입기가 그냥 `cp949` 로 인코딩하면 GDI 가 알아서 찾아온다.
      · CP949 에 없는 글자 → **U+E000.. (사설영역)** 에 심는다. 주입기가 대응하는
        사용자정의영역 바이트(C9A1..)를 넣으면 GDI 가 이 자리로 데려온다.

    크기 맞추기 — 두 폰트는 upm 이 같아도 **글자를 그리는 상자가 다르다**.
    Noto Sans 기준 한글은 advance 920 에 y 8..935, 일본어는 advance 1000 에
    y -89..850 다. 그대로 심으면 한자가 크고 아래로 내려앉는다. 그래서

      1. `advance` 비율로 균일 축소 (920/1000) — 글자가 한글과 같은 칸을 쓴다
      2. 상자 **중앙**을 한글에 맞춰 올린다 — 베이스라인 관습 차이를 흡수한다

    두 값 다 스펙에서 `JP_SCALE` / `JP_DY` 로 덮어쓸 수 있다.
    """
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.pens.cu2quPen import Cu2QuPen
    from ujyu import jpmap

    freq, _res = jpmap.scan()
    src = getattr(spec, "JP_SOURCE", None)
    if not freq:
        print("  JP 병합: 미번역 조각이 없다 — 건너뜀")
        return
    if not src:
        # 미번역 일본어가 있는데 소스 폰트가 없으면 그 대목이 화면에서 깨진다.
        # 조용히 넘어가면 나중에 게임에서야 발견하므로 여기서 알린다.
        print("  ⚠ 미번역 조각이 %d자 있는데 spec.JP_SOURCE 가 없다 — "
              "그 대목은 글리프가 없어 깨진다." % len(freq))
        return
    if not os.path.exists(src):
        raise SystemExit("JP 소스 폰트가 없다: %s" % src)

    # **표를 여기서 다시 만든다.** 폰트와 주입기가 같은 표를 봐야 하는데, 번역이
    # 늘면 미번역 문자 집합이 바뀌어 자리 배정이 달라진다. 표만 갱신하고 폰트를
    # 안 만들면(또는 그 반대) 화면의 글자가 다른 글자로 바뀐다. 그래서 폰트를
    # 만들 때 표도 같이 만들어 어긋날 여지를 없앤다.
    charmap = getattr(spec, "JP_CHARMAP", None)
    jpmap.cmd_jpmap(charmap)
    jpmap._CACHE.clear()
    _enc, pua = jpmap.load(charmap)

    jf = TTFont(src, fontNumber=0)
    # CFF(OTF) 소스도 받는다 — glyphSet 으로 그리면 아웃라인 종류와 무관하고,
    # 아래에서 Cu2QuPen 으로 2차 베지어로 바꿔 담는다. Noto CJK 는 OTF 배포가 많다.
    jcm, jgs = jf.getBestCmap(), jf.getGlyphSet()
    jp_is_cff = "glyf" not in jf

    kupm, jupm = f["head"].unitsPerEm, jf["head"].unitsPerEm
    kr = _box(f, [0xAC00 + i * 17 for i in range(400)])
    jp = _box(jf, [c for c in range(0x4E00, 0x9FA0, 13)][:400], jcm)
    if not kr or not jp:
        raise SystemExit("한글/한자 표본을 못 잡았다 — 폰트를 확인해라")
    k_lo, k_hi, k_adv = kr
    j_lo, j_hi, j_adv = jp

    s = getattr(spec, "JP_SCALE", None)
    if s is None:
        s = (k_adv / kupm) / (j_adv / jupm) * (kupm / jupm)   # 같은 칸을 쓰도록
    dy = getattr(spec, "JP_DY", None)
    if dy is None:                      # 상자 중앙 맞춤
        dy = round((k_lo + k_hi) / 2 - (j_lo + j_hi) / 2 * s)
    adv = round(k_adv)                  # 전각 = 한글 폭
    print("  JP 병합: 한글 y%d..%d adv=%d / 한자 y%d..%d adv=%d"
          % (k_lo, k_hi, k_adv, j_lo, j_hi, j_adv))
    print("           배율=%.4f  세로이동=%+d  advance=%d" % (s, dy, adv))

    glyf, hmtx = f["glyf"], f["hmtx"]
    kcm = f.getBestCmap()
    ucmaps = [t for t in f["cmap"].tables if t.isUnicode()]
    override = _override_set(getattr(spec, "JP_OVERRIDE", None))
    added = skipped = missing = 0
    for ch in sorted(freq, key=lambda c: -freq[c]):
        target = pua.get(ch, ord(ch)) if not jpmap.cp949_ok(ch) else ord(ch)
        if target in kcm and target not in override:   # 이미 한글 폰트에 있는 글자
            skipped += 1
            continue
        gn_src = jcm.get(ord(ch))
        if not gn_src:
            missing += 1
            continue
        # 이미 있는 코드포인트를 덮어쓰는 경우엔 **그 글리프를 제자리에서 교체**한다.
        # 새 글리프를 만들어 cmap 만 돌리면, 저장할 때 post 가 이름을 코드포인트에서
        # 다시 만들어 내면서 옛 글리프와 이름이 겹쳐 옛것이 살아남는다.
        name = kcm.get(target) or ("jp_%04X" % target)
        pen = TTGlyphPen(None)
        sink = Cu2QuPen(pen, max_err=kupm / 1000.0) if jp_is_cff else pen
        jgs[gn_src].draw(TransformPen(sink, (s, 0, 0, s, 0, dy)))
        g = pen.glyph()
        glyf[name] = g               # glyf.__setitem__ 이 glyphOrder 에도 넣는다
        # **lsb 는 글리프의 xMin 과 맞춰야 한다.** 0 으로 두면 렌더러가 아웃라인을
        # 그만큼 왼쪽으로 당겨 붙여, 좌여백이 큰 글자(`く` 204, `、` 52)가 칸 왼쪽에
        # 쏠리고 오른쪽에 빈 자리가 남는다 — 화면에서 자간이 제멋대로로 보인다.
        g.recalcBounds(glyf)
        hmtx[name] = (adv, getattr(g, "xMin", 0))
        for t in ucmaps:
            t.cmap[target] = name
        added += 1
    f.setGlyphOrder(glyf.glyphOrder)   # 폰트 쪽 순서를 glyf 가 갱신한 것에 맞춘다
    f["maxp"].numGlyphs = len(glyf.glyphOrder)
    print("           심음 %d자 (이미 있음 %d, JP 폰트에 없음 %d)"
          % (added, skipped, missing))


def build(spec, out, mode="proportional", shift_em=None):
    f = TTFont(spec.SOURCE, fontNumber=0)
    upm = f["head"].unitsPerEm
    is_cff = "CFF " in f

    # 1) 세로 시프트
    low = hangul_bottom(f, spec.SAMPLE)
    if shift_em is None:
        if low is None:
            raise SystemExit("한글 글리프를 못 찾음 — 한글 폰트가 맞는지 확인")
        dy = round(spec.TARGET_BOTTOM_EM * upm - low)
    else:
        dy = round(shift_em * upm)
    print("  아웃라인=%s upm=%d  한글최저=%s  시프트=%+d (%.3f em)"
          % ("CFF" if is_cff else "glyf", upm, low, dy, dy / upm))
    # 세로쓰기 메트릭은 버린다. 게임은 가로쓰기뿐이고, 남겨 두면 글리프를 추가할 때
    # (merge_jp) vmtx 를 같이 갱신하지 않아 저장이 KeyError 로 죽는다. vmtx 는 hmtx 와
    # 같은 클래스라 에러가 hmtx 이름으로 떠서 역추적이 헷갈린다. glyf 소스(Noto 등)도
    # vmtx 를 갖고 있으므로 CFF 변환 경로가 아니라 여기서 지운다.
    for t in ("vmtx", "vhea", "VORG"):
        if t in f:
            del f[t]
    (cff_to_glyf if is_cff else shift_glyf)(f, dy)
    if is_cff:
        print("  CFF -> glyf 변환")

    # 1-b) 일본어 글리프 병합 — 한글이 이미 시프트된 뒤라 그 좌표계에 맞춰 심는다
    merge_jp(f, spec)

    # 1-c) 셀 밖으로 나간 글리프 되돌리기 — 전역 시프트는 한글 표본만 보고 정하므로
    #      그보다 깊은 디센더(괄호·쉼표 등)와 병합된 일본어를 여기서 함께 걸러 낸다.
    fit_vertical(f, spec)

    # 2) 전각 ASCII 재매핑 + 별칭 (모드 무관 — 글자가 화면에 나오게)
    remap_fullwidth_ascii(f)
    apply_cmap_alias(f, spec)

    # 3) 가변폭 조정 (proportional 모드만)
    if mode == "proportional":
        apply_proportional(f, spec)
    else:
        print("  fullwidth 모드 — 가변폭 조정 생략 (모든 글자 전각 고정폭)")

    # 4) 이름 교체 (게임이 CreateFontIndirectA(FACE) 로 찾는다)
    face = spec.FACE
    name = f["name"]
    name.names = [r for r in name.names if r.nameID not in (1, 4, 6, 16, 17, 21)]
    for lang in (0x409, 0x412):
        name.setName(face, 1, 3, 1, lang)
        name.setName(face, 4, 3, 1, lang)
    name.setName(face.replace(" ", ""), 6, 3, 1, 0x409)

    # CP949(949) 코드페이지 표시 → HANGUL_CHARSET 요청 시 매칭
    try:
        f["OS/2"].ulCodePageRange1 |= (1 << 19)
    except Exception:
        pass
    if "DSIG" in f:
        del f["DSIG"]

    f.save(out)
    print("  저장: %s (face=%r, mode=%s)" % (out, face, mode))


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu font",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="폰트 스펙(.py)으로 게임용 한국어 텍스트 폰트 빌드 (세로 시프트·전각 재매핑·가변폭)",
        epilog="인자를 생략하면 config 에서 가져온다:\n"
               "  스펙 = fonts/<config.FONT_FACE>.py · 출력 = fonts/<스펙의 FACE>.ttf\n"
               "  모드 = config.FONT_WIDTH_MODE\n"
               "예:\n"
               "  ujyu font                              # config 그대로 빌드\n"
               "  ujyu font 스펙.py out.ttf --mode fullwidth   # 전각 고정폭\n")
    ap.add_argument("spec", nargs="?", default=None,
                    help="폰트 스펙 파일 (.py). 생략하면 fonts/<config.FONT_FACE>.py")
    ap.add_argument("out", nargs="?", default=None,
                    help="출력 .ttf 경로. 생략하면 fonts/<스펙의 FACE>.ttf")
    ap.add_argument("--mode", default=None,
                    choices=["proportional", "fullwidth"],
                    help="fullwidth=전각 고정폭 / proportional=가변폭(스펙 조정) "
                         "(기본: config.FONT_WIDTH_MODE)")
    ap.add_argument("--shift-em", type=float, default=None,
                    help="세로 시프트를 em 단위로 직접 지정 (기본: 한글 최저점으로 자동)")
    a = ap.parse_args()

    spec_path, mode, out = a.spec, a.mode, a.out
    if spec_path is None or mode is None or out is None:
        from ujyu.titleconfig import config as C     # 생략된 것만 config 에서 채운다
        if spec_path is None:
            face = getattr(C, "FONT_FACE", None)
            if not face:
                raise SystemExit("스펙 경로를 주거나 config.FONT_FACE 를 채워라"
                                 "(fonts/<face>.py — 템플릿: samples/font_spec.py)")
            spec_path = C.repo("fonts", face + ".py") if hasattr(C, "repo") \
                else os.path.join("fonts", face + ".py")
        if mode is None:
            mode = getattr(C, "FONT_WIDTH_MODE", None) or "proportional"
    if not os.path.exists(spec_path):
        raise SystemExit("폰트 스펙이 없다: %s  (템플릿: samples/font_spec.py)" % spec_path)
    spec = load_spec(spec_path)
    if out is None:
        out = os.path.join(os.path.dirname(spec_path) or ".", spec.FACE + ".ttf")
    print("빌드: %s -> %s (mode=%s)" % (os.path.basename(spec_path), out, mode))
    build(spec, out, mode, a.shift_em)


if __name__ == "__main__":
    raise SystemExit(main())
