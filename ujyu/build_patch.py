#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전체 패치 빌드 — 무패치 원본에서 매번 다시 생성 (SKILL 13절).

무패치 원본은 `config.ORIG_DIR` 에 둔다. 이 스크립트는 거기서 읽어 번역/패치를
씌운 결과를 배포 폴더 `config.GAME_DIR` 에 쓴다. 몇 번을 돌려도 결과가 같고,
'텍스트 주입 → 창 제목' 순서가 강제된다. (제목 패치는 common.csv 를 다시 팩하므로
반드시 텍스트 주입 뒤에 와야 한다. 순서가 바뀌면 제목이 원본으로 되돌아간다.)

  0. exe           : ORIG exe → UI·엔진·코드케이브        (patch_exe)
  1. scenario 전체  : ORIG + strings.json(v2) → 유효본 arc별 주입 + 점프테이블 재매핑
                     (inject_text.build — 패치 아카이브 포함 전부 처리)
  2. common.csv    : 창 제목·기본 글꼴                     (patch_title)  ← 1 뒤
  3. cg 이미지     : ORIG + 번역 PNG 주입                  (CG_TRANS_DIR 있으면)
  4. movie         : 무비 아카이브 배치                     (MOVIE_SRC_DIR 있으면 거기서)

사용:
  ujyu build                  # 전체
  ujyu build cg               # cg.axr 만
  ujyu build all --no-exe     # exe 빼고 전체
