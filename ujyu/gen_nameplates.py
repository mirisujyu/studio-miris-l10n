# -*- coding: utf-8 -*-
"""네임플레이트(화자명) 대응표 생성 — `NAMEPLATES.md`

입력:
  `_names.json`      strings.json(v2)의 speaker 집계 [(원문 화자명, 대사수)] 목록
  `nameplates.json`  번역표 {원문: [한국어, 읽기, 분류, 비고]}   ← **타이틀 종속 데이터**

번역표는 코드가 아니라 데이터다. 새 타이틀에서는 `nameplates.json` 만 채우면 된다
(구조는 `samples/nameplates.sample.json` 참조). 채우는 방법은 SKILL.md 11절.
"""
import argparse
import json, io, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ujyu.titleconfig import config as C

# 분류별 정렬 순서. nameplates.json 에서 쓰는 분류명과 맞추면 된다.
ORDER = {"주역": 0, "총칭": 0, "인간(주역)": 1, "인간": 2,
         "별명/애칭": 3, "별명": 3, "역할명": 4, "동물": 5, "플레이스홀더": 6}


def aggregate(records, kinds=("dlg",)):
    """strings.json(v2) 레코드 -> [[화자, 대사수]] (많은 순). `_names.json` 과 같은 구조.

    **대사 본문(`dlg`)만 센다.** 한 대사는 여는 괄호(`quote`)와 본문(`dlg`)이 각각
    레코드이고 둘 다 speaker 를 갖는다 — 전부 세면 대사 수가 두 배로 부풀려진다.
    """
    from collections import Counter
    c = Counter()
    for r in records:
        sp = r.get("speaker") or ""
        if not sp or (kinds is not None and r.get("kind") not in kinds):
            continue
        # str.strip() 은 전각 공백(U+3000)까지 지운다. 공백만으로 된 네임플레이트는
        # 실제로 쓰이는 플레이스홀더이므로(익명 화자) 사라지지 않게 원본을 남긴다.
        c[sp.strip() or sp] += 1
    return [[nm, n] for nm, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]


def load_table(path):
    """번역표(nameplates.json) -> {원문: (한국어, 읽기, 분류, 비고)}."""
    try:
        return {k: tuple(v) for k, v in
                json.load(io.open(path, encoding="utf-8")).items()}
    except FileNotFoundError:
        raise SystemExit(
            "번역표가 없습니다: %s\n"
            "  samples/nameplates.sample.json 을 복사해 채우세요 (SKILL.md 11절)." % path)


def build_md(names, T, out_path):
    """집계 결과 + 번역표로 NAMEPLATES.md 를 쓴다. 반환 (행수, 번역표 누락 화자들)."""
    rows = []
    for nm, cnt in names:
        kr, yomi, cat, note = T.get(nm, ("", "", "", ""))
        rows.append((ORDER.get(cat, 9), -cnt, nm, yomi, kr, cnt, cat, note))
    rows.sort()

    missing = [nm for nm, _ in names if nm not in T]

    out = io.open(out_path, "w", encoding="utf-8")
    out.write("""# 네임플레이트(화자명) 표

> 각 대사의 화자(네임플레이트 이름)는 `ujyu scn extract` 가 SPEAKER opcode(`0d 33`)에서
> 추출해 `strings.json` 의 `speaker` 필드로 저장한다.
> ※ = 읽기/표기 확인 필요(검수 대상). **살아있는 문서** — 번역하며 갱신한다.

## 표기 원칙
- 인명은 **발음 음역**. 읽기 확정 전에는 ※ 표시.
- 역할명(男/少年/村人 등)은 의미 번역. 플레이스홀더(＊/？？？/공백)는 원문 유지.
- 네임플레이트 창이 좁으므로 **짧게** 짓는다 (SKILL.md 12-3).

| 원문(네임플레이트) | 읽기 | 한국어 | 대사수 | 분류 | 비고 |
|---|---|---|---|---|---|
""")
    for _, _, nm, yomi, kr, cnt, cat, note in rows:
        out.write("| %s | %s | %s | %d | %s | %s |\n"
                  % (nm, yomi or "-", kr or "**미번역**", cnt, cat, note))
    if missing:
        out.write("\n> ⚠️ 번역표에 없는 화자 %d개: %s\n"
                  % (len(missing), ", ".join(missing)))
    out.close()
    return len(rows), missing


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu nameplates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="화자명 대응표 문서 생성 (_names.json + nameplates.json -> NAMEPLATES.md)",
        epilog="입출력은 config 고정: config.NAMEPLATES -> config.NAMEPLATES_MD\n"
               "예:\n"
               "  ujyu nameplates   # 번역표를 채운 뒤 문서를 다시 뽑는다\n")
    ap.parse_args()

    names = json.load(io.open(C.work("_names.json"), encoding="utf-8"))
    T = load_table(C.NAMEPLATES)
    n, missing = build_md(names, T, C.NAMEPLATES_MD)

    print("NAMEPLATES.md 생성: %d행" % n)
    if missing:
        print("  번역표 누락 %d개: %s" % (len(missing), ", ".join(missing[:10])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
