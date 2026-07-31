import tempfile
import unittest
from collections import Counter
from pathlib import Path

from ujyu.formats import vneg
from ujyu import scn_dims


def _definition(op, slots=1, args=()):
    out = bytearray([op])
    out += slots.to_bytes(2, "big")
    out.append(len(args))
    for kind, value in args:
        if kind == "i":
            out.append(1)
            out += int(value).to_bytes(4, "big", signed=True)
        else:
            out.append(0)
            out += str(value).encode("cp932") + b"\x00"
    return bytes(out)


def _scene(definitions, flow=b"\x00" * 8, labels=1):
    defs = b"".join(definitions)
    flow_start = 8 + len(defs)
    table = bytearray(labels.to_bytes(2, "big") + b"\x00" * 6)
    for index in range(labels):
        offset = 0x3B if index == 0 else 8 + labels * 4
        table += offset.to_bytes(2, "big") + b"\x00\x00"
    scene = (b"VNEG" + b"\x00\x00" + len(definitions).to_bytes(2, "big")
             + defs + bytes(table) + flow)
    return scene, flow_start


class SymbolParsingTests(unittest.TestCase):
    def test_registered_scroll_type_and_identity_relocation(self):
        scroll = _definition(0x16, args=(
            ("i", 0), ("i", 0), ("i", 640), ("i", 480), ("i", 0), ("i", 9000)))
        scene, flow_start = _scene([scroll], labels=2)

        syms, parsed_start = vneg.parse_syms(scene)
        self.assertEqual(parsed_start, flow_start)
        self.assertEqual([(row[2], row[3]) for row in syms], [(0x16, 9000)])
        self.assertEqual(vneg.jt_flowstart(scene), flow_start)

        relocated, fixed, failed = vneg.relocate_jumptable(scene, scene, edits=[])
        self.assertEqual(relocated, scene)
        self.assertEqual((fixed, failed), (1, 0))

    def test_relocation_is_idempotent_and_always_uses_jp_offsets(self):
        jp, flow_start = _scene([_definition(0x16)], flow=b"ABCDEFGH", labels=2)
        code_start = vneg.jumptable_end(jp, flow_start)
        kr = jp[:code_start] + b"xyz" + jp[code_start:]
        edits = [(code_start, 0, 3)]

        first, fixed1, failed1 = vneg.relocate_jumptable(kr, jp, edits=edits)
        second, fixed2, failed2 = vneg.relocate_jumptable(first, jp, edits=edits)

        self.assertEqual((fixed1, failed1), (1, 0))
        self.assertEqual((fixed2, failed2), (1, 0))
        self.assertEqual(second, first)
        entry = flow_start + 8 + 4
        self.assertEqual(int.from_bytes(first[entry:entry + 2], "big"),
                         code_start - flow_start + 3)

    def test_invalid_symbol_type_fails_instead_of_returning_partial_table(self):
        scene, _ = _scene([_definition(0x17)])
        args = {99: [("i", 1, 1)]}
        self.assertEqual(vneg.parse_syms(scene, args_out=args), ([], 0))
        self.assertEqual(args, {})
        self.assertIsNone(vneg.jt_flowstart(scene))

    def test_symbol_reference_supports_indices_above_255(self):
        self.assertEqual(vneg.symref_index(0x1000, 258), 0)
        self.assertEqual(vneg.symref_index(0x1100, 258), 256)
        self.assertEqual(vneg.symref_index(0x1101, 258), 257)
        self.assertIsNone(vneg.symref_index(0x1102, 258))
        self.assertIsNone(vneg.symref_index(0x2000, 258))


