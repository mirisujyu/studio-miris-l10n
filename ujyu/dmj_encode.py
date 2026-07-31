# -*- coding: utf-8 -*-
"""영상 -> DMJ0 인코더 (docs/formats/MOVIE.md).

원본 .dmj 의 DMJQ/DMJH 청크를 그대로 재사용해 게임 디코더 호환을 보장하고,
DMJ0 헤더(W,H,프레임수,최대프레임크기)와 DMJF 프레임만 새로 만든다. **전 블록
intra** (inter 델타는 쓰지 않는다 — 화질·정확성 우선, 대신 용량이 커진다).

  ujyu dmj encode <입력영상> <출력.dmj> --ref <원본.dmj> --size 640x480
                             [--qscale 40] [--max-frame N] [--frames N]

--size 로 지정한 해상도로 **먼저 lanczos 다운샘플**한 뒤 인코딩한다(ffmpeg).
--qscale 은 DMJQ 양자화 스케일(원본 40; 키우면 용량↓ 화질↓).

**중요 — 헤더 `+0x14` = 최대 프레임 페이로드 크기.** 엔진은 이 값으로 프레임
버퍼를 잡는다(디코더 ctor `0x45b710`: `alloc(w*8 + (3*픽셀 + 0x2052c)*4 + [+0x14])`).
값이 실제보다 작으면 그 프레임부터 화면이 깨진다. 이 인코더는 끝에서 **실측
최대값을 기록**하므로 해상도와 무관하게 안전하다. `--max-frame` 은 그래도 상한을
두고 싶을 때만 쓴다(넘는 프레임의 고주파 계수를 잘라 맞춘다).

주의: 폭·높이는 8의 배수여야 한다(MCU 그리드). 엔트로피는 바이트 스터핑·restart
마커가 없는 raw 비트스트림이다(MOVIE.md §1.2).
치수는 **렌더러의 2배 확대 여부**에 맞춘다 — 기본은 화면 ÷ 2, exe 에서 2배 확대를
끄면(RESOLUTION.md §6-1) 화면과 같은 치수로 네이티브 재생한다.
"""
import argparse, os, struct, subprocess, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass
from ujyu.formats.dmj import ZZ, _enc_maps, DMJ

ZZA = np.array(ZZ)

# --max-frame 상한 맞추기용 계수 유지 개수 사다리(내림차순, [0]=자르지 않음).
KEEP = (64, 48, 36, 28, 20, 14, 10, 7, 5, 3, 2, 1)


def _maps(m):
    n = max(m) + 1
    c = np.zeros(n, np.uint32); l = np.zeros(n, np.uint8)
    for k, (ln, code) in m.items():          # _enc_maps 는 (길이, 코드) 순
        c[k] = code; l[k] = ln
    return c, l


def _pack(codes, lens):
    tot = int(lens.sum())
    if tot == 0:
        return b''
    ends = np.cumsum(lens.astype(np.int64)); starts = ends - lens
    idx = np.arange(tot, dtype=np.int64) - np.repeat(starts, lens)
    shift = np.repeat(lens.astype(np.int64), lens) - 1 - idx
    bits = (np.repeat(codes.astype(np.uint64), lens) >> shift.astype(np.uint64)) & 1
    if tot % 8:
        bits = np.concatenate([bits, np.zeros(8 - tot % 8, bits.dtype)])
    return np.packbits(bits.astype(np.uint8)).tobytes()


