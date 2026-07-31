import struct
import unittest

from ujyu.formats import adp


def _header(channels=1, sample_rate=44100):
    return b"ADPx" + sample_rate.to_bytes(3, "little") + bytes([channels])


def _signed_samples(pcm):
    return struct.unpack("<%dh" % (len(pcm) // 2), pcm)


class ADPTests(unittest.TestCase):
    def test_header_is_magic_u24_rate_and_u8_channels(self):
        header = adp.parse_header(_header(channels=2, sample_rate=48000))
        self.assertEqual(header.sample_rate, 48000)
        self.assertEqual(header.channels, 2)
        self.assertEqual(header.compressed_block_size, 8)

    def test_zero_stereo_block_decodes_to_six_silent_frames(self):
        header, pcm = adp.decode(_header(channels=2) + b"\0" * 8)
        self.assertEqual(header.channels, 2)
        self.assertEqual(pcm, b"\0" * 24)

    def test_mono_codes_are_accumulated(self):
        packed = sum(1 << (sample * 5) for sample in range(6))
        _header_info, pcm = adp.decode(
            _header(channels=1) + struct.pack("<I", packed)
        )
        self.assertEqual(_signed_samples(pcm), (4, 8, 12, 16, 20, 24))

    def test_code_16_uses_alternating_signed_tables(self):
        packed = sum(16 << (sample * 5) for sample in range(6))
        _header_info, pcm = adp.decode(
            _header(channels=1) + struct.pack("<I", packed)
        )
        self.assertEqual(
            _signed_samples(pcm),
            (-64, 72, -220, 408, -940, 1956),
        )

    def test_stereo_words_are_interleaved(self):
        left = sum(1 << (sample * 5) for sample in range(6))
        right = sum(31 << (sample * 5) for sample in range(6))
        _header_info, pcm = adp.decode(
            _header(channels=2) + struct.pack("<II", left, right)
        )
        self.assertEqual(
            _signed_samples(pcm),
            (4, -4, 8, -8, 12, -12, 16, -16, 20, -20, 24, -24),
        )

    def test_rejects_partial_compressed_block(self):
        with self.assertRaises(adp.ADPError):
            adp.decode(_header(channels=2) + b"\0" * 7)


if __name__ == "__main__":
    unittest.main()
