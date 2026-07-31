#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""테스트용 세이브 - 원하는 씬 앞부분부터 시작하는 savedataN.dat 만들기.

근거: docs/formats/SAVE.md. 세이브는 절대 오프셋이 없는 순차 직렬화라, **씬 이름
문자열만 같은 길이로 바꾸면** 뒤가 밀리지 않는다. 씬 CRC 토큰이 어긋나면 엔진이
"해당 시나리오의 선두 부분부터 재개"하므로(§2) 그 씬 처음부터 시작한다.

구조(§1) 중 이 도구가 쓰는 부분:

    <제목 cstr> <성 cstr> <이름 cstr>                    ← 선두 문자열 3개
    ... <u8 플래그> 10 <u8 인덱스> <u32 토큰> <씬이름 cstr>   ← 씬 참조 레코드 여러 개
    ... <u16BE 진행위치> <u16BE 인덱스> <씬이름 cstr> <u32 토큰>  ← 파일 끝 = 현재 위치

  ujyu save show <savefile>                    씬 이름·진행 위치 표시
  ujyu save goto <savefile> <scn이름>          그 씬 앞부분부터 시작하는 세이브 생성

원본 세이브는 절대 고치지 않는다. 결과는 `<savefile>.goto` 또는 `--out` 으로 나간다.
"""
import argparse
import binascii
import os
import re
import struct

# 씬 이름에 쓰이는 문자 (확장자 없는 .scn 이름: 0506_01a 등)
NAME_RE = re.compile(rb"[0-9A-Za-z_][0-9A-Za-z_.\-]{1,30}\x00")
NAME_CH = re.compile(rb"[0-9A-Za-z_.\-]")
REC_MARK = 0x10                                  # VNEG 심볼 참조 마커 (§1-2)


def _safe(s):
    """CP949 콘솔에 못 쓰는 문자를 ? 로 (일본어 기본 이름 등 출력용)."""
    return s.encode("cp949", "replace").decode("cp949")


def _cstr(b, i):
    j = b.find(b"\x00", i)
    if j < 0:
        raise SystemExit("널 종단 문자열이 끊겼다 (오프셋 0x%X) - 세이브가 아닌 듯" % i)
    return b[i:j], j + 1


class Save:
    """savedataN.dat 파서 (읽기 전용). 정수는 전부 big-endian."""

    def __init__(self, path):
        self.path = path
        self.raw = open(path, "rb").read()
        if len(self.raw) < 24:
            raise SystemExit("세이브 파일이 너무 작다: %s (%d B)" % (path, len(self.raw)))
        b = self.raw
        self.title, i = _cstr(b, 0)              # 로드 화면 제목 (본문과 같은 인코딩)
        self.last_name, i = _cstr(b, i)          # 성  (common.csv string,LN 기본값)
        self.first_name, _ = _cstr(b, i)         # 이름(string,FN)

        self.token = b[-4:]                      # 파일 끝 토큰 = 씬 .scn 의 CRC32(BE)
        self.records = self._records()
        self.cur_off = self._current_name_off()
        self.cur_name = _cstr(b, self.cur_off)[0]
        self.pos = struct.unpack(">H", b[self.cur_off - 4:self.cur_off - 2])[0]
        self.index = struct.unpack(">H", b[self.cur_off - 2:self.cur_off])[0]

    def _current_name_off(self):
        """파일 끝 = <u16 위치><u16 인덱스><씬이름 cstr><u32 토큰> 의 씬이름 시작."""
        end = len(self.raw) - 4                  # 토큰 앞 = 널 종단자
        if self.raw[end - 1] != 0:
            raise SystemExit("파일 끝이 <씬이름><토큰> 형태가 아니다 - "
                             "savedataN.dat 가 맞는지 확인하라")
        i = end - 1
        while i > 5 and NAME_CH.match(self.raw[i - 1:i]):
            i -= 1
        run = self.raw[i:end - 1]
        if not run:
            raise SystemExit("현재 씬 이름을 찾지 못했다 - 세이브 형식이 다르다")
        # 진행위치·인덱스 바이트가 우연히 이름 글자로 읽힐 수 있다. 레코드에 있는
        # 씬 이름과 끝이 맞으면 그 길이를 믿는다 (레코드는 마커 0x10 으로 확실하다).
        same = [r["name"] for r in self.records if run.endswith(r["name"])]
        if same:
            run = max(same, key=len)
        return end - 1 - len(run)

    def _records(self):
        """<플래그> 10 <인덱스> <토큰> <씬이름> 레코드 목록."""
        out = []
        for m in NAME_RE.finditer(self.raw):
            p = m.start()
            if p >= 7 and self.raw[p - 6] == REC_MARK:
                out.append({
                    "off": p,
                    "name": m.group()[:-1],
                    "flag": self.raw[p - 7],
                    "index": self.raw[p - 5],
                    "token": self.raw[p - 4:p],
                })
        return out

    def scene_names(self):
        names = {r["name"] for r in self.records}
        names.add(self.cur_name)
        return sorted(names)

    # ── goto: 씬 이름 치환 + 진행위치 0
    def goto(self, new_name, pool=()):
        old = self.cur_name
        if not re.fullmatch(rb"[0-9A-Za-z_][0-9A-Za-z_.\-]*", new_name):
            raise SystemExit("씬 이름에 쓸 수 없는 문자가 있다: %r "
                             "(확장자 없는 .scn 이름을 쓴다)" % new_name.decode("latin1"))
        if len(new_name) != len(old):
            cand = [n.decode() for n in self.scene_names()] + list(pool)
            same = sorted({c for c in cand if len(c) == len(old)})
            msg = ["씬 이름 길이가 다르다: 요청 %r(%dB) != 현재 %r(%dB)"
                   % (new_name.decode(), len(new_name), old.decode(), len(old)),
                   "  같은 길이로 바꿔야 뒤쪽 오프셋이 밀리지 않는다 (SAVE.md 3절).",
                   "  쓸 수 있는 %dB 씬 이름: %s"
                   % (len(old), ", ".join(same) if same else "(찾지 못함)")]
            if not pool:
                msg.append("  더 많은 후보: --archive <scenario.axr> 를 함께 주라")
            msg.append("  길이가 다른 씬으로 가려면 그 씬 이름 길이의 세이브를 "
                       "먼저 만들어야 한다(다른 슬롯에서 세이브).")
            raise SystemExit("\n".join(msg))
        b = bytearray(self.raw)
        n = 0
        i = b.find(old + b"\x00")
        while i >= 0:
            b[i:i + len(old)] = new_name
            n += 1
            i = b.find(old + b"\x00", i + 1)
        b[self.cur_off - 4:self.cur_off - 2] = b"\x00\x00"     # 진행 위치 -> 씬 처음
        return bytes(b), n


def _archive_names(archive):
    """아카이브 안 .scn 엔트리 이름(확장자 제거). 실패하면 빈 목록."""
    try:
        from ujyu.formats import axr
        _, entries, _tbl = axr.load(archive)
        return sorted(os.path.splitext(n)[0] for n, _o, _s in entries
                      if n.lower().endswith(".scn"))
    except Exception as e:                       # 포맷·경로 문제는 치명적이지 않다
        print("경고: 아카이브를 읽지 못했다: %s (%s)" % (archive, e))
        return []


def cmd_show(a):
    s = Save(a.savefile)
    print("파일: %s (%d bytes)" % (s.path, len(s.raw)))
    print("제목: %s" % _safe(s.title.decode("cp949", "replace")))
    print("기본 이름: %s / %s"
          % (_safe(s.last_name.decode("cp932", "replace")),
             _safe(s.first_name.decode("cp932", "replace"))))
    print("현재 씬: %s   진행 위치 %d (0x%04X), 인덱스 %d"
          % (s.cur_name.decode(), s.pos, s.pos, s.index))
    print("토큰(파일 끝 CRC32 BE): %s" % binascii.hexlify(s.token, " ").decode())
    print("씬 참조 레코드 %d개:" % len(s.records))
    for r in s.records:
        same = "=" if r["token"] == s.token else "!"
        print("  @0x%03X  플래그 %02X 인덱스 %3d 토큰 %s %s  %s"
              % (r["off"], r["flag"], r["index"],
                 binascii.hexlify(r["token"], " ").decode(), same, r["name"].decode()))
    print("(토큰 ! = 다른 씬의 CRC. 분기 씬을 함께 참조하는 세이브다)")
    print("이 세이브에 든 씬 이름: %s"
          % ", ".join(n.decode() for n in s.scene_names()))
    if a.archive:
        names = _archive_names(a.archive)
        same = [n for n in names if len(n) == len(s.cur_name)]
        print("아카이브 %s: .scn %d개, 현재와 같은 길이(%dB) %d개"
              % (a.archive, len(names), len(s.cur_name), len(same)))
        if same:
            print("  goto 로 바로 쓸 수 있는 씬: %s" % ", ".join(same))
    return 0


def cmd_goto(a):
    s = Save(a.savefile)
    new = a.scn.encode("latin1", "replace")
    if a.scn.lower().endswith(".scn"):
        new = new[:-4]
    out = a.out or (a.savefile + ".goto")
    if os.path.abspath(out) == os.path.abspath(a.savefile):
        raise SystemExit("입력 세이브를 제자리에서 고칠 수 없다 - --out 에 다른 경로를 주라")
    parent = os.path.dirname(os.path.abspath(out))
    if not os.path.isdir(parent):
        raise SystemExit("출력 폴더가 없다: %s" % parent)
    pool = _archive_names(a.archive) if a.archive else ()
    if pool and new.decode() not in pool:
        print("경고: %s 에 %s.scn 이 없다 - 이름을 확인하라" % (a.archive, new.decode()))
    data, n = s.goto(new, pool)
    with open(out, "wb") as f:
        f.write(data)
    print("현재 씬 %s (진행 위치 %d) -> %s (진행 위치 0)"
          % (s.cur_name.decode(), s.pos, new.decode()))
    print("씬 이름 %d곳 치환, %d bytes 저장: %s" % (n, len(data), out))
    print("토큰은 그대로라 어긋난다 - 엔진이 그 씬 선두부터 재개한다 (SAVE.md 2절).")
    print("게임에 넣기: 이 파일을 <게임>/save/savedataN.dat 로 복사 (빈 슬롯 번호로)")
    return 0


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu save",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="테스트용 세이브 조회/생성 (원하는 씬 앞부분부터 시작)",
        epilog="원본 세이브는 고치지 않는다. goto 결과는 <savefile>.goto 로 나간다.\n"
               "예:\n"
               "  ujyu save show save/savedata1.dat\n"
               "  ujyu save show save/savedata1.dat --archive orig/scenario.axr\n"
               "  ujyu save goto save/savedata1.dat 0506_02\n"
               "  ujyu save goto save/savedata1.dat 0512_01 --out _test/savedata9.dat\n")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<하위>")

    p = sub.add_parser("show", help="세이브의 씬 이름·진행 위치·토큰 표시")
    p.add_argument("savefile", help="읽을 savedataN.dat 경로")
    p.add_argument("--archive", help="시나리오 아카이브(.axr) - 같은 길이 씬 이름 후보 안내")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("goto", help="그 씬 앞부분부터 시작하는 세이브 만들기")
    p.add_argument("savefile", help="바탕이 될 savedataN.dat (수정하지 않는다)")
    p.add_argument("scn", help="시작할 씬 이름 (확장자 없이, 현재 씬과 같은 길이)")
    p.add_argument("--out", "-o", help="출력 경로 (기본: <savefile>.goto)")
    p.add_argument("--archive", help="시나리오 아카이브(.axr) - 씬 이름 확인·후보 안내")
    p.set_defaults(fn=cmd_goto)

    a = ap.parse_args()
    if not os.path.exists(a.savefile):
        raise SystemExit("세이브 파일이 없다: %s" % a.savefile)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
