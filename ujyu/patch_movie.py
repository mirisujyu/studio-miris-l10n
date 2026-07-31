# -*- coding: utf-8 -*-
"""무비 2배 확대 끄기 패치 (docs/formats/RESOLUTION.md §6-1).

엔진은 무비 재생 객체를 만들 때 블리터에 **스케일 플래그 2**(=2배 확대)를 넘긴다.
그래서 원작의 320×240 무비가 640×480 화면을 채운다. 이 플래그를 0 으로 바꾸면
무비가 **1:1** 로 그려져 화면 치수와 같은 `.dmj` 를 네이티브 해상도로 재생할 수 있다.

  神無ノ鳥 실측 (VA `0x458EE7`, ctor `0x458E10` 안):
      6a 02        push 2          <- 확대 ON  (원작)
      6a 00        push 0          <- 확대 OFF (네이티브)
  플래그는 블리터 셋업 `0x45B7F0` 의 1번 비트로 들어간다(`and esi,2`).

찾는 법: 무비 재생 op 핸들러 -> 무비 객체 ctor 안, 디코더 셋업 호출 직전의
`push 2` 즉치. 주변에 `[obj+0x20/0x24/0x28]`(치수) 를 push 하는 코드가 있다.

주의: 확대를 끄면 **무비 치수 = 화면 치수** 여야 화면이 찬다. 절반짜리 무비를
그대로 두면 좌상단에 작게 나온다. `.dmj` 를 함께 다시 인코딩할 것.

  ujyu exe movie <exe> --off <파일오프셋> [--scale 1|2]
  # --scale 1 = 확대 끔(네이티브), 2 = 원작 동작
"""
import argparse, shutil, sys


def main():
    ap = argparse.ArgumentParser(description="무비 2배 확대 on/off")
    ap.add_argument("exe")
    ap.add_argument("--off", required=True,
                    help="`push <flag>` 의 즉치 **파일 오프셋**(0x… 허용). "
                         "명령 시작이 아니라 6a 다음 바이트를 가리켜도 되고, "
                         "6a 를 가리켜도 자동으로 +1 한다")
    ap.add_argument("--scale", type=int, choices=(1, 2), default=1,
                    help="1=확대 끔(네이티브 재생), 2=원작(2배 확대)")
    ap.add_argument("--backup", action="store_true", help="<exe>.bak 로 원본 보관")
    a = ap.parse_args()

    off = int(a.off, 0)
    b = bytearray(open(a.exe, "rb").read())
    if b[off] == 0x6A:                       # push opcode 를 가리켰다면 즉치로
        off += 1
    elif b[off - 1] != 0x6A:
        sys.exit("오프셋 %#x 주변에 push imm8(6a) 가 없다 — 오프셋을 확인할 것" % off)
    old = b[off]
    new = 0 if a.scale == 1 else 2
    if old not in (0, 2):
        sys.exit("예상치 못한 플래그 값 %#x — 잘못된 오프셋으로 보인다" % old)
    if a.backup:
        shutil.copy2(a.exe, a.exe + ".bak")
    b[off] = new
    open(a.exe, "wb").write(bytes(b))
    print("무비 스케일 플래그 %d -> %d (오프셋 %#x) = %s"
          % (old, new, off, "네이티브 1:1" if new == 0 else "2배 확대"))


if __name__ == "__main__":
    main()