"""
import os, sys, shutil, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ujyu.titleconfig import config as C
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ujyu.formats import axr as A
from ujyu import inject_text
from ujyu import patch_title
from ujyu import patch_exe
from ujyu import scale

SCALE = getattr(C, "SCALE", 1)


def step_exe():
    print("[0] exe — UI·엔진·코드케이브 (from %s)" % C.EXE_IN)
    patch_exe.build()
    if SCALE > 1:
        scale.cmd_exe(C.EXE_OUT, C.EXE_OUT, SCALE)
    if getattr(C, "MOVIE_NATIVE", False):
        # 무비 2배 확대를 끈다 — 무비도 화면 치수로 인코딩해야 한다(RESOLUTION.md §6-1)
        off = getattr(C, "OFF_MOVIE_SCALE", None)
        if off is None:
            raise SystemExit("MOVIE_NATIVE=True 인데 OFF_MOVIE_SCALE 이 없다")
        b = bytearray(open(C.EXE_OUT, "rb").read())
        if b[off] == 0x6A:                    # push opcode 를 가리켰으면 즉치로
            off += 1
        if b[off - 1] != 0x6A or b[off] not in (0, 2):
            raise SystemExit("OFF_MOVIE_SCALE %#x 가 push imm8 이 아니다" % off)
        b[off] = 0
        open(C.EXE_OUT, "wb").write(bytes(b))
        print("    무비 2배 확대 OFF (네이티브 재생)")


def step_scenario():
    print("[1] scenario 아카이브 전체 — 텍스트 주입 + 점프테이블 재매핑 (from %s)" % C.ORIG_DIR)
    n_tl, errors = inject_text.build(C.GAME_DIR, verbose=True)
    print("    번역주입 %d조각%s" % (n_tl, "" if not errors else "  (오류 %d건)" % len(errors)))
    if SCALE > 1:
        targets = [C.game(a) for a in C.ARCHIVES]
        scale.cmd_common(targets, SCALE)      # 각 아카이브 자기 common.csv 제자리 ×N
        scale.cmd_center(targets, SCALE)      # 스케일 비대상 창(선택지 등) 가운데 정렬
        scale.cmd_dims(targets, SCALE)        # 명시 치수 씬 ×N (SCN_DIMS)
        scale.cmd_dims_auto(targets, SCALE)   # 좌표 심볼 자동 도출 씬 ×N (SCN_DIMS_AUTO)


def step_title():
    if not C.COMMON_CSV:
        print("[2] common.csv 설정 없음 — 건너뜀"); return
    print("[2] common.csv — 창 제목·기본 글꼴")
    patch_title.apply()


def step_cg():
    if not C.CG_TRANS_DIR or not os.path.isdir(C.CG_TRANS_DIR):
        print("[3] 번역 이미지 폴더 없음 — 건너뜀"); return
    src = C.orig(C.CG_ARCHIVE)
    if not os.path.exists(src):
        print("[3] %s 원본 없음 — 건너뜀" % C.CG_ARCHIVE); return
    print("[3] %s — 번역 이미지 주입 (from %s)" % (C.CG_ARCHIVE, src))
    data, entries, tbl = A.load(src)
    blk2 = int.from_bytes(open(src, "rb").read()[8:12], "little")
    trans = {f: os.path.join(C.CG_TRANS_DIR, f)
             for f in os.listdir(C.CG_TRANS_DIR) if f.lower().endswith(".png")}
    files, rep = [], 0
    for name, off, sz in entries:
        if name in trans:
            files.append((name, open(trans[name], "rb").read())); rep += 1
        else:
            files.append((name, A.getfile(data, tbl, off, sz)))
    open(C.game(C.CG_ARCHIVE), "wb").write(A.pack(files, blk2))
    print("    엔트리 %d / 교체 %d" % (len(files), rep))
    if SCALE > 1:
        up = getattr(C, "CG_UPSCALE_DIR", None)     # 외부 AI 업스케일 결과(같은 파일명, 원본×N)
        if up and not os.path.isdir(up):
            print("    ⚠ CG_UPSCALE_DIR 없음(%s) — bilinear 리샘플" % up); up = None
        elif up:
            print("    ×%d 이미지: 외부 업스케일 %s 에서 주입" % (SCALE, up))
        scale.cmd_cg(C.game(C.CG_ARCHIVE), C.game(C.CG_ARCHIVE), SCALE, from_dir=up)


def step_movie():
    """무비 아카이브를 배포 폴더에 놓는다.

    무비는 번역 대상이 아니라 **치수** 문제다. `MOVIE_NATIVE` 로 엔진의 2배 확대를
    끄면 무비를 화면 치수로 다시 인코딩해야 하는데(`ujyu dmj encode`), 그 결과물을
    `MOVIE_SRC_DIR` 에 두면 여기서 가져온다. 없으면 원본을 그대로 복사한다.
    """
    arc = getattr(C, "MOVIE_ARCHIVE", None) or "movie.axr"
    src_dir = getattr(C, "MOVIE_SRC_DIR", None)
    src = os.path.join(src_dir, arc) if src_dir else C.orig(arc)
    if not os.path.exists(src):
        print("[4] %s 없음(%s) - 건너뜀" % (arc, src)); return
    dst = C.game(arc)
    if os.path.abspath(src) == os.path.abspath(dst):
        print("[4] %s 원본과 대상이 같음 - 건너뜀" % arc); return
    same = (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src))
    print("[4] %s - %s 에서 배치%s"
          % (arc, "MOVIE_SRC_DIR" if src_dir else "ORIG", " (크기 동일, 재복사)" if same else ""))
    shutil.copy2(src, dst)
    print("    %.1f MB -> %s" % (os.path.getsize(dst) / 1048576.0, dst))
    if SCALE > 1 and not getattr(C, "MOVIE_NATIVE", False):
        print("    참고: MOVIE_NATIVE 가 꺼져 있어 엔진이 무비를 2배로 확대해 그린다")


STEPS = {"exe": step_exe, "scenario": step_scenario, "title": step_title,
         "cg": step_cg, "movie": step_movie}
ORDER = ["exe", "scenario", "title", "cg", "movie"]   # 실행 순서 (title 은 scenario 뒤)
_ALIAS = {"text": "scenario", "image": "cg"}


def main():
    if os.path.abspath(C.ORIG_DIR) == os.path.abspath(C.GAME_DIR):
        raise SystemExit("ORIG_DIR 와 GAME_DIR 가 같다. 원본과 배포 폴더를 분리하라.")
    ap = argparse.ArgumentParser(
        prog="ujyu build",
        description="원본에서 배포본을 조립한다. 단계: %s (생략하면 all)." % "/".join(ORDER))
    ap.add_argument("steps", nargs="*",
                    help="빌드할 단계: all | %s (별칭 text=scenario, image=cg). 생략하면 all."
                         % " | ".join(ORDER))
    for s in ORDER:
        ap.add_argument("--no-" + s, action="store_true", help="%s 단계 제외" % s)
    a = ap.parse_args()

    # positional → 요청 단계 (없거나 all 이면 전체)
    req = []
    for tok in a.steps:
        tok = _ALIAS.get(tok, tok)
        if tok == "all":
            req = list(ORDER); continue
        if tok not in STEPS:
            raise SystemExit("알 수 없는 단계: %r  (all | %s)" % (tok, " | ".join(ORDER)))
        if tok not in req:
            req.append(tok)
    if not req:
        req = list(ORDER)
    # --no-<단계> 제외
    req = [s for s in req if not getattr(a, "no_" + s)]
    if not req:
        raise SystemExit("실행할 단계가 없다.")

    if SCALE > 1:
        print("※ SCALE=%d — 각 단계에 해상도 확대(ujyu scale)를 포함해 빌드한다." % SCALE)
    if "title" in req and "scenario" not in req:
        print("⚠ title 은 scenario 재팩 뒤가 정상 — scenario 없이 실행하면 "
              "GAME_DIR 의 기존 아카이브를 편집한다.")
    for s in ORDER:                              # 항상 ORDER 순 (의존 보장)
        if s in req:
            STEPS[s]()
    print("완료: %s" % " ".join(s for s in ORDER if s in req))


if __name__ == "__main__":
    raise SystemExit(main())
