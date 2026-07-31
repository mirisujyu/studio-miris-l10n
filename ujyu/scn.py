# -*- coding: utf-8 -*-
"""scn — VNEG(.scn) CLI (miris.vneg 래퍼). 디스어셈블 + 텍스트 추출 + 점프테이블 재매핑.

  ujyu scn disasm  <archive|.scn> ... [-o out] [--src-dir DIR]
  ujyu scn extract <원본아카이브(우선순위 오름차순)> ... -o strings.json
                              [--resource-re RE]      (구조적 텍스트 추출 → v2 strings)
  ujyu scn relocate --jp-dir <원본게임> <번역아카이브> ...   (in-place 재매핑)

extract 는 아카이브 override 규칙(번호 큰 쪽 우선)대로 **유효본만** 추출한다.
레코드: {arc, file, id, kind, off, bytelen, jp[, speaker], kr}.
id는 DB 호환용 참조(`sym:<정의순번>` / `flow:<디코드순번>`)다.
flow 바이트코드의 심볼참조와 디스어셈블 `#id`는 별도의 런타임 슬롯 인덱스다.
"""
import io, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ujyu.formats import vneg

_JP = re.compile(r"[぀-ヿ一-鿿]")


def _csv_values(buf):
    """common.csv 의 `string,<키>,<값>` 줄에서 (값 오프셋, 길이, 키, 값) 을 뽑는다.

    `#` 주석은 개발자 메모라 제외. 값은 줄 끝까지(쉼표 포함 가능).
    오프셋은 파일 내 바이트 위치라 주입 시 그 자리만 교체하면 된다.
    """
    out = []
    pos = 0
    for raw in buf.split(b"\r\n"):
        if not raw.startswith(b"#"):
            t = raw.split(b",", 2)
            if len(t) == 3 and t[0] == b"string":
                voff = pos + len(t[0]) + 1 + len(t[1]) + 1
                try:
                    out.append((voff, len(t[2]), t[1].decode("ascii", "replace"),
                                t[2].decode("cp932")))
                except UnicodeDecodeError:
                    pass
        pos += len(raw) + 2
    return out


def cmd_extract(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="ujyu scn extract",
                                 description="원본 아카이브에서 구조적 텍스트 추출 (유효본 기준)")
    ap.add_argument("archives", nargs="+",
                    help="원본 시나리오 아카이브, 우선순위 오름차순 (scenario.axr scenario.ax2 ...)")
    ap.add_argument("-o", "--out", required=True, help="출력 strings.json")
    ap.add_argument("--resource-re", default=None, help="리소스명 패턴 (화자 오인 방지)")
    a = ap.parse_args(argv)
    A = vneg.load_axr_tool()

    owner = {}                            # 파일명 -> (아카이브명, bytes). 뒤(번호 큰 쪽)가 승자
    csvs = []                             # common.csv 는 아카이브마다 내용이 달라 전부 담는다
    for arc in a.archives:
        d, entries, tbl = A.load(arc)
        base = os.path.basename(arc)
        for name, off, sz in entries:
            if name.endswith(".scn"):
                owner[name] = (base, A.getfile(d, tbl, off, sz))
            elif name.lower() == "common.csv":
                csvs.append((base, name, A.getfile(d, tbl, off, sz)))

    rows = []; kinds = {}; nonvneg = []
    for name in sorted(owner):
        base, scn = owner[name]
        recs = vneg.extract(scn, a.resource_re)
        if not recs and scn[:4] != b"VNEG":
            nonvneg.append(name)
        for r in recs:
            row = {"arc": base, "file": name, "id": r["id"], "kind": r["kind"],
                   "off": r["off"], "bytelen": r["len"], "jp": r["jp"]}
            if r.get("speaker"):
                row["speaker"] = r["speaker"]
            row["kr"] = ""
            rows.append(row)
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1

    # common.csv 의 표시 값(`string,<키>,<값>`) 중 일본어가 있는 것도 같은 DB 로.
    # 아카이브마다 내용이 달라 arc 별로 각각 담는다(제자리 편집 원칙, COMMON_CSV.md).
    for base, name, buf in csvs:
        for off, ln, key, val in _csv_values(buf):
            if not _JP.search(val):
                continue
            rows.append({"arc": base, "file": name, "id": "csv:%s" % key, "kind": "csv",
                         "off": off, "bytelen": ln, "jp": val, "kr": ""})
            kinds["csv"] = kinds.get("csv", 0) + 1

    json.dump(rows, io.open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("추출 %d조각 (%d파일 + common.csv %d개) -> %s" % (len(rows), len(owner), len(csvs), a.out))
    for k in sorted(kinds):
        print("  %-6s %d" % (k, kinds[k]))
    if nonvneg:
        print("  ⚠️ VNEG 아님(추출 0): %s" % ", ".join(nonvneg))


def main():
    argv = sys.argv[1:]
    mode = argv[0] if argv else "disasm"
    if mode == "extract":
        cmd_extract(argv[1:])
    elif mode == "relocate":
        import argparse
        ap = argparse.ArgumentParser(prog="ujyu scn relocate",
                                     description="번역 아카이브 점프테이블 flow상대 오프셋 재매핑(in-place)")
        ap.add_argument("archives", nargs="+", help="번역 아카이브들 (예: <배포>/scenario.axr ...)")
        ap.add_argument("--jp-dir", required=True, help="원본 게임 디렉토리(동명 아카이브 대조)")
        a = ap.parse_args(argv[1:])
        for arc in a.archives:
            vneg.relocate_archive(arc, a.jp_dir)
    else:
        if mode == "disasm":
            sys.argv = [sys.argv[0]] + argv[1:]   # 'disasm' 토큰 제거 후 vneg.main 위임
        vneg.main()


if __name__ == "__main__":
    main()
