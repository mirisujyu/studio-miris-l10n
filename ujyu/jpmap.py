#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""미번역 일본어를 CP949 로 실어 나르기 위한 문자 매핑.

번역이 없는 조각은 `inject_text.transcode_jp` 가 SJIS -> CP949 로 옮긴다. 가나와
상당수 한자는 CP949(KS X 1001)에 그대로 있어서 문제가 없지만, **CP949 에 없는
한자·기호**는 옮길 자리가 없어 원본 SJIS 바이트로 남고 화면에서 깨진다.

해결: CP949 안에서 **안 쓰는 자리**를 빌려 쓴다. 자리는 두 종류다.

    1. 사용자 정의 영역     0xC9A1~0xC9FE · 0xFEA1~0xFEFE      188자
    2. **한자 영역**        0xCAA1~0xFDFE 중 안 쓰는 코드      최대 4,888자

Windows 코드페이지 949 는 각 자리를 이렇게 유니코드로 바꾼다:

    0xC9A1 -> U+E000 ... 0xFEFE -> U+E0BB     (사설영역)
    0xCAA1 -> U+4F3D  등                       (그 한자 자신)

본문 렌더가 `GetGlyphOutlineA` + HANGEUL_CHARSET 이라, GDI 가 2바이트 코드를 이 표대로
유니코드로 바꾼 뒤 폰트 cmap 을 찾는다. 즉 **폰트의 그 유니코드 자리에 글리프를 넣고
해당 바이트를 주입하면 그 글리프가 나온다**(실측 확인).

■ 한자 영역을 빌려도 되는 이유

CP949 의 한자 4,888자는 한국어 문장에서 거의 안 쓰인다. 그래서 그 자리에 **일본어
한자의 글리프를 얹어** 쓴다. 예를 들어 `茎`(CP949 에 없음)을 `丁`(CP949 CDD1)의
자리에 실으면, 주입기는 CDD1 을 넣고 폰트는 `丁` 자리에 `茎` 글리프를 그린다.

당연히 **그 자리를 원래 뜻으로 쓰는 글자는 비켜 가야** 한다. 아래 두 부류를 예약하고
남은 자리만 배정한다:

    · 미번역 일본어가 쓰는 글자 중 CP949 에 있는 것 (자기 자리를 그대로 써야 한다)
    · 번역문이 쓰는 글자 (한국어 문장에 한자가 섞이는 경우)

■ 순서 — **폰트를 먼저 만들고 번역한다**

이 표는 "번역이 안 된 조각"에서 만들어지므로 번역이 진행되면 대상이 줄어든다. 곧
**표가 바뀌고, 그러면 폰트도 다시 만들어야 한다.** 그래서 `ujyu font` 가 빌드할 때
표를 같이 만든다(어긋날 여지를 없앤다). 번역을 시작하기 전에 한 번 돌려 두면
그 뒤로는 자리가 줄기만 하므로 다시 만들지 않아도 안전하다.

사용:
    ujyu jpmap [-o 경로]      # strings.json 을 훑어 표를 만든다
