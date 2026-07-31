import struct
import unittest

from ujyu.formats import vneg
from ujyu import scn_dims


def _definition(op, slots, args):
    out = bytearray([op])
    out += slots.to_bytes(2, "big")
    out.append(len(args))
    for typ, value in args:
        if typ == "i":
            out.append(1)
            out += int(value).to_bytes(4, "big", signed=True)
        else:
            out.append(0)
            out += value.encode("ascii") + b"\x00"
    return bytes(out)


def _scene():
    # runtime slots:
    #   #0 button
    #   #1..#6 int[6] = x,y,w,h,id,mode
    #   #7..#9 string[3] = off,on,select
    defs = [
        _definition(0x0C, 1, [("i", 0)] * 5),
        _definition(0x02, 6, [("i", v) for v in (10, 20, 30, 40, 7, 2)]),
        _definition(0x03, 3, [("s", v) for v in ("off.png", "on.png", "select.png")]),
    ]
    args = (1, 2, 3, 4, 1, 7, 8, 6, 9)  # x와 id가 #1을 공유
    call = b"\x01\x10\x00\x02\x09" + b"".join(
        (vneg.SYMREF | idx).to_bytes(2, "big") for idx in args
    )
    # config.scn에서 첫 m02 경계를 놓치게 했던 scratch-object 미확정 구간을 재현한다.
    prefix = b"\x39\x00\x00\x10\x0d\x1a\x00\x02"
    return b"VNEG\x00\x00\x00\x03" + b"".join(defs) + prefix + call


class MultiSlotSymbolTests(unittest.TestCase):
    def test_parse_syms_expands_runtime_slots(self):
        scene = _scene()
        args = {}
        syms, flow_start = vneg.parse_syms(scene, args_out=args)

        self.assertEqual(int.from_bytes(scene[6:8], "big"), 3)  # 정의 수
        self.assertEqual([idx for idx, *_ in syms], list(range(10)))
        self.assertEqual([v for _i, _o, _op, v in syms[1:7]],
                         [10, 20, 30, 40, 7, 2])
        self.assertEqual([v for _i, _o, _op, v in syms[7:]],
                         ["off.png", "on.png", "select.png"])
        self.assertEqual(len(args[0]), 5)
        self.assertEqual(len(args[1]), 1)
        self.assertEqual(vneg.jt_flowstart(scene), flow_start)

    def test_apply_repoints_full_symbol_word_and_preserves_header_semantics(self):
        scene = _scene()
        scaled, entries, repoints, skipped = scn_dims.apply(scene, 2)

        self.assertEqual((entries, repoints, skipped), (3, 1, []))
        self.assertEqual(int.from_bytes(scaled[6:8], "big"), 4)  # 정의 3 + 새 정의 1
        syms, flow_start = vneg.parse_syms(scaled)
        self.assertEqual(len(syms), 11)                           # 슬롯 10 + 새 슬롯 1
        self.assertEqual(syms[10][3], 20)

        calls = [c for c in scn_dims.calls(scaled, flow_start)
                 if c[1] == 0 and c[2] == 0x02]
        self.assertEqual(len(calls), 1)
        refs = [idx for idx, _off in calls[0][3]]
        self.assertEqual(refs, [10, 2, 3, 4, 1, 7, 8, 6, 9])
        vals = {idx: val for idx, _off, _op, val in syms}
        self.assertEqual([vals[idx] for idx in refs[:4]], [20, 40, 60, 80])
        self.assertEqual(vals[refs[4]], 10)                       # id는 원래 값 유지

        x_ref_off = calls[0][3][0][1]
        self.assertEqual(struct.unpack_from(">H", scaled, x_ref_off)[0],
                         vneg.SYMREF | 10)


if __name__ == "__main__":
    unittest.main()
