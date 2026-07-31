#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DMJ0 비디오 디코더 — Studio Miris 오프닝 무비 (opening.dmj).

포맷 (리버싱):
  컨테이너 = DMJ0 헤더 + DMJQ(양자화) + DMJH(허프만) + DMJF×N (프레임)
    DMJ0: +8 W(u16) +10 H(u16) +12 fps(u16) +16 frame_count(u32)
    DMJQ: 2바이트 프리픽스 + 64바이트 양자화표(raster 순, JPEG 표준 휘도표)
    DMJH: [DC BITS 16][AC BITS 16][DC VALS 12][AC VALS 162]  (표준 휘도 DC/AC)
    DMJF: "DMJF"+size(u32)+엔트로피. 프레임 walk 는 off += 8+size.
  각 프레임 = baseline JPEG 계열 엔트로피 (exe 0x45bda0 기준):
    - 3 컴포넌트 = **B, G, R** 순 (YCbCr 아님), MCU당 인터리브. 모두 동일 DC/AC/양자화 공유.
      (_render에서 RGB로 R↔B 스왑. 예전 "R,G,B 직접"은 오판 — 빨강/파랑 뒤바뀜.)
    - **블록마다 1비트 intra/inter 플래그** (스트림에서 소비): 0=intra(블록 리셋),
      1=inter(이전 프레임의 같은 블록 계수에 **누적** = 델타 예측).
    - 블록당 DC 는 절대값(플래그로 리셋/누적), 계수는 de-zigzag 하며 배치.
    - FF 바이트 **비스터핑**, restart 마커 없음 — 순수 raw 비트스트림.
    - 인터프레임 예측 때문에 **순차 디코드**해야 한다(키프레임부터).
  양자화: 실제값 = round(raw * scale/100), scale = DMJQ 첫 u16(예:40 → ×0.4).

사용:
  ujyu dmj info                      # 헤더 정보
  ujyu dmj frames <out_dir> [N]      # 프레임 PNG 순차 추출 (N=개수 제한)
  ujyu dmj video|mjpeg [out] [N]     # 영상 재인코드 / 무손실 MJPEG