"""
import sys, os, json, io, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ujyu.titleconfig import config as C

# 1순위 — 사용자 정의 영역. (리드, 트레일 시작, 개수)
PUA_AREAS = [(0xC9, 0xA1, 94), (0xFE, 0xA1, 94)]
PUA_BASE = 0xE000
# 2순위 — 한자 영역. 안 쓰는 코드를 빌린다.
HANJA_LEAD = (0xCA, 0xFD)

DEFAULT_PATH = os.path.join(os.path.dirname(C.STRINGS), "jp_charmap.json")


def cp949_ok(ch):
    try:
        ch.encode("cp949")
        return True
    except UnicodeEncodeError:
        return False


def _pua_slots():
    out = []
    for lead, t0, n in PUA_AREAS:
        for k in range(n):
            out.append(((lead << 8) | (t0 + k), PUA_BASE + len(out)))
    return out


def _hanja_slots(reserved):
    """[(cp949코드, 그 코드가 가리키는 유니코드)] — `reserved` 에 든 유니코드는 뺀다."""
    out = []
    for lead in range(HANJA_LEAD[0], HANJA_LEAD[1] + 1):
        for trail in range(0xA1, 0xFF):
            code = (lead << 8) | trail
            try:
                u = ord(bytes([lead, trail]).decode("cp949"))
            except UnicodeDecodeError:
                continue
            if u in reserved:
                continue
            out.append((code, u))
    return out


def slots(reserved=frozenset()):
    """배정 가능한 자리 목록. 사용자 정의 영역을 먼저 쓰고 한자 영역으로 넘어간다."""
    return _pua_slots() + _hanja_slots(reserved)


def scan(strings_path=None):
    """(미번역 문자 빈도, 예약 유니코드).

    예약 = 자기 자리를 그대로 써야 하는 유니코드.
      · 미번역 일본어 중 CP949 에 있는 글자
      · 번역문(kr)이 쓰는 글자
    """
    from ujyu import filter_text as F
    S = json.load(io.open(strings_path or C.STRINGS, encoding="utf-8"))
    cnt = collections.Counter()
    reserved = set()
    for r in S:
        jp = r.get("jp") or ""
        kr = (r.get("kr") or "").strip()
        if kr:
            reserved.update(ord(c) for c in kr)
        if F.classify(jp) != "text" or kr:
            continue
        body = F.split_tail(jp)[0]
        cnt.update(body)
    for ch in list(cnt):
        if cp949_ok(ch):
            reserved.add(ord(ch))
    return cnt, reserved


def build(freq, reserved=frozenset()):
    """{문자: 횟수} -> 매핑 dict. CP949 에 없는 글자만 빈도 내림차순으로 배정한다."""
    need = sorted((c for c in freq if not cp949_ok(c)),
                  key=lambda c: (-freq[c], c))
    sl = slots(reserved)
    if len(need) > len(sl):
        raise SystemExit(
            "CP949 에서 빌릴 자리가 모자란다: 필요 %d자 / 자리 %d자.\n"
            "  한자 영역까지 다 썼다. 원문이 이만큼 다양한 타이틀이면 표시할 글자를\n"
            "  줄이는 수밖에 없다(덜 쓰이는 %d자 포기)."
            % (len(need), len(sl), len(need) - len(sl)))
    n_pua = len(_pua_slots())
    return {
        "chars": [
            {"ch": c, "cp949": "%04X" % sl[i][0], "uni": "%04X" % sl[i][1],
             "area": "pua" if i < n_pua else "hanja", "n": freq[c]}
            for i, c in enumerate(need)
        ],
        "capacity": len(sl),
        "used": len(need),
    }


_CACHE = {}


def load(path=None):
    """(치환표, 유니코드표) = ({문자: bytes}, {문자: 폰트에 심을 유니코드})

    주입기가 조각마다 부르므로 캐시한다.
    """
    p = path or DEFAULT_PATH
    if p in _CACHE:
        return _CACHE[p]
    if not os.path.exists(p):
        _CACHE[p] = ({}, {})
        return _CACHE[p]
    d = json.load(io.open(p, encoding="utf-8"))
    enc, uni = {}, {}
    for e in d["chars"]:
        code = int(e["cp949"], 16)
        enc[e["ch"]] = bytes([code >> 8, code & 0xFF])
        uni[e["ch"]] = int(e.get("uni") or e.get("pua"), 16)
    _CACHE[p] = (enc, uni)
    return enc, uni


def cmd_jpmap(out=None):
    freq, reserved = scan()
    m = build(freq, reserved)
    p = out or DEFAULT_PATH
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(m, ensure_ascii=False, indent=1))
    tot = sum(freq[e["ch"]] for e in m["chars"])
    n_h = sum(1 for e in m["chars"] if e["area"] == "hanja")
    print("CP949 미매핑 %d자 -> 빈 자리에 배정 (사용자정의 %d + 한자영역 %d, 자리 %d)"
          % (m["used"], m["used"] - n_h, n_h, m["capacity"]))
    print("  해당 문자 총 출현 %d회" % tot)
    print("  상위:", " ".join("%s(%d)" % (e["ch"], e["n"]) for e in m["chars"][:12]))
    print("-> %s" % p)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        prog="ujyu jpmap",
        description="미번역 일본어를 CP949 빈 자리에 싣는 문자 매핑 생성")
    ap.add_argument("-o", "--out", help="출력 경로 (기본 translation/jp_charmap.json)")
    a = ap.parse_args()
    cmd_jpmap(a.out)


if __name__ == "__main__":
    main()
