#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전체 언팩 - 한글 패치에 필요한 모든 파일을 풀고 디스어셈블한다.

기존 포맷 처리기를 엮어 돌리는 **오케스트레이터**다(서브프로세스 없이 직접 호출):

  ujyu.formats.axr    아카이브  -> <out>/unpack/<아카이브>/
  ujyu.formats.vneg   .scn      -> <out>/disasm/<아카이브>/<씬>.txt

대상 아카이브는 config.ARCHIVES + config.CG_ARCHIVE + 원본 폴더에서 발견한 나머지
`.axr/.ax2...` 전부다. config.PASSTHROUGH_ARCHIVES(음성·BGM 등 비번역 대용량)는
기본 제외하고 `--all` 로 포함한다.

원본(config.ORIG_DIR)은 **읽기만** 한다. 출력은 전부 `--out`(기본 config.WORK_DIR) 아래.
"""
import argparse
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from ujyu.titleconfig import config as C          # noqa: E402
from ujyu.formats import axr, vneg                # noqa: E402

# 아카이브 파일명: .axr / .ax2 / .ax3 ...
_ARC_RE = re.compile(r"\.ax(?:r|\d+)$", re.I)

# 표에 세로로 세울 종류 열. 나머지는 '기타' 로 합친다.
_KIND_COLS = ["VNEG", "PNG", "DMJ0"]


def _size(n):
    """사람이 읽는 크기."""
    if n >= 1 << 20:
        return "%.1fMB" % (n / float(1 << 20))
    if n >= 1024:
        return "%.0fKB" % (n / 1024.0)
    return "%dB" % n


def _width(s):
    """CP949 기준 표시 폭(한글·한자는 2칸)."""
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


def _pad(s, n):
    return s + " " * max(0, n - _width(s))


def discover(orig_dir):
    """원본 폴더의 아카이브 목록. config 에 적힌 것을 앞에, 나머지는 이름순."""
    try:
        found = sorted(f for f in os.listdir(orig_dir)
                       if _ARC_RE.search(f) and os.path.isfile(os.path.join(orig_dir, f)))
    except OSError as e:
        raise SystemExit("원본 폴더를 읽을 수 없습니다: %s (%s)" % (orig_dir, e))
    order = []
    named = [os.path.basename(a) for a in (list(getattr(C, "ARCHIVES", None) or []))]
    cg = getattr(C, "CG_ARCHIVE", None)
    if cg:
        named.append(os.path.basename(cg))
    for n in named:
        if n in found and n not in order:
            order.append(n)
    for n in found:
        if n not in order:
            order.append(n)
    return order, [n for n in named if n not in found]


def select(names, only=None, include_all=False):
    """대상 선별. 반환 (대상, 제외된 대용량).

    `only` 를 주면 그것만 쓴다(대용량 제외 규칙보다 우선).
    """
    passthrough = {os.path.basename(p).lower()
                   for p in (getattr(C, "PASSTHROUGH_ARCHIVES", None) or [])}
    keep, skipped = [], []
    for n in names:
        if only is not None:
            if not ({n.lower(), os.path.splitext(n)[0].lower()} & only):
                continue
        elif not include_all and n.lower() in passthrough:
            skipped.append(n)
            continue
        keep.append(n)
    return keep, skipped


def entry_kinds(data, table, entries):
    """엔트리 종류 분포 {종류: 개수} (앞 4바이트 시그니처 기준)."""
    kinds = Counter()
    for name, off, sz in entries:
        head = axr.getfile(data, table, off, min(4, sz)) if sz else b""
        kinds[axr.kind_of(name, head)] += 1
    return kinds


def _enough(out_dir, need):
    """이미 풀린 결과가 있는가(파일 수가 필요 수 이상)."""
    if need <= 0 or not os.path.isdir(out_dir):
        return False
    try:
        return len(os.listdir(out_dir)) >= need
    except OSError:
        return False


def unpack_one(data, table, entries, out_dir):
    """아카이브 엔트리를 폴더로 쓴다. 반환 = 쓴 개수."""
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for name, off, sz in entries:
        dst = os.path.join(out_dir, name.replace("/", os.sep))
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dst, "wb") as f:
            f.write(axr.getfile(data, table, off, sz))
        n += 1
    return n


def disasm_one(data, table, scns, out_dir, opstat):
    """.scn 엔트리를 VNEG 디스어셈블해 <out_dir>/<씬>.txt 로 쓴다. 반환 = 쓴 개수."""
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for name, off, sz in scns:
        txt = vneg.disasm(axr.getfile(data, table, off, sz), opstat)
        dst = os.path.join(out_dir, os.path.splitext(os.path.basename(name))[0] + ".txt")
        with open(dst, "w", encoding="utf-8") as f:
            f.write(txt)
        n += 1
    return n


def process(name, path, out_root, do_disasm=True, force=False, opstat=None):
    """아카이브 하나를 풀고(필요하면) 디스어셈블한다. 반환 = 표에 실을 행 dict."""
    data, entries, table = axr.load(path)
    kinds = entry_kinds(data, table, entries)
    row = {"name": name, "count": len(entries), "kinds": kinds,
           "bytes": sum(sz for _n, _o, sz in entries),
           "unpack": "", "disasm": "-", "error": None}

    up_dir = os.path.join(out_root, "unpack", name)
    if _enough(up_dir, len(entries)) and not force:
        row["unpack"] = "스킵"
    else:
        unpack_one(data, table, entries, up_dir)
        row["unpack"] = "풀었음"

    scns = [e for e in entries if e[0].lower().endswith(".scn")]
    if do_disasm and scns:
        dis_dir = os.path.join(out_root, "disasm", name)
        if _enough(dis_dir, len(scns)) and not force:
            row["disasm"] = "스킵"
        else:
            row["disasm"] = "%d개" % disasm_one(data, table, scns, dis_dir,
                                                opstat if opstat is not None else Counter())
    elif scns:
        row["disasm"] = "생략"
    return row


def print_table(rows):
    """아카이브별 요약 표."""
    w = max([_width(r["name"]) for r in rows] + [8])
    head = "%s %6s %6s %6s %6s %6s %9s  %s %s" % (
        _pad("아카이브", w), "엔트리", "VNEG", "PNG", "DMJ0", "기타", "크기",
        _pad("언팩", 8), "디스어셈블")
    print(head)
    print("-" * (_width(head) + 1))
    tot = Counter()
    for r in rows:
        if r["error"]:
            print("%s %s" % (_pad(r["name"], w), "실패: %s" % r["error"]))
            continue
        k = r["kinds"]
        etc = r["count"] - sum(k.get(c, 0) for c in _KIND_COLS)
        print("%s %6d %6d %6d %6d %6d %9s  %s %s"
              % (_pad(r["name"], w), r["count"], k.get("VNEG", 0), k.get("PNG", 0),
                 k.get("DMJ0", 0), etc, _size(r["bytes"]), _pad(r["unpack"], 8),
                 r["disasm"]))
        tot["count"] += r["count"]
        tot["bytes"] += r["bytes"]
        for c in _KIND_COLS:
            tot[c] += k.get(c, 0)
        tot["etc"] += etc
    print("-" * (_width(head) + 1))
    print("%s %6d %6d %6d %6d %6d %9s"
          % (_pad("합계", w), tot["count"], tot["VNEG"], tot["PNG"], tot["DMJ0"],
             tot["etc"], _size(tot["bytes"])))


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu unpack",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="한글 패치에 필요한 모든 아카이브를 풀고 .scn 을 디스어셈블한다",
        epilog="예:\n"
               "  ujyu unpack                              # 전부 (음성·BGM 등 대용량 제외)\n"
               "  ujyu unpack --only scenario.axr,cg.axr   # 일부만\n"
               "  ujyu unpack --all --no-disasm            # 대용량까지, 디스어셈블은 생략\n"
               "\n원본(config.ORIG_DIR)은 읽기만 한다. 출력: <out>/unpack/<아카이브>/ 와\n"
               "<out>/disasm/<아카이브>/<씬>.txt. 이미 풀린 결과는 건너뛴다(--force 로 덮어쓰기).")
    ap.add_argument("--out", metavar="DIR", default=None,
                    help="출력 루트 (기본: config.WORK_DIR)")
    ap.add_argument("--orig", metavar="DIR", default=None,
                    help="원본(무패치) 게임 폴더 (기본: config.ORIG_DIR)")
    ap.add_argument("--only", metavar="목록", default=None,
                    help="이 아카이브만, 쉼표 구분 (확장자 생략 시 같은 이름 전부: scenario)")
    ap.add_argument("--all", dest="every", action="store_true",
                    help="config.PASSTHROUGH_ARCHIVES(음성·BGM 등)도 포함")
    ap.add_argument("--no-disasm", dest="disasm", action="store_false",
                    help=".scn VNEG 디스어셈블을 건너뛴다")
    ap.add_argument("--force", action="store_true",
                    help="이미 풀린 결과를 덮어쓴다")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="VNEG opcode 사용 통계까지 표시")
    a = ap.parse_args()

    orig = a.orig or getattr(C, "ORIG_DIR", None)
    if not orig or not os.path.isdir(orig):
        raise SystemExit("원본 게임 폴더가 없습니다: %s (config.ORIG_DIR 확인)" % orig)
    out_root = a.out or getattr(C, "WORK_DIR", None)
    if not out_root:
        raise SystemExit("출력 폴더를 정할 수 없습니다: --out 을 주거나 config.WORK_DIR 을 채우세요.")
    only = set(s.strip().lower() for s in a.only.split(",") if s.strip()) if a.only else None

    names, missing = discover(orig)
    targets, skipped = select(names, only, a.every)
    if not targets:
        raise SystemExit("대상 아카이브가 없습니다 (--only 값을 확인하세요): %s"
                         % (a.only or ", ".join(names)))

    print("ujyu unpack - 전체 언팩/디스어셈블")
    print("원본   : %s" % orig)
    print("출력   : %s" % out_root)
    print("대상   : %d개 (%s)" % (len(targets), ", ".join(targets)))
    if skipped:
        print("제외   : %s  (대용량 비번역, 포함하려면 --all)" % ", ".join(skipped))
    if missing:
        print("경고   : config 에 있으나 원본에 없음: %s" % ", ".join(missing))
    print()

    opstat = Counter()
    rows, fails = [], []
    for name in targets:
        path = os.path.join(orig, name)
        try:
            rows.append(process(name, path, out_root, a.disasm, a.force, opstat))
        except Exception as e:                      # 한 아카이브 실패로 전체를 멈추지 않는다
            rows.append({"name": name, "error": "%s: %s" % (type(e).__name__, e),
                         "count": 0, "kinds": Counter(), "bytes": 0,
                         "unpack": "실패", "disasm": "-"})
            fails.append(name)

    print_table(rows)
    print()
    print("언팩     : %s" % os.path.join(out_root, "unpack"))
    if any(r["disasm"] not in ("-", "생략") for r in rows):
        print("디스어셈블: %s" % os.path.join(out_root, "disasm"))
    if a.verbose and opstat:
        print("\nVNEG opcode 사용 통계:")
        for op, c in opstat.most_common():
            print("  OP%02x %-11s : %d" % (op, vneg.OP_TYPES.get(op, ""), c))
    if fails:
        print("\n실패 %d개: %s" % (len(fails), ", ".join(fails)))
        return 1
    print("\n다음: ujyu extract (번역용 텍스트·정보 추출)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