def encode_frame(coef, dcc, dcl, acc, acl, NB):
    """coef (3,NB,64) 자연순 -> DMJF 페이로드. 블록 순서 = MCU 바깥/컴포넌트 안쪽."""
    Z = np.transpose(coef, (1, 0, 2)).reshape(NB * 3, 64)[:, ZZA]
    B = Z.shape[0]
    dc = Z[:, 0].astype(np.int64)
    s = np.where(dc == 0, 0, np.floor(np.log2(np.abs(dc) + (dc == 0))).astype(np.int64) + 1)
    codes = [np.zeros(B, np.uint32)]; lens = [np.ones(B, np.uint8)]      # intra 플래그
    order = [np.arange(B) * 1000.0]
    codes.append(dcc[s]); lens.append(dcl[s]); order.append(np.arange(B) * 1000.0 + 1)
    m = s > 0
    dcmag = np.where(dc >= 0, dc, dc + (1 << s) - 1)
    codes.append(dcmag[m].astype(np.uint32)); lens.append(s[m].astype(np.uint8))
    order.append(np.arange(B)[m] * 1000.0 + 2)

    ac = Z[:, 1:]
    bi, pi = np.nonzero(ac)
    if len(bi):
        k = pi + 1
        newblk = np.empty(len(bi), bool); newblk[0] = True
        newblk[1:] = bi[1:] != bi[:-1]
        prevk = np.where(newblk, 0, np.roll(k, 1))
        run = k - prevk - 1
        nzrl = run // 16; rem = run % 16
        val = ac[bi, pi].astype(np.int64)
        sz = np.floor(np.log2(np.abs(val))).astype(np.int64) + 1
        if nzrl.sum():
            zi = np.repeat(np.arange(len(bi)), nzrl)
            zseq = np.arange(len(zi)) - np.repeat(np.cumsum(nzrl) - nzrl, nzrl)
            codes.append(np.full(len(zi), acc[0xF0], np.uint32))
            lens.append(np.full(len(zi), acl[0xF0], np.uint8))
            order.append(bi[zi] * 1000.0 + 3 + (pi[zi] + zseq * 1e-3) * 1e-3)
        sym = (rem << 4) | sz
        codes.append(acc[sym]); lens.append(acl[sym])
        order.append(bi * 1000.0 + 3 + pi * 1e-3 + 5e-7)
        mag = np.where(val >= 0, val, val + (1 << sz) - 1)
        codes.append(mag.astype(np.uint32)); lens.append(sz.astype(np.uint8))
        order.append(bi * 1000.0 + 3 + pi * 1e-3 + 6e-7)
        lastk = np.zeros(B, np.int64); np.maximum.at(lastk, bi, k)
        has = np.zeros(B, bool); has[bi] = True
    else:
        lastk = np.zeros(B, np.int64); has = np.zeros(B, bool)
    ei = np.nonzero((~has) | (lastk < 63))[0]
    codes.append(np.full(len(ei), acc[0x00], np.uint32))
    lens.append(np.full(len(ei), acl[0x00], np.uint8))
    order.append(ei * 1000.0 + 999.0)

    C = np.concatenate(codes); L = np.concatenate(lens); O = np.concatenate(order)
    p = np.argsort(O, kind='stable')
    return _pack(C[p], L[p])


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu dmj encode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="영상 -> DMJ0 인코딩",
        epilog="예:\n"
               "  ujyu dmj encode in.mp4 out.dmj --ref 원본/opening.dmj --size 1280x960\n")
    ap.add_argument("src", help="입력 영상 (ffmpeg 가 읽는 형식)")
    ap.add_argument("out", help="출력 .dmj 경로")
    ap.add_argument("--ref", required=True, help="테이블을 가져올 원본 .dmj")
    ap.add_argument("--size", required=True, help="WxH (8의 배수)")
    ap.add_argument("--qscale", type=int, default=None, help="양자화 스케일 (기본: 원본 값)")
    ap.add_argument("--max-frame", type=int, default=0,
                    help="프레임 페이로드 상한(바이트). 넘는 프레임만 고주파 계수를 "
                         "잘라 맞춘다 — 엔진의 프레임 버퍼 한계 대응")
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--ffmpeg", default=os.environ.get("FFMPEG", "ffmpeg"))
    a = ap.parse_args()
    W, H = (int(x) for x in a.size.lower().split("x"))
    assert W % 8 == 0 and H % 8 == 0, "폭·높이는 8의 배수여야 한다"

    from scipy.fft import dctn
    ref = DMJ(a.ref); b = ref.b
    dmjq = bytearray(b[0x18:0x18 + 8 + struct.unpack('<I', b[0x1c:0x20])[0]])
    dmjh = b[0x62:0x62 + 8 + struct.unpack('<I', b[0x66:0x6a])[0]]
    qs = a.qscale if a.qscale is not None else (dmjq[8] | (dmjq[9] << 8))
    dmjq[8] = qs & 0xFF; dmjq[9] = (qs >> 8) & 0xFF
    qraw = np.array([dmjq[10 + i] for i in range(64)], np.float32)
    quant = np.round(qraw * qs / 100.0); quant[quant < 1] = 1
    qq = quant.reshape(8, 8)
    dcc, dcl = _maps(_enc_maps(dmjh[8:24], dmjh[40:52]))
    acc, acl = _maps(_enc_maps(dmjh[24:40], dmjh[52:214]))

    NBX, NBY = W // 8, H // 8; NB = NBX * NBY
    cmd = [a.ffmpeg, '-v', 'error', '-i', a.src,
           '-vf', 'scale=%d:%d:flags=lanczos' % (W, H)]
    if a.frames: cmd += ['-frames:v', str(a.frames)]
    cmd += ['-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1 << 26)

    hdr = bytearray(b[0:0x18])                       # 원본 DMJ0 청크(24B) 재사용
    hdr[8:10] = struct.pack('<H', W); hdr[10:12] = struct.pack('<H', H)
    f = open(a.out, 'wb')
    f.write(bytes(hdr) + bytes(dmjq) + dmjh)         # 프레임은 0x138 부터
    fsz = W * H * 3; n = 0; t0 = time.time(); tot = 0; n_cap = 0; hint = 1; mx = 0
    while True:
        raw = p.stdout.read(fsz)
        if len(raw) < fsz:
            break
        im = np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.float32) - 128.0
        coef = np.empty((3, NB, 64), np.int16)
        for ci, sc in enumerate((2, 1, 0)):          # 내부 컴포넌트 순 = B,G,R
            bl = np.ascontiguousarray(im[:, :, sc]).reshape(
                NBY, 8, NBX, 8).swapaxes(1, 2).reshape(NB, 8, 8)
            coef[ci] = np.round(dctn(bl, axes=(1, 2), norm='ortho') / qq
                                ).astype(np.int16).reshape(NB, 64)
        pay = encode_frame(coef, dcc, dcl, acc, acl, NB)
        if a.max_frame and len(pay) > a.max_frame:
            # 고주파(지그재그 뒤쪽) 계수를 점점 더 잘라 상한에 맞춘다.
            # 화질은 그 프레임만 부드러워지고 구조는 유지된다(DC·저주파 보존).
            # keep 이 작을수록 페이로드가 단조 감소하므로 **이진 탐색**으로 상한을
            # 만족하는 가장 큰 keep 을 찾는다. 시작점은 직전 프레임에서 통한 수준
            # (hint) — 연속 프레임은 복잡도가 비슷해 보통 1~2회로 끝난다.
            lo, hi = 1, len(KEEP) - 1
            i = min(max(hint, lo), hi)
            best = None
            while lo <= hi:
                c2 = coef.copy()
                c2[:, :, ZZA[KEEP[i]:]] = 0
                p2 = encode_frame(c2, dcc, dcl, acc, acl, NB)
                if len(p2) <= a.max_frame:
                    best = (i, p2); hi = i - 1
                else:
                    lo = i + 1
                    if best is None:
                        pay = p2          # 전부 실패해도 가장 작게 자른 것을 쓴다
                i = (lo + hi) // 2
            if best is not None:
                hint, pay = best[0], best[1]
            else:
                hint = len(KEEP) - 1
            n_cap += 1
        else:
            hint = max(1, hint - 1)       # 여유가 생기면 다음 프레임은 덜 자르는 쪽부터
        f.write(b'DMJF' + struct.pack('<I', len(pay)) + pay)
        n += 1; tot += len(pay); mx = max(mx, len(pay))
        if n % 100 == 0:
            el = time.time() - t0
            print('  %d프레임 %.0fs (%.2f s/f, %.0f MB)' % (n, el, el / n, tot / 1e6), flush=True)
    p.stdout.close(); p.wait()
    f.seek(16); f.write(struct.pack('<I', n))        # 프레임 수
    f.seek(0x14); f.write(struct.pack('<I', mx))     # 최대 프레임 크기 = 엔진 버퍼 크기
    f.close()
    print('완료: %s  %dx%d %d프레임 %.0f MB (%.0f분)'
          % (a.out, W, H, n, os.path.getsize(a.out) / 1e6, (time.time() - t0) / 60))
    print('  최대 프레임 %d B (헤더 +0x14 에 기록), 평균 %d B' % (mx, tot // max(n, 1)))
    if a.max_frame:
        print('  상한 %dB 초과로 계수 축소한 프레임: %d개 (%.1f%%)'
              % (a.max_frame, n_cap, 100.0 * n_cap / max(n, 1)))


if __name__ == "__main__":
    main()
