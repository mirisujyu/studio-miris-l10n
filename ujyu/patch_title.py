#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
창 제목 패치 — 아카이브 안 `common.csv` 의 `string,title,...`

창 제목은 exe 가 아니라 **아카이브 안 `common.csv`** 에 있다:

    string,title,<제목>

게임은 이것을 `<제목> ～ [씬 제목] ～` 형태로 조립해 `SetWindowTextA` 로 넘긴다.
SJIS 인 채로 두면 한국어 Windows(ACP=949)에서 깨진 한글이 된다.

`common.csv` 는 **모든 시나리오 아카이브에** 들어 있고 수정(패치) 아카이브가
뒤쪽 우선이므로, `config.ARCHIVES` 에 나열된 것을 전부 패치한다.

설정할 항목은 `config.COMMON_CSV` 에서 가져온다.

사용:
  ujyu title            현재 상태 확인 (= ujyu title show)
  ujyu title apply      전 아카이브 패치 (원본은 .bak 로 보존)
"""
import sys, os, shutil, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from ujyu.formats import axr as A, common_csv as CSV
from ujyu.titleconfig import config as C
GAME = C.GAME_DIR
ARCHIVES = C.ARCHIVES

# common.csv 의 `string,<키>[,<값>]` 을 이 값으로 설정한다 (config.py 에서 정의).
CONFIG = C.COMMON_CSV


def _load(path):
    data, entries, tbl = A.load(path)
    blk2 = int.from_bytes(open(path, "rb").read()[8:12], "little")
    files = [[n, bytearray(A.getfile(data, tbl, o, sz))] for n, o, sz in entries]
    return files, blk2


def show():
    for a in ARCHIVES:
        p = os.path.join(GAME, a)
        if not os.path.exists(p):
            print("  %-14s (없음)" % a); continue
        files, _ = _load(p)
        for n, buf in files:
            if n != "common.csv":
                continue
            b = bytes(buf)
            out = []
            for key in CONFIG:
                cur = CSV.get_field(b, "string", key)
                if cur is None:
                    out.append("%s=(항목없음)" % key); continue
                try:
                    txt, enc = cur.decode("cp949"), "CP949"
                except UnicodeDecodeError:
                    txt, enc = cur.decode("cp932", "replace"), "SJIS"
                out.append("%s=%r(%s)" % (key, txt, enc))
            print("  %-14s %s" % (a, "  ".join(out)))


def apply():
    for a in ARCHIVES:
        p = os.path.join(GAME, a)
        if not os.path.exists(p):
            print("  %-14s 없음 — 건너뜀" % a); continue
        files, blk2 = _load(p)
        done = []
        for pair in files:
            if pair[0] != "common.csv":
                continue
            b = bytes(pair[1])
            for key, val in CONFIG.items():
                if not CSV.has_field(b, "string", key):
                    continue
                nb = CSV.set_field(b, "string", key, val.encode("cp949"))
                done.append(key + ("(이미)" if nb == b else ""))
                b = nb
            pair[1] = bytearray(b)
        if not done:
            print("  %-14s common.csv 항목 없음" % a); continue
        bak = p + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(p, bak)
        open(p, "wb").write(A.pack([(n, bytes(x)) for n, x in files], blk2))
        print("  %-14s 적용: %s" % (a, ", ".join(done)))


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu title",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="아카이브 안 common.csv 의 창 제목 등 설정 값을 조회·패치",
        epilog="예:\n"
               "  ujyu title          # 현재 값 확인 (기본 = show)\n"
               "  ujyu title apply    # 전 아카이브 패치 (원본은 .bak 로 보존)\n")
    sub = ap.add_subparsers(dest="cmd", metavar="<명령>")
    sub.add_parser("show", help="현재 common.csv 값 확인 (인자 없으면 기본 동작)",
                   description="배포 폴더 아카이브의 common.csv 현재 값을 인코딩과 함께 보여준다")
    sub.add_parser("apply", help="config.ARCHIVES 전부 패치 (원본은 .bak 로 보존)",
                   description="config.COMMON_CSV 의 값을 전 아카이브 common.csv 에 CP949 로 넣는다")
    a = ap.parse_args()

    print("common.csv 설정: %s" % CONFIG)
    if a.cmd == "apply":
        apply()
        print("\n적용 후:")
    show()


if __name__ == "__main__":
    raise SystemExit(main())