class DisassemblyTests(unittest.TestCase):
    def _large_symbol_scene(self):
        definitions = [
            _definition(0x02, slots=256),
            _definition(0x03, args=(("s", "RU01597"),)),
            _definition(0x03, args=(("s", "settitle"),)),
        ]
        flow = b"\x0b\x00\x76\x11\x00\x00\x13\x11\x01"
        return _scene(definitions, flow=flow)

    def test_disasm_skips_jump_table_and_resolves_11xx_references(self):
        scene, flow_start = self._large_symbol_scene()
        code_start = vneg.jumptable_end(scene, flow_start)
        text = vneg.disasm(scene)

        self.assertIn("defs=3  runtime_symbols=258", text)
        self.assertIn("VOICE     RU01597(#256)", text)
        self.assertIn("CALL  settitle()", text)
        flow = text.split("=== FLOW ===\n", 1)[1]
        self.assertNotIn("%06x  .   00 01 00 00" % flow_start, flow)
        self.assertIn("%06x" % code_start, flow)

    def test_scn_dims_accepts_11xx_object_and_argument_references(self):
        definitions = [_definition(0x02, slots=256), _definition(0x0C)]
        definitions += [_definition(0x02, args=(("i", value),))
                        for value in (10, 20, 30, 40)]
        refs = b"".join((vneg.SYMREF + i).to_bytes(2, "big")
                        for i in range(257, 261))
        flow = b"\x01\x11\x00\x02\x04" + refs
        scene, _ = _scene(definitions, flow=flow)

        found = scn_dims.calls(scene)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1:3], (256, 2))
        self.assertEqual([idx for idx, _off in found[0][3]], [257, 258, 259, 260])

    def test_walk_uses_exe_verified_operand_layouts(self):
        flow = (
            b"\x03\x10\x00\x7f"
            b"\x1c\x02\x10\x00\x11\x00"
            b"\x22\xaa"
            b"\x23\xbb"
            b"\x26\x01\x02\x03\x04\x11\x01"
            b"\x27\xcc\x11\x01"
            b"\x28\x05\x06\x07\x08\x11\x01"
            b"\x29\xdd\x11\x01"
            b"\x00"
        )
        scene, _ = _scene([], flow=flow)
        rows = vneg.walk(scene)

        self.assertEqual([row[2] for row in rows], [4, 6, 2, 2, 7, 4, 7, 4, 1])
        self.assertEqual([value for value, _off in rows[0][3]], [0x1000, 0x7F])
        self.assertEqual([value for value, _off in rows[1][3]], [0x1000, 0x1100])
        self.assertEqual([value for value, _off in rows[4][3]], [0x01020304, 0x1101])
        self.assertEqual([value for value, _off in rows[5][3]], [0xCC, 0x1101])

    def test_signed_branch_mnemonics_match_exe_handlers(self):
        flow = b"\x18\x00\x01\x19\x00\x01\x1a\x00\x01"
        scene, _ = _scene([], flow=flow, labels=2)
        text = vneg.disasm(scene)

        self.assertIn("IF>0→     L1", text)
        self.assertIn("IF≥0→     L1", text)
        self.assertIn("IF<0→     L1", text)
        self.assertNotIn("WAIT→", text)

    def test_extract_tracks_11xx_mode_and_speaker_references(self):
        definitions = [_definition(0x02) for _ in range(256)]
        definitions += [
            _definition(0x03, args=(("s", "話者"),)),
            _definition(0x02, args=(("i", 1),)),
        ]
        flow = b"\x0d\x33\x01\x11\x00\x11\x01" + "あ".encode("cp932") + b"%"
        scene, _ = _scene(definitions, flow=flow)

        records = vneg.extract(scene)
        dialogue = [row for row in records if row["kind"] == "dlg"]

        self.assertEqual(len(dialogue), 1)
        self.assertEqual(dialogue[0]["jp"], "あ%")
        self.assertEqual(dialogue[0]["speaker"], "話者")

    def test_extract_keeps_multislot_data_screen_cstr_fallback(self):
        definitions = [
            _definition(0x02, slots=256),
            _definition(0x02, args=(("i", 0),)),
        ]
        flow = b"\x11\x00" + "曲名".encode("cp932") + b"\x00"
        scene, _ = _scene(definitions, flow=flow)

        records = vneg.extract(scene)

        self.assertFalse(any(row["kind"] in ("narr", "dlg") for row in records))
        self.assertTrue(any(row["kind"] == "cstr" and row["jp"] == "曲名"
                            for row in records))

    def test_archive_output_directories_include_extension(self):
        first, _ = self._large_symbol_scene()
        second, _ = _scene([_definition(0x16)])

        class FakeArchive:
            @staticmethod
            def load(path):
                return Path(path).read_bytes(), [("same.scn", 0, 1)], None

            @staticmethod
            def getfile(data, _table, _off, _size):
                return data

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            axr = root / "scenario.axr"
            ax2 = root / "scenario.ax2"
            axr.write_bytes(first)
            ax2.write_bytes(second)

            vneg.run_one(FakeArchive, str(axr), str(root / "out"), Counter())
            vneg.run_one(FakeArchive, str(ax2), str(root / "out"), Counter())

            base = root / "out"
            self.assertTrue((base / "scenario_axr" / "same.txt").is_file())
            self.assertTrue((base / "scenario_ax2" / "same.txt").is_file())
            self.assertNotEqual(
                (base / "scenario_axr" / "same.txt").read_text(encoding="utf-8"),
                (base / "scenario_ax2" / "same.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
