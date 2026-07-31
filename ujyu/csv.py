# -*- coding: utf-8 -*-
"""csv — 아카이브 안 common.csv 조회/편집 CLI (miris.common_csv + miris.axr).

  ujyu csv show <archive.axr>                 # 모든 필드 나열
  ujyu csv get  <archive.axr> <type> <name>   # 값 조회
  ujyu csv todo <archive.axr>                 # 번역 안 된 일본어 값 찾기
설정값 일괄 적용(제목 등)은 ujyu title 참조.
"""
import os, sys, argparse
try:                     # 콘솔이 CP949 여도 일본어 출력이 죽지 않게
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ujyu.formats import axr as A, common_csv as CSV


def _common_csv(path):
    data, entries, tbl = A.load(path)
    for n, o, s in entries:
        if n == "common.csv":
            return A.getfile(data, tbl, o, s)
    return None


JP_RE = None

def cmd_todo(buf):
    """표시 값 중 일본어가 남은 `string,<키>,<값>` 줄을 찾는다.

    `#` 주석 줄은 개발자 메모라 화면에 나오지 않으므로 제외한다.
    여기 나오는 키를 `config.COMMON_CSV` 에 등록하면 patch_title 이 번역을 넣는다.
    """
    global JP_RE
    import re
    if JP_RE is None:
        JP_RE = re.compile(r"[぀-ヿ一-鿿]")
    n = 0
    for raw in buf.splitlines():
        if raw.startswith(b"#"):
            continue
        try:
            s = raw.decode("cp932")
        except UnicodeDecodeError:
            continue
        t = s.split(",")
        if len(t) >= 3 and t[0] == "string" and JP_RE.search(",".join(t[2:])):
            print("  %-14s %s" % (t[1], ",".join(t[2:])))
            n += 1
    print("번역 필요 %d개 (config.COMMON_CSV 에 키→한국어 등록)" % n)


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu csv", description="아카이브 안 common.csv 필드 조회",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="예:\n"
               "  ujyu csv show 원본/scenario.axr\n"
               "  ujyu csv get 원본/scenario.axr string title")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("show", help="common.csv 의 모든 필드 나열")
    p.add_argument("archive", help="common.csv 가 든 아카이브 (.axr/.ax2/…)")

    p = sub.add_parser("get", help="필드 하나의 값 조회")
    p.add_argument("archive", help="common.csv 가 든 아카이브 (.axr/.ax2/…)")
    p.add_argument("type", help="필드 타입 (예: string, int)")
    p.add_argument("name", help="필드 이름(키)")

    p = sub.add_parser("todo", help="번역 안 된 일본어 표시 값 찾기")
    p.add_argument("archive", help="common.csv 가 든 아카이브 (.axr/.ax2/…)")

    a = ap.parse_args()
    if not a.cmd:
        ap.print_help(); return
    buf = _common_csv(a.archive)
    if buf is None:
        print("common.csv 없음:", a.archive); return
    if a.cmd == "show":
        for typ, name, rest in CSV.fields(buf):
            try: r = rest.decode("cp949")
            except UnicodeDecodeError: r = rest.decode("cp932", "replace")
            print("  %-8s %-16s %s" % (typ, name, r))
    elif a.cmd == "get":
        v = CSV.get_field(buf, a.type, a.name)
        print(v if v is None else v.decode("cp949", "replace"))
    elif a.cmd == "todo":
        cmd_todo(buf)


if __name__ == "__main__":
    main()
