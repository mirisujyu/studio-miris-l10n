# -*- coding: utf-8 -*-
"""x86 exe 디스어셈블 헬퍼 (capstone + pefile).

엔진 내부 동작을 추적할 때 쓴다. 주소는 **RVA 가 아니라 파일 오프셋도 VA 도 아닌
`ImageBase + RVA` = 런타임 VA** 로 주고받는다(문서·디버거와 같은 기준).

  ujyu exe disasm <exe> at   <VA> [개수]     지정 주소부터 N개 명령
  ujyu exe disasm <exe> fn   <VA> [최대]     ret/무조건점프까지 (함수 1개)
  ujyu exe disasm <exe> xref <VA>            그 주소를 call/jmp 하는 곳
  ujyu exe disasm <exe> imm  <값> [크기]      즉치로 그 값을 쓰는 명령
  ujyu exe disasm <exe> info                 섹션·ImageBase
"""
import argparse
import capstone, pefile


class Img:
    def __init__(self, path):
        self.pe = pefile.PE(path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.secs = []
        for s in self.pe.sections:
            va = self.base + s.VirtualAddress
            self.secs.append((va, va + max(s.Misc_VirtualSize, len(s.get_data())),
                              s.get_data(), s.Name.rstrip(b'\0').decode('latin1')))
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True

    def read(self, va, n):
        for lo, hi, data, _ in self.secs:
            if lo <= va < hi:
                o = va - lo
                return data[o:o + n]
        return b''

    def dis(self, va, count=40):
        return list(self.md.disasm(self.read(va, count * 16), va, count))


def _p(i):
    print("%08x  %-24s %s %s" % (i.address, i.bytes.hex(), i.mnemonic, i.op_str))


def _va(s):
    """주소 인자 -> 정수. 항상 16진 해석 (`0x` 접두 유무 무관)."""
    return int(s, 16)


def _imm(s):
    """즉치 인자 -> 정수. 접두로 진법 판단 (`0x…`=16진, 그 외 10진)."""
    return int(s, 0)


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu exe disasm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="x86 exe 디스어셈블 헬퍼 (주소는 런타임 VA = ImageBase + RVA)",
        epilog="예:\n"
               "  ujyu exe disasm game.exe at 403970 20   # 그 주소부터 20개 명령\n"
               "  ujyu exe disasm game.exe xref 4756A0    # 그 주소를 call/jmp 하는 곳\n")
    ap.add_argument("exe", help="디스어셈블할 exe 경로")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<명령>")

    sub.add_parser("info", help="섹션 목록과 ImageBase 출력")

    p = sub.add_parser("at", help="지정 주소부터 N개 명령")
    p.add_argument("va", type=_va, help="시작 주소 (16진 VA)")
    p.add_argument("count", nargs="?", type=int, default=40,
                   help="출력할 명령 개수 (기본: 40)")

    p = sub.add_parser("fn", help="ret/무조건점프까지 = 함수 1개")
    p.add_argument("va", type=_va, help="함수 시작 주소 (16진 VA)")
    p.add_argument("max", nargs="?", type=int, default=400,
                   help="최대 명령 개수 (기본: 400)")

    p = sub.add_parser("xref", help="그 주소를 call/jmp 하는 곳 찾기")
    p.add_argument("va", type=_va, help="참조 대상 주소 (16진 VA)")

    p = sub.add_parser("imm", help="즉치로 그 값을 쓰는 명령 찾기")
    p.add_argument("value", type=_imm, help="찾을 즉치 값 (0x… 16진 / 그 외 10진)")
    p.add_argument("size", nargs="?", type=int, default=None,
                   help="즉치 크기 (현재 미사용, 호환용)")

    a = ap.parse_args()

    cmd = a.cmd
    img = Img(a.exe)
    if cmd == "info":
        print("ImageBase %08x" % img.base)
        for lo, hi, d, n in img.secs:
            print("  %-8s %08x-%08x (%d B)" % (n, lo, hi, len(d)))
    elif cmd == "at":
        for i in img.dis(a.va, a.count):
            _p(i)
    elif cmd == "fn":
        va, mx = a.va, a.max
        for i in img.dis(va, mx):
            _p(i)
            if i.mnemonic == "ret" or (i.mnemonic == "jmp" and i.op_str.startswith("0x")
                                       and int(i.op_str, 16) < va):
                break
    elif cmd == "xref":
        tgt = a.va
        for lo, hi, data, name in img.secs:
            if name not in (".text", "CODE"):
                continue
            for i in img.md.disasm(data, lo):
                if i.mnemonic in ("call", "jmp") and i.op_str.startswith("0x") \
                        and int(i.op_str, 16) == tgt:
                    _p(i)
    elif cmd == "imm":
        val = a.value
        for lo, hi, data, name in img.secs:
            if name not in (".text", "CODE"):
                continue
            for i in img.md.disasm(data, lo):
                if ("0x%x" % val) in i.op_str or (", %d" % val) in i.op_str:
                    _p(i)


if __name__ == "__main__":
    main()
