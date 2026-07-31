#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strings.json v1(휴리스틱 추출) → v2(vneg 구조적 추출) 번역 이관 + 커버리지 QA.

v1: [{file, off, bytelen, jp, kr[, speaker]}]   — 구 _sjis_runs 기반 (삭제됨)
v2: [{arc, file, id, kind, off, bytelen, jp[, speaker], kr}] — miris.vneg.extract 기반
    (ujyu scn extract 로 생성)

하는 일
  1. v1 의 kr 이 채워진 레코드를 (file, jp) 스트링 매치로 v2 에 이관한다.
  2. 커버리지 QA: 구 휴리스틱 스캐너(사본)를 유효본 .scn 에 돌려, 일본어 표시문자를
     포함하는데 v2 레코드가 커버하지 않는 런을 보고한다 (추출 누락 검출).

사용
  python migrate_strings.py <v1.json> <v2.json> --archives <arc1> <arc2> ... [--report out.txt]
  (v2.json 은 in-place 로 kr 이 채워진다. --archives 는 우선순위 오름차순 원본.)
"""
import argparse, io, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ujyu.formats import axr as A

JP_CHAR = re.compile(r"[぀-ゟ゠-ヿ一-鿿々〆ぁ-ん]")


def legacy_runs(d):
    """구 miris.axr._sjis_runs 의 사본 — QA 대조 전용."""
    runs = []; i = 0; cur = bytearray(); start = 0
    def lead(b): return (0x81 <= b <= 0x9f) or (0xe0 <= b <= 0xfc)
    def trail(b): return (0x40 <= b <= 0x7e) or (0x80 <= b <= 0xfc)
    while i < len(d):
        b = d[i]
        if lead(b) and i + 1 < len(d) and trail(d[i + 1]):
            if not cur: start = i
            cur += d[i:i + 2]; i += 2
        elif (0xa1 <= b <= 0xdf) or (0x20 <= b < 0x7f):
            if not cur: start = i
            cur.append(b); i += 1
        else:
            if cur:
                try:
                    s = cur.decode('cp932')
                    if any(c > '　' for c in s):
                        runs.append((start, len(cur), s))
                except Exception:
                    pass
            cur = bytearray(); i += 1
    return runs


def main():
    ap = argparse.ArgumentParser(description="strings v1→v2 번역 이관 + 커버리지 QA")
    ap.add_argument("v1"); ap.add_argument("v2")
    ap.add_argument("--archives", nargs="+", required=True,
                    help="원본 시나리오 아카이브 (우선순위 오름차순)")
    ap.add_argument("--report", default="_migrate_report.txt")
    a = ap.parse_args()

    V1 = json.load(open(a.v1, encoding="utf-8"))
    V2 = json.load(open(a.v2, encoding="utf-8"))

    # 1) 번역 이관 — (file, jp) 첫 번역 우선 (구 patch_archives.load_tl 과 동일 규칙)
    tl = {}; spk1 = {}
    for r in V1:
        kr = (r.get("kr") or "").strip()
        if kr:
            tl.setdefault((r["file"], r["jp"]), kr)
        if r.get("speaker"):
            spk1.setdefault((r["file"], r["jp"]), r["speaker"])
    n_mig = 0; used = set()
    for r in V2:
        k = (r["file"], r["jp"])
        if k in tl:
            r["kr"] = tl[k]; used.add(k); n_mig += 1
    unmatched = [k for k in tl if k not in used]

    # 화자 태깅 대조 (v1 tag_speakers vs v2 구조적 태깅)
    spk_diff = []
    for r in V2:
        k = (r["file"], r["jp"])
        if k in spk1 and r.get("speaker") != spk1[k]:
            spk_diff.append((k[0], r.get("id"), spk1[k], r.get("speaker"), r["jp"][:24]))

    # 1b) 역방향 QA — base 아카이브 유효본의 v2 레코드인데 v1 런 어디에도 (부분문자열로도)
    #     없는 것. v1 휴리스틱은 base의 모든 SJIS 런 상위집합이므로, 여기 걸리면 오탐 의심.
    #     (v1 런은 1바이트 오퍼랜드 접두어를 포함할 수 있어 부분문자열 비교)
    jp1_by_file = {}
    for r in V1:
        jp1_by_file.setdefault(r["file"], []).append(r["jp"])
    base = os.path.basename(a.archives[0])
    fabricated = []
    for r in V2:
        if r["arc"] != base or r["kind"] == "sym":
            continue
        runs1 = jp1_by_file.get(r["file"], [])
        if not any(r["jp"] in s for s in runs1):
            fabricated.append(r)

    # 2) 커버리지 QA — 유효본 기준 휴리스틱 런 중 v2 미커버 + 일본어 포함
    owner = {}
    for arc in a.archives:
        d, entries, tbl = A.load(arc)
        for name, off, sz in entries:
            if name.endswith(".scn"):
                owner[name] = A.getfile(d, tbl, off, sz)
    spans = {}
    for r in V2:
        spans.setdefault(r["file"], []).append((r["off"], r["off"] + r["bytelen"]))
    def strip_operands(o, ln, s):
        """v1 런의 선행 1바이트(오퍼랜드: ASCII/반각가나)를 벗긴다 — 표시 텍스트만 남김."""
        k = 0
        for c in s:
            if ord(c) < 0x80 or 0xFF61 <= ord(c) <= 0xFF9F:
                k += 1
            else:
                break
        return o + k, ln - k, s[k:]

    gaps = []
    for name in sorted(owner):
        sp = sorted(spans.get(name, []))
        for o, ln, s in legacy_runs(owner[name]):
            o, ln, s = strip_operands(o, ln, s)
            if not JP_CHAR.search(s):
                continue
            if any(x0 <= o and o + ln <= x1 for x0, x1 in sp):
                continue
            gaps.append((name, o, ln, s))

    out = io.open(a.report, "w", encoding="utf-8")
    out.write("v1 레코드 %d (번역 %d) / v2 레코드 %d\n" % (len(V1), len(tl), len(V2)))
    out.write("이관된 v2 레코드 %d / 미매치 v1 번역 %d\n" % (n_mig, len(unmatched)))
    out.write("화자 불일치 %d\n" % len(spk_diff))
    out.write("역방향(오탐 의심: base v2인데 v1에 없음) %d\n" % len(fabricated))
    out.write("커버리지 갭(일본어 포함, v2 미커버) %d\n\n" % len(gaps))
    if fabricated:
        out.write("=== 오탐 의심 (base v2 ∉ v1) ===\n")
        for r in fabricated[:200]:
            out.write("  %s %s %s @0x%x %r\n"
                      % (r["file"], r["id"], r["kind"], r["off"], r["jp"][:40]))
    if unmatched:
        out.write("=== 미매치 v1 번역 ===\n")
        for f, jp in sorted(unmatched):
            out.write("  %s | %s | KR: %s\n" % (f, jp, tl[(f, jp)]))
    if spk_diff:
        out.write("\n=== 화자 불일치 (v1 → v2) ===\n")
        for f, rid, s1, s2, jp in spk_diff[:200]:
            out.write("  %s %s: %r -> %r | %s\n" % (f, rid, s1, s2, jp))
    if gaps:
        out.write("\n=== 커버리지 갭 ===\n")
        from collections import Counter
        cnt = Counter(s for _, _, _, s in gaps)
        out.write("-- 빈도순 상위 --\n")
        for s, c in cnt.most_common(40):
            out.write("  %5d × %r\n" % (c, s))
        out.write("-- 위치 샘플 --\n")
        for name, o, ln, s in gaps[:300]:
            out.write("  %s @0x%x len=%d %r\n" % (name, o, ln, s))
    out.close()

    json.dump(V2, io.open(a.v2, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("이관 %d / 미매치 %d / 화자불일치 %d / 오탐의심 %d / 커버리지갭 %d -> %s"
          % (n_mig, len(unmatched), len(spk_diff), len(fabricated), len(gaps), a.report))


if __name__ == "__main__":
    main()