"""
import sys, os, struct, argparse
import numpy as np
from scipy.fftpack import idct

ZZ = [0,1,8,16,9,2,3,10,17,24,32,25,18,11,4,5,12,19,26,33,40,48,41,34,27,20,13,6,7,14,21,28,
      35,42,49,56,57,50,43,36,29,22,15,23,30,37,44,51,58,59,52,45,38,31,39,46,53,60,61,54,47,55,62,63]


def _huff(bits, vals):
    codes = {}; code = 0; k = 0
    for L in range(1, 17):
        for _ in range(bits[L-1]):
            codes[(L, code)] = vals[k]; k += 1; code += 1
        code <<= 1
    return codes


class _BR:
    __slots__ = ('d', 'p', 'b', 'n')
    def __init__(self, d): self.d = d; self.p = 0; self.b = 0; self.n = 0
    def bit(self):
        if self.n == 0:
            self.b = self.d[self.p] if self.p < len(self.d) else 0; self.p += 1; self.n = 8
        self.n -= 1
        return (self.b >> self.n) & 1
    def bits(self, c):
        v = 0
        for _ in range(c): v = (v << 1) | self.bit()
        return v


class DMJ:
    def __init__(self, path):
        b = open(path, "rb").read(); self.b = b
        assert b[:4] == b"DMJ0", "DMJ0 아님"
        self.W = struct.unpack('<H', b[8:10])[0]; self.H = struct.unpack('<H', b[10:12])[0]
        self.fps = struct.unpack('<H', b[12:14])[0]
        self.count = struct.unpack('<I', b[16:20])[0]
        dmjq = self._chunk(0x18); dmjh = self._chunk(0x62)
        # DMJQ: 2바이트 프리픽스 = 품질 스케일러. 실제 양자화 = round(raw * scale/100).
        # (exe 0x45b9a0: imul raw,scale; +50; /100)
        scale = dmjq[0] | (dmjq[1] << 8)
        raw = np.array([dmjq[2+i] for i in range(64)], dtype=np.float32)   # raster
        self.quant = np.round(raw * scale / 100.0)
        self.DCH = _huff(dmjh[0:16], dmjh[32:44])
        self.ACH = _huff(dmjh[16:32], dmjh[44:206])
        self.frames = []
        off = 0x138
        while off + 8 <= len(b) and b[off:off+4] == b"DMJF":
            sz = struct.unpack('<I', b[off+4:off+8])[0]
            self.frames.append(b[off+8:off+8+sz]); off += 8 + sz
        self.NBX, self.NBY = self.W // 8, self.H // 8
        self.NB = self.NBX * self.NBY

    def _chunk(self, off):
        sz = struct.unpack('<I', self.b[off+4:off+8])[0]
        return self.b[off+8:off+8+sz]

    def _hd(self, br, codes):
        code = 0
        for L in range(1, 17):
            code = (code << 1) | br.bit()
            v = codes.get((L, code))
            if v is not None: return v
        raise ValueError("bad huffman")

    def reset(self):
        """순차 디코드 상태 초기화. 컴포넌트별 이전-프레임 계수(자연순 64/블록)."""
        self._prev = [np.zeros((self.NB, 64), np.float32) for _ in range(3)]
        self._pos = 0

    def _decode_into_prev(self, idx):
        """프레임 idx 를 엔트로피 디코드해 self._prev 를 갱신(intra 리셋/inter 누적)."""
        br = _BR(self.frames[idx])
        for mcu in range(self.NB):
            for c in range(3):
                flag = br.bit()                         # 1=inter(누적), 0=intra(리셋)
                blk = np.zeros(64, np.float32)
                s = self._hd(br, self.DCH)
                if s:
                    v = br.bits(s)
                    blk[0] = v - ((1 << s) - 1) if v < (1 << (s-1)) else v
                k = 1
                while k < 64:
                    rs = self._hd(br, self.ACH); r = rs >> 4; sz = rs & 0xf
                    if sz == 0:
                        if r == 15: k += 16; continue   # ZRL
                        break                           # EOB
                    k += r
                    v = br.bits(sz)                      # magnitude 는 항상 소비
                    if k < 64:
                        blk[ZZ[k]] = v - ((1 << sz) - 1) if v < (1 << (sz-1)) else v
                    k += 1
                if flag:
                    self._prev[c][mcu] += blk            # inter: 델타 누적
                else:
                    self._prev[c][mcu] = blk             # intra: 리셋

    def _render(self):
        chans = []
        for c in range(3):
            m = (self._prev[c] * self.quant).reshape(self.NB, 8, 8)
            sp = idct(idct(m, axis=1, norm='ortho'), axis=2, norm='ortho') + 128.0
            img = np.zeros((self.H, self.W), np.float32)
            for i in range(self.NB):
                by, bx = divmod(i, self.NBX)
                img[by*8:by*8+8, bx*8:bx*8+8] = sp[i]
            chans.append(img)
        # 컴포넌트 순서 = B,G,R (exe가 그 순서로 저장). RGB로 내보내려 R↔B 스왑.
        return np.clip(np.stack([chans[2], chans[1], chans[0]], -1), 0, 255).astype(np.uint8)

    def frame(self, idx):
        """프레임 idx → (H,W,3) uint8 RGB.

        인터프레임 예측이라 **순차 접근**이어야 정확하다(reset() 후 0,1,2,… 순).
        건너뛰거나 되돌아가면 델타가 어긋난다.
        """
        if not hasattr(self, "_prev") or idx < self._pos:
            self.reset()
        while self._pos <= idx:
            self._decode_into_prev(self._pos); self._pos += 1
        return self._render()

    def iter_frames(self, n=None):
        """0..n-1 프레임을 순차로 yield (RGB). 대량 추출용."""
        self.reset()
        n = self.count if n is None else n
        for i in range(n):
            self._decode_into_prev(i); self._pos = i + 1
            yield i, self._render()


# ───────────────────────── MJPEG 무손실 transliteration ─────────────────────────
# DMJ 프레임은 이미 양자화 DCT 계수(JPEG와 동일 표현)이고 인터프레임 예측이 있다. 픽셀 재인코드
# 대신 각 프레임의 누적 양자화 계수를 baseline RGB JPEG로 재포장 → YCbCr 변환 손실 0.
# 컴포넌트 = R,G,B (DMJ 내부순 B,G,R 스왑; JPEG 컴포넌트 ID 'R'/'G'/'B'로 RGB 표기).
_INV_ZZ = [0] * 64
for _k, _n in enumerate(ZZ): _INV_ZZ[_n] = _k

def _enc_maps(bits, vals):
    m = {}; code = 0; k = 0
    for L in range(1, 17):
        for _ in range(bits[L-1]):
            m[vals[k]] = (L, code); k += 1; code += 1
        code <<= 1
    return m

class _BW:
    """MSB-first 비트 라이터 + JPEG 바이트 스터핑(FF→FF00)."""
    def __init__(self): self.out = bytearray(); self.acc = 0; self.nb = 0
    def put(self, code, length):
        self.acc = (self.acc << length) | (code & ((1 << length) - 1)); self.nb += length
        while self.nb >= 8:
            self.nb -= 8; b = (self.acc >> self.nb) & 0xFF; self.out.append(b)
            if b == 0xFF: self.out.append(0x00)
    def flush(self):
        if self.nb:
            b = (self.acc << (8 - self.nb)) & 0xFF; b |= (1 << (8 - self.nb)) - 1
            self.out.append(b)
            if b == 0xFF: self.out.append(0x00)
            self.nb = 0

def _jsize(v): return 0 if v == 0 else int(abs(v)).bit_length()
def _jbits(v, s): return v if v >= 0 else v + (1 << s) - 1

def mjpeg_tables(dmj):
    """DMJ Huffman 청크(0x62)에서 JPEG DC/AC bits·vals + 인코딩 맵 도출."""
    dmjh = dmj._chunk(0x62)
    dcb, dcv = list(dmjh[0:16]), list(dmjh[32:44])
    acb, acv = list(dmjh[16:32]), list(dmjh[44:206])
    return dcb, dcv, acb, acv, _enc_maps(dcb, dcv), _enc_maps(acb, acv)

def frame_coeffs_rgb(dmj):
    """현재 _prev 상태의 계수를 R,G,B 순으로(내부 B,G,R 스왑)."""
    return [dmj._prev[2], dmj._prev[1], dmj._prev[0]]

def frame_to_jpeg(coeff_rgb, quant_nat, tables, W, H):
    """coeff_rgb[c]=(NB,64) 양자화계수(자연순, c=R,G,B). baseline RGB JPEG 바이트."""
    dcb, dcv, acb, acv, dcmap, acmap = tables
    NB = coeff_rgb[0].shape[0]
    o = bytearray(b"\xFF\xD8")
    dqt = bytes([quant_nat[ZZ[k]] & 0xFF for k in range(64)])
    o += b"\xFF\xDB" + struct.pack(">H", 2 + 1 + 64) + b"\x00" + dqt
    sof = b"\x08" + struct.pack(">HH", H, W) + b"\x03"
    for cid in (0x52, 0x47, 0x42): sof += bytes([cid, 0x11, 0x00])   # 'R','G','B' (색변환 없음)
    o += b"\xFF\xC0" + struct.pack(">H", 2 + len(sof)) + sof
    def dht(tc_th, bits, vals):
        return b"\xFF\xC4" + struct.pack(">H", 2+1+16+len(vals)) + bytes([tc_th]) + bytes(bits) + bytes(vals)
    o += dht(0x00, dcb, dcv) + dht(0x10, acb, acv)
    sos = b"\x03" + b"\x52\x00\x47\x00\x42\x00" + b"\x00\x3F\x00"
    o += b"\xFF\xDA" + struct.pack(">H", 2 + len(sos)) + sos
    bw = _BW(); prev_dc = [0, 0, 0]
    for blk in range(NB):
        for c in range(3):
            nat = coeff_rgb[c][blk]
            dc = int(nat[0]); diff = dc - prev_dc[c]; prev_dc[c] = dc
            s = _jsize(diff); L, code = dcmap[s]; bw.put(code, L)
            if s: bw.put(_jbits(diff, s), s)
            run = 0
            for k in range(1, 64):
                v = int(nat[ZZ[k]])
                if v == 0: run += 1; continue
                while run > 15:
                    L, code = acmap[0xF0]; bw.put(code, L); run -= 16
                s = _jsize(v); L, code = acmap[(run << 4) | s]; bw.put(code, L); bw.put(_jbits(v, s), s)
                run = 0
            if run:
                L, code = acmap[0x00]; bw.put(code, L)
    bw.flush()
    o += bw.out + b"\xFF\xD9"
    return bytes(o)

def to_mjpeg(dmj, out, n=None, ffmpeg=None):
    """DMJ → 무손실 MJPEG (ffmpeg image2pipe copy). ffmpeg=경로 또는 None(환경변수 FFMPEG/PATH)."""
    import subprocess
    ff = ffmpeg or os.environ.get("FFMPEG", "ffmpeg")
    tables = mjpeg_tables(dmj); n = n or dmj.count
    p = subprocess.Popen([ff, "-y", "-f", "image2pipe", "-r", str(dmj.fps),
                          "-c:v", "mjpeg", "-i", "pipe:0", "-c:v", "copy", out],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dmj.reset()
    for i in range(n):
        dmj._decode_into_prev(i); dmj._pos = i + 1
        jpg = frame_to_jpeg(frame_coeffs_rgb(dmj), dmj.quant.astype(int), tables, dmj.W, dmj.H)
        p.stdin.write(jpg)
        if i % 300 == 0: print("  %d/%d" % (i, n), flush=True)
    p.stdin.close(); p.wait()
    print("무손실 MJPEG -> %s" % out)


def export(dmj, out, n=None, audio=None, ffmpeg=None):
    """DMJ → **무손실** 영상 파일(FFV1/mkv). 게임이 실제로 디코드하는 픽셀 그대로다.

    인코딩 결과(특히 `--max-frame` 으로 고주파를 잘라낸 프레임)의 화질을 눈으로
    확인할 때 쓴다. 재인코딩 손실이 섞이면 판단이 흐려지므로 무손실로 낸다.
    audio 에 원본 영상을 주면 그 오디오 트랙을 그대로 붙인다.
    """
    import subprocess
    ff = ffmpeg or os.environ.get("FFMPEG", "ffmpeg")
    n = n or dmj.count
    cmd = [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", "%dx%d" % (dmj.W, dmj.H), "-r", str(dmj.fps), "-i", "pipe:0"]
    if audio:
        cmd += ["-i", audio, "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "copy", "-shortest"]
    # FFV1 level3 + gbrp = RGB 무손실(색공간 변환 없음). 손실 코덱을 쓰면 안 된다.
    cmd += ["-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1",
            "-g", "1", "-pix_fmt", "gbrp", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i, rgb in dmj.iter_frames(n):
        p.stdin.write(rgb.tobytes())
        if i % 300 == 0:
            print("  %d/%d" % (i, n), flush=True)
    p.stdin.close(); p.wait()
    print("영상 -> %s (%dx%d %d프레임)" % (out, dmj.W, dmj.H, n))


def main():
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_movie", "opening.dmj")
    ap = argparse.ArgumentParser(
        prog="ujyu dmj", description="DMJ0 무비 정보 조회/프레임 추출/영상 변환",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="예:\n"
               "  ujyu dmj info\n"
               "  ujyu dmj --file _movie/other.dmj frames _png 100\n"
               "\n"
               "인코딩은 하위 명령으로:\n"
               "  ujyu dmj encode         영상 -> DMJ0 (ujyu dmj encode --help)")
    ap.add_argument("--file", dest="file", default=default_path,
                    help="대상 .dmj 경로 (기본: 모듈 옆 _movie/opening.dmj)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("info", help="헤더 정보(해상도·fps·프레임 수) 출력")

    p = sub.add_parser("frames", help="프레임을 PNG 로 순차 추출")
    p.add_argument("out", help="PNG 를 쓸 폴더 (없으면 만든다)")
    p.add_argument("n", nargs="?", type=int, default=None,
                   help="추출할 프레임 수 (기본: 파싱된 프레임 전부)")

    p = sub.add_parser("video", help="mp4v 로 재인코드 (cv2)")
    p.add_argument("out", nargs="?", default=None,
                   help="출력 mp4 경로 (기본: 대상과 같은 폴더의 opening_rbfix.mp4)")
    p.add_argument("n", nargs="?", type=int, default=None,
                   help="인코드할 프레임 수 (기본: 헤더의 전체 프레임 수)")

    p = sub.add_parser("export", help="FFV1/mkv 무손실 내보내기 (ffmpeg)")
    p.add_argument("out", nargs="?", default="out.mkv", help="출력 mkv 경로 (기본: out.mkv)")
    p.add_argument("audio", nargs="?", default=None,
                   help="오디오 트랙을 가져올 원본 영상 (기본: 없음)")

    p = sub.add_parser("mjpeg", help="양자화 계수 그대로 무손실 MJPEG 재포장 (ffmpeg)")
    p.add_argument("out", nargs="?", default=None,
                   help="출력 mkv 경로 (기본: 대상과 같은 폴더의 opening_lossless.mkv)")
    p.add_argument("n", nargs="?", type=int, default=None,
                   help="인코드할 프레임 수 (기본: 헤더의 전체 프레임 수)")

    a = ap.parse_args()
    path = a.file
    cmd = a.cmd or "info"                            # 서브커맨드 생략 시 info
    d = DMJ(path)
    if cmd == "info":
        print("W=%d H=%d fps=%d frames=%d (파싱 %d)" % (d.W, d.H, d.fps, d.count, len(d.frames)))
    elif cmd == "frames":
        from PIL import Image
        out = a.out; os.makedirs(out, exist_ok=True)
        n = a.n if a.n is not None else len(d.frames)
        for i, rgb in d.iter_frames(n):
            Image.fromarray(rgb).save(os.path.join(out, "f%05d.png" % i))
            if i % 200 == 0: print("  %d/%d" % (i, n))
        print("추출 %d프레임 -> %s" % (n, out))
    elif cmd == "video":
        import cv2
        out = a.out or os.path.join(os.path.dirname(path), "opening_rbfix.mp4")
        n = a.n if a.n is not None else d.count
        vw = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), float(d.fps), (d.W, d.H))
        for i, rgb in d.iter_frames(n):
            vw.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))   # RGB(스왑됨) -> cv2 BGR
            if i % 300 == 0: print("  %d/%d" % (i, n), flush=True)
        vw.release()
        print("영상 %d프레임 -> %s" % (n, out))
    elif cmd == "export":
        export(d, a.out, audio=a.audio)
    elif cmd == "mjpeg":
        out = a.out or os.path.join(os.path.dirname(path), "opening_lossless.mkv")
        n = a.n if a.n is not None else d.count
        to_mjpeg(d, out, n)


if __name__ == "__main__":
    main()
