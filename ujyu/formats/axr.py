#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Studio Miris 'AXRe' archive tool
- 아카이브 포맷을 게임 실행파일의 로더에서 리버싱해 재구현 (SKILL.md 1절).
- 지원: 언팩(unpack), 리팩(repack).
- 시나리오(.scn) 텍스트 추출은 miris.vneg.extract (CLI `ujyu scn extract`) 를 쓴다.

포맷 요약
  [16B 헤더] "AXRe"(4) + enc_size(4) + blk2(4) + checksum(4)
    magic = LE32("AXRe") = 0x65525841
    K2    = ks(ks(magic ^ blk2))
    index_size = LE32(enc_size) ^ K2
  [인덱스] index_size 바이트, CFB형 스트림 암호(key=index_size)로 복호화
    엔트리 = off(4, 절대) + size(4) + name('\0') + 4바이트 정렬 패딩
  [파일 데이터] 각 파일 = raw XOR keystream_table[i % 1024]
    keystream_table = CFB 암호를 0버퍼에 seed=LE32(enc_size)로 돌려 생성한 1024B
  스트림 암호 1스텝(ks): S1 = S ^ ((S&0xFFF)<<17);  K = ~(((S1>>15)|(S1<<18)) ^ S1)
    바이트열 XOR 후 다음 상태 S = K + LE32(평문dword)   (평문 피드백)

사용법
  ujyu axr list   <archive.axr> [--sort name|size|offset] [--json]
  ujyu axr unpack <archive.axr> <out_dir>
  ujyu axr repack <in_dir> <archive.axr> [--blk2-from <orig.axr>]
"""
import sys, os, io, json, struct, glob, argparse
try:                       # 대용량 아카이브(수백 MB)의 XOR 을 벡터화 — 없으면 순수 파이썬
    import numpy as _np
except ImportError:
    _np = None


def _xor_key(data, table, start=0):
    """data ^ table[(start+i) % 1024]. numpy 가 있으면 벡터 연산."""
    n = len(data)
    if _np is None or n < 4096:
        return bytes(data[i] ^ table[(start + i) % 1024] for i in range(n))
    a = _np.frombuffer(bytes(data), _np.uint8)
    t = _np.frombuffer(bytes(bytearray(table)), _np.uint8)
    if start:
        t = _np.roll(t, -(start % 1024))
    key = _np.resize(t, n)          # 1024 주기로 반복
    return (a ^ key).tobytes()

MASK = 0xFFFFFFFF

def ks(S):
    S1 = (S ^ ((S & 0xFFF) << 17)) & MASK
    return (~(((S1 >> 15) | ((S1 << 18) & MASK)) ^ S1)) & MASK

def _crypt(buf, key, ndw, decrypt):
    """CFB형 스트림 암/복호화. decrypt=True면 복호화, False면 암호화."""
    S = key & MASK
    out = bytearray(buf)
    if len(out) < ndw * 4:
        out += b'\x00' * (ndw * 4 - len(out))
    for i in range(ndw):
        S1 = (S ^ ((S & 0xFFF) << 17)) & MASK
        K = (~(((S1 >> 15) | ((S1 << 18) & MASK)) ^ S1)) & MASK
        o = i * 4
        if decrypt:
            p = [out[o + j] ^ ((K >> (8 * j)) & 0xff) for j in range(4)]
            out[o:o + 4] = bytes(p)
        else:
            p = [out[o + j] for j in range(4)]
            out[o:o + 4] = bytes(p[j] ^ ((K >> (8 * j)) & 0xff) for j in range(4))
        S = (K + (p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24))) & MASK
    return out

def _table(seed):
    return bytes(_crypt(b'\x00' * 1024, seed, 256, True)) * 2

def load(path):
    """아카이브 열기 -> (data, entries[(name,off,size)], table)."""
    data = open(path, 'rb').read()
    h = data[:16]
    assert h[:4] == b'AXRe', "AXRe 매직 아님: %r" % h[:4]
    magic = int.from_bytes(h[0:4], 'little')
    blk2 = int.from_bytes(h[8:12], 'little')
    K2 = ks(ks((magic ^ blk2) & MASK))
    size = int.from_bytes(h[4:8], 'little') ^ K2
    idx = _crypt(data[16:16 + size], size, ((size + 4) & ~3) >> 2, True)[:size]
    entries = []
    p = 0
    while p + 8 <= len(idx):
        off = int.from_bytes(idx[p:p + 4], 'little')
        sz = int.from_bytes(idx[p + 4:p + 8], 'little')
        q = p + 8
        e = idx.find(b'\x00', q)
        if e < 0:
            break
        entries.append((idx[q:e].decode('latin1'), off, sz))
        p = (e + 1 + 3) & ~3
    seed = int.from_bytes(h[4:8], 'little')
    return data, entries, _table(seed)

def getfile(data, table, off, sz):
    return _xor_key(data[off:off + sz], table)

def _make_header(index_size, blk2):
    magic = 0x65525841
    A0 = (magic ^ blk2) & MASK
    K1 = ks(A0)
    K2 = ks(K1)
    raw47 = (index_size ^ K2) & MASK
    hb = bytearray(16)
    hb[0:4] = b'AXRe'
    hb[4:8] = raw47.to_bytes(4, 'little')
    hb[8:12] = blk2.to_bytes(4, 'little')
    # checksum: H = h4 ^ XOR_{k=1..7} rotr8-ish(h[4+k], k)  (exe 0x428124 루프)
    H = hb[4]
    for k in range(1, 8):
        v = hb[4 + k]
        H ^= ((v >> k) | ((v << (8 - k)) & MASK)) & MASK
    H &= MASK
    # 체크섬: K3 = ks(K2) (A0->K1->K2->K3 연속 적용, exe 0x4281C4~0x428208).
    # 게임 검증식: (K3 ^ LE32(h12:15)) 은 상위3바이트=0, 하위바이트=H&0xff 이어야 함.
    K3 = ks(K2)
    hb[12:16] = ((K3 ^ (H & 0xff)) & MASK).to_bytes(4, 'little')
    return bytes(hb), raw47

def pack(files, blk2=0x007E7A4D):
    """files: [(name, data_bytes)] -> 유효한 AXRe 아카이브 bytes.

    중요: 원본은 헤더의 index_size 필드 = 실제 인덱스바이트 + 8 이다(항상 +8).
    파일 데이터는 16 + 실제인덱스바이트 위치에서 시작하며(마지막 8B는 첫 파일과 겹침),
    게임은 size(=+8)바이트를 인덱스로 읽되 507엔트리만 파싱하고 나머지 8B는 무시한다.
    이 +8 규칙을 지켜야 콘텐츠 키스트림 시드(raw47)와 인덱스 키가 원본과 일치한다.
    """
    # 데이터 시작 오프셋을 알아야 하므로 먼저 실제 인덱스 바이트 길이를 계산
    idx = bytearray()
    for name, dat in files:
        idx += b'\x00\x00\x00\x00' + len(dat).to_bytes(4, 'little') + name.encode('latin1') + b'\x00'
        while len(idx) % 4:
            idx.append(0)
    index_bytes = len(idx)          # 실제 인덱스 길이 (엔트리들)
    size_field = index_bytes + 8    # 헤더 필드 & 인덱스 암호 키 (원본 규칙)
    header, raw47 = _make_header(size_field, blk2)
    cur = 16 + index_bytes          # 첫 파일 오프셋 (size_field가 아니라 index_bytes 기준)
    idx2 = bytearray()
    for name, dat in files:
        idx2 += cur.to_bytes(4, 'little') + len(dat).to_bytes(4, 'little') + name.encode('latin1') + b'\x00'
        while len(idx2) % 4:
            idx2.append(0)
        cur += len(dat)
    # 인덱스는 index_bytes만 물리적으로 기록. 암호 키=size_field(스트림이라 앞부분은 길이 무관).
    enc_idx = _crypt(idx2, size_field, ((index_bytes + 3) & ~3) >> 2, False)[:index_bytes]
    tbl = _table(raw47)
    out = bytearray(header) + enc_idx
    for name, dat in files:
        out += _xor_key(dat, tbl)
    return bytes(out)

# 앞 4바이트 시그니처 → 종류 이름. 정찰 단계에서 아카이브 성격을 바로 가리기 위한 것.
_MAGIC = [
    (b'VNEG',      "VNEG"),      # 시나리오 스크립트
    (b'AXRe',      "AXRe"),      # 아카이브 안 아카이브
    (b'\x89PNG',   "PNG"),       # 이미지
    (b'DMJ0',      "DMJ0"),      # 무비
]

def kind_of(name, head):
    """엔트리 종류 힌트. 시그니처가 먼저, 없으면 확장자."""
    for sig, label in _MAGIC:
        if head.startswith(sig):
            return label
    ext = os.path.splitext(name)[1].lstrip('.').lower()
    return ext or "?"

def cmd_list(a):
    data, entries, tbl = load(a.archive)
    rows = []
    for name, off, sz in entries:
        head = getfile(data, tbl, off, min(4, sz)) if sz else b''
        rows.append({"name": name, "offset": off, "size": sz,
                     "kind": kind_of(name, head)})
    key = {"name": lambda r: r["name"], "size": lambda r: r["size"],
           "offset": lambda r: r["offset"]}[a.sort]
    rows.sort(key=key)
    total = sum(r["size"] for r in rows)
    if a.json:
        print(json.dumps({"archive": a.archive, "count": len(rows),
                          "total_size": total, "entries": rows},
                         ensure_ascii=False, indent=2))
        return
    w = max([len(r["name"]) for r in rows] + [4])
    print("%-*s  %10s  %10s  %s" % (w, "이름", "오프셋", "크기", "종류"))
    for r in rows:
        print("%-*s  0x%08X  %10d  %s"
              % (w, r["name"], r["offset"], r["size"], r["kind"]))
    print("엔트리 %d개, 합계 %d바이트" % (len(rows), total))

def cmd_unpack(a):
    data, entries, tbl = load(a.archive)
    os.makedirs(a.out_dir, exist_ok=True)
    for name, off, sz in entries:
        with open(os.path.join(a.out_dir, name), 'wb') as f:
            f.write(getfile(data, tbl, off, sz))
    print("추출 %d개 -> %s" % (len(entries), a.out_dir))

def cmd_repack(a):
    in_dir, out = a.in_dir, a.archive
    blk2 = 0x007E7A4D
    if a.blk2_from:
        blk2 = int.from_bytes(open(a.blk2_from, 'rb').read()[8:12], 'little')
    # 원본 인덱스 순서 유지를 위해 정렬 없이 디렉터리 순서 사용 권장: 여기선 이름순
    files = [(os.path.basename(p), open(p, 'rb').read()) for p in sorted(glob.glob(os.path.join(in_dir, '*')))]
    open(out, 'wb').write(pack(files, blk2))
    print("리팩 %d개 -> %s" % (len(files), out))

def main():
    ap = argparse.ArgumentParser(
        prog="ujyu axr", description="AXRe 아카이브 언팩/리팩",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="예:\n"
               "  ujyu axr list scenario.axr\n"
               "  ujyu axr unpack scenario.axr _out\n"
               "  ujyu axr repack _out scenario.axr --blk2-from 원본/scenario.axr")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", prog="ujyu axr list",
                       help="풀지 않고 엔트리 목록만 본다",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="AXRe 아카이브 엔트리 목록 (이름/오프셋/크기/종류)",
                       epilog="예:\n"
                              "  ujyu axr list 원본/scenario.axr\n"
                              "  ujyu axr list 원본/cg.axr --sort size --json\n"
                              "종류는 앞 4바이트 시그니처(VNEG/AXRe/PNG/DMJ0)로, 없으면 확장자로 매긴다.")
    p.add_argument("archive", help="입력 아카이브 (.axr/.ax2/…)")
    p.add_argument("--sort", choices=["name", "size", "offset"], default="offset",
                   help="정렬 기준 (기본: offset = 아카이브 수록 순서)")
    p.add_argument("--json", action="store_true", help="기계 판독용 JSON 으로 출력")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("unpack", help="아카이브를 폴더로 푼다")
    p.add_argument("archive", help="입력 아카이브 (.axr/.ax2/…)")
    p.add_argument("out_dir", help="추출할 폴더 (없으면 만든다)")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("repack", help="폴더를 아카이브로 다시 묶는다")
    p.add_argument("in_dir", help="묶을 파일들이 든 폴더 (이름순으로 담는다)")
    p.add_argument("archive", help="출력 아카이브 경로")
    p.add_argument("--blk2-from", help="원본 아카이브에서 blk2 값을 가져온다 (기본: 0x007E7A4D)")
    p.set_defaults(func=cmd_repack)

    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help(); return 1
    a.func(a)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
