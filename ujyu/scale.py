# -*- coding: utf-8 -*-
"""해상도 N× 스케일 — config 주도 (docs/formats/RESOLUTION.md · SKILL 15절).

  ujyu scale common <아카이브...>          common.csv 제자리 ×N (아카이브별 자기 것)
  ujyu scale dims   <아카이브...>          C.SCN_DIMS 씬 치수 ×N (제자리)
  ujyu scale cg     <src> [--out dst] [--from-dir 결과폴더]
                                                     cg 콘텐츠 이미지 ×N (UI 1× 유지)
  ujyu scale exe    <src> [--out dst]      exe 화면 상수 ×N (+LARGEADDRESSAWARE)

외부 AI 업스케일러(Topaz 등) 연동:
  ujyu scale cg-export <원본 cg> --out <작업폴더> [--prefer <번역본 cg>]
       → <작업폴더>/1x/{art,small,translated} + _manifest.tsv   (입력, 재생성 가능)
       cg 이미지를 **전부** 뽑는다 (1× 유지 프리픽스로 거르지 않는다) — 나중에 확대
       대상으로 돌려도 업스케일본이 있게. 확대 안 하는 이미지는 주입 때 무시된다.
       업스케일 결과는 **다른 폴더**(예 <작업폴더>/2x/)에 저장한다. 1x/ 를 덮어쓰지 말 것.
  ujyu scale cg-check <작업폴더> <결과폴더>   결과 검사(누락·치수·알파)
  ujyu scale cg <원본 cg> --out <출력> --from-dir <결과폴더>   주입

배율은 config `SCALE`(--scale 로 오버라이드). 타이틀 설정 (config.py):
  ORIG_W/ORIG_H            원본 해상도 (기본 640×480)
  SCALE_DIALOG_1X          크기 1× 유지 + 우하단 시프트할 창 이름들 (SKILL 15-6)
  SCALE_FS_WINDOWS         w/h·패딩을 ×N 할 풀스크린 textwindow 이름들
  SCALE_COMMON_INTS        ×N 할 `int,<이름>,<값>` 전역 수치 (글꼴 크기·줄 높이)
  SCN_DIMS                 {scn파일: [(오프셋, 바이트폭, 원본값), ...]} — 명시 치수 (15-5)
  SCN_VALUE_REMAP          {scn파일: [((시작심볼, 끝심볼), {옛값: 새값})]} — ×N 전 값 치환
  SCN_REPOINT              {scn파일: [((참조오프셋...), 원본값)]} — 공유 심볼을 참조 단위로
                           갈라 ×N (한 값이 ×N 자리와 1× 자리에 같이 쓰일 때)
  CG_CONTENT_PREFIX / CG_UI_1X_PREFIX / CG_FORCE_1X   cg 이미지 분류 (15-4·15-7)
  OFF_SCREEN_W / OFF_SCREEN_H   exe 화면 폭/높이 dword 파일오프셋들 (15-2)

common.csv 는 **각 아카이브 자기 것을 제자리** 편집한다 — 다른 아카이브 것을 주입하면
고유 변수 정의가 사라져 크래시한다 (docs/formats/COMMON_CSV.md).
"""
import argparse, collections, io, os, struct, sys

try:                                   # 콘솔이 CP949 여도 출력이 죽지 않게
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ujyu.titleconfig import config as C
sys.path.insert(0, os.path.dirname(HERE))
from ujyu.formats import axr as A

ORIG_W = getattr(C, "ORIG_W", 640)
ORIG_H = getattr(C, "ORIG_H", 480)


# ─────────────────────────────────────────── common.csv
def scale_common_line(raw, N, dialog, fs_windows, ints=()):
    try:
        s = raw.decode("ascii")
    except UnicodeDecodeError:
        return raw
    t = s.split(",")
    if len(t) < 2:
        return raw

    def mul(i):
        if i < len(t) and t[i].strip().lstrip("-").isdigit():
            t[i] = str(int(t[i]) * N)

    def add(i, v):
        if i < len(t) and t[i].strip().lstrip("-").isdigit():
            t[i] = str(int(t[i]) + v)

    k, name = t[0], t[1]
    if k == "int" and name in ints:          # 전역 수치(글꼴 크기·줄 높이 등) ×N
        mul(2)
    elif name in dialog:                     # 대화창: 위치만 우하단 시프트, 크기 1×
        add(2, ORIG_W * (N - 1)); add(3, ORIG_H * (N - 1))
    elif k == "layer":                       # 월드 레이어: x,y,w,h ×N
        for i in range(2, 6):
            mul(i)
    elif k == "textwindow" and name in fs_windows:   # 풀스크린 창: w,h + 패딩 ×N
        for i in range(4, 10):
            mul(i)
    else:                                    # select·나머지 int 등: 불변
        return raw
    return ",".join(t).encode("ascii")


def scale_common(orig, N):
    dialog = set(getattr(C, "SCALE_DIALOG_1X", []))
    fs = set(getattr(C, "SCALE_FS_WINDOWS", []))
    ints = set(getattr(C, "SCALE_COMMON_INTS", ()) or ())
    nl = b"\r\n" if b"\r\n" in orig else b"\n"
    return nl.join(scale_common_line(l, N, dialog, fs, ints) for l in orig.split(nl))


def _int_field(t, i):
    """t[i] 가 정수면 값, 아니면 None."""
    if i < len(t) and t[i].strip().lstrip("-").isdigit():
        return int(t[i])
    return None


def center_common(orig, N, name, hint=None):
    """스케일 **비대상**인 창(`textwindow,<name>` + `int,<name>_*`)을 화면 가운데로.

    `scale_common` 은 `int,*` 와 선택지 창을 건드리지 않는다 — 치수가 버튼·글꼴
    계산에 그대로 쓰여 ×N 하면 어긋나기 때문이다. 그래서 화면만 ×N 되면 그 창이
    **좌상단에 몰린다**. 크기는 1× 로 두고 위치(x, y)만 다시 잡는다.

      textwindow,<name>,x,y,w,h,…  ->  x=(W-w)//2, y=(H-h)//2
      int,<name>_x                 ->  (W - <name>_width)//2
      int,<name>_y                 ->  (H - hint)//2     (hint = 런타임 높이)

    런타임 높이는 선택지 개수에 따라 변하므로(`select_height` 를 flow 가 계산)
    세로는 config 의 대표값(hint)으로 맞춘다.
    """
    W, H = ORIG_W * N, ORIG_H * N
    nl = b"\r\n" if b"\r\n" in orig else b"\n"
    lines = orig.split(nl)
    width = None
    for l in lines:                                   # 1차: <name>_width 수집
        t = l.decode("ascii", "ignore").split(",")
        if len(t) >= 3 and t[0] == "int" and t[1] == name + "_width":
            width = _int_field(t, 2)
    out = []; n_fix = 0
    for l in lines:
        try:
            t = l.decode("ascii").split(",")
        except UnicodeDecodeError:
            out.append(l); continue
        new = None
        if len(t) >= 6 and t[0] == "textwindow" and t[1] == name:
            w, h = _int_field(t, 4), _int_field(t, 5)
            if w is not None and h is not None:
                t[2] = str((W - w) // 2); t[3] = str((H - h) // 2); new = t
        elif len(t) >= 3 and t[0] == "int" and t[1] == name + "_x" and width:
            t[2] = str((W - width) // 2); new = t
        elif len(t) >= 3 and t[0] == "int" and t[1] == name + "_y" and hint:
            t[2] = str((H - hint) // 2); new = t
        if new is None:
            out.append(l)
        else:
            out.append(",".join(new).encode("ascii")); n_fix += 1
    return nl.join(out), n_fix


def cmd_center(archives, N):
    """config.COMMON_CENTER 의 창을 확대 화면 가운데로 (제자리, 1× 는 무시)."""
    if N <= 1:
        return
    targets = getattr(C, "COMMON_CENTER", None) or {}
    if not targets:
        return
    for path in archives:
        d, e, t = A.load(path)
        by = {n: (o, s) for n, o, s in e}
        if "common.csv" not in by:
            continue
        cc = A.getfile(d, t, *by["common.csv"]); tot = 0
        for name, hint in targets.items():
            cc, n = center_common(cc, N, name, hint)
            tot += n
        if not tot:
            continue
        files = [(n, cc if n.lower() == "common.csv" else A.getfile(d, t, o, s))
                 for n, o, s in e]
        blk2 = int.from_bytes(open(path, "rb").read()[8:12], "little")
        open(path, "wb").write(A.pack(files, blk2))
        print("  %s: %s 창 가운데 정렬 (%d항목, 화면 %d×%d)"
              % (os.path.basename(path), "/".join(targets), tot, ORIG_W * N, ORIG_H * N))


def cmd_common(archives, N):
    for path in archives:
        d, e, t = A.load(path)
        by = {n: (o, s) for n, o, s in e}
        if "common.csv" not in by:
            print("  %s: common.csv 없음, 스킵" % os.path.basename(path)); continue
        cc = scale_common(A.getfile(d, t, *by["common.csv"]), N)
        files = [(n, cc if n.lower() == "common.csv" else A.getfile(d, t, o, s))
                 for n, o, s in e]
        blk2 = int.from_bytes(open(path, "rb").read()[8:12], "little")
        open(path, "wb").write(A.pack(files, blk2))
        print("  %s: 자기 common.csv ×%d (제자리, 정의 보존)" % (os.path.basename(path), N))


# ─────────────────────────────────────────── .scn 명시 치수
def cmd_dims_auto(archives, N):
    """`config.SCN_DIMS_AUTO` 의 씬은 좌표 심볼을 **그 자리에서 도출**해 ×N 한다.

    메뉴·세이브·설정처럼 좌표가 전부 VNEG int 심볼인 화면이 대상이다
    (ujyu/scn_dims.py 의 규칙). 오프셋을 동결하지 않으므로 번역으로 문자열 길이가
    바뀌어도 안전하다 — **텍스트 주입 뒤**에 부르면 된다.
    """
    names = set(getattr(C, "SCN_DIMS_AUTO", ()) or ())
    if not names:
        return
    import scn_dims
    remaps = getattr(C, "SCN_VALUE_REMAP", None) or {}
    for path in archives:
        arc = os.path.basename(path)
        d, e, t = A.load(path)
        files = []; touched = 0
        for n, o, s in e:
            raw = A.getfile(d, t, o, s)
            if n in names:
                rule = remaps.get("%s/%s" % (arc, n), remaps.get(n))
                n_rm = 0
                if rule:                       # ×N 전에 값 치환 (표 원소 등)
                    raw, n_rm = scn_dims.remap_values(raw, rule)
                raw, n_ent, n_rep, skipped = scn_dims.apply(raw, N)
                touched += 1
                print("  %s/%s: 좌표 %d개 ×%d%s%s%s"
                      % (arc, n, n_ent, N,
                         ", 값치환 %d개" % n_rm if n_rm else "",
                         ", 공유 %d개 분리" % n_rep if n_rep else "",
                         ", 미해결 %d개" % len(skipped) if skipped else ""))
            files.append((n, raw))
        if touched:
            blk2 = int.from_bytes(open(path, "rb").read()[8:12], "little")
            open(path, "wb").write(A.pack(files, blk2))


def cmd_dims(archives, N):
    dims_map = getattr(C, "SCN_DIMS", {}) or {}
    rep_map = getattr(C, "SCN_REPOINT", {}) or {}
    if not dims_map and not rep_map:
        print("  SCN_DIMS 비어 있음 — 스킵 (config 에 채우는 법: SKILL 15-5)"); return
    import scn_dims
    for path in archives:
        arc = os.path.basename(path)
        d, e, t = A.load(path)
        files = []; touched = 0
        for n, o, s in e:
            raw = A.getfile(d, t, o, s)
            # 같은 이름이 아카이브마다 다를 수 있다(패치 증분) — "<아카이브>/<이름>" 우선
            def _key(m):
                k = "%s/%s" % (arc, n)
                return k if k in m else (n if n in m else None)
            key, rkey = _key(dims_map), _key(rep_map)
            if key:
                buf = bytearray(raw)
                for off, width, base in dims_map[key]:
                    cur = int.from_bytes(buf[off:off+width], "big")
                    assert cur == base, "%s@0x%x: 원본값 %d 예상, 실제 %d" % (n, off, base, cur)
                    buf[off:off+width] = (base * N).to_bytes(width, "big")
                raw = bytes(buf); touched += 1
                print("  %s/%s: dim %d개 ×%d" % (arc, n, len(dims_map[key]), N))
            if rkey:
                # 심볼 값(오프셋 < flow)을 다 고친 뒤에 한다 — 새 심볼을 flow 앞에
                # 끼워 넣어 그 뒤 오프셋이 밀리기 때문이다.
                raw, n_ref = scn_dims.repoint_refs(raw, rep_map[rkey], N)
                touched += 1
                print("  %s/%s: 참조 %d개를 새 심볼 %d개로 분리 ×%d"
                      % (arc, n, n_ref, len(rep_map[rkey]), N))
            files.append((n, raw))
        if touched:
            blk2 = int.from_bytes(open(path, "rb").read()[8:12], "little")
            open(path, "wb").write(A.pack(files, blk2))


# ─────────────────────────────────────────── cg 이미지
def is_content(name):
    if name in getattr(C, "CG_FORCE_1X", ()): return False
    if any(name.startswith(p) for p in getattr(C, "CG_UI_1X_PREFIX", ())): return False
    if any(name.startswith(p) for p in getattr(C, "CG_CONTENT_PREFIX", ())): return True
    return False


def cmd_cg_export(src, outdir, N, prefer=None):
    """cg 이미지를 **전부** 폴더로 뽑는다 — 외부 업스케일러(Topaz 등) 입력용.

    1× 유지 프리픽스로 거르지 않는다. 나중에 어떤 이미지를 콘텐츠(확대 대상)로 돌려도
    업스케일본이 없어 뿌옇게 늘어나는 일이 없게 미리 만들어 두는 것이다 — 있어도
    `cg --from-dir` 이 **확대 대상일 때만 쓰고 아니면 무시**하므로 손해가 없다.

    두 갈래로 나눠 쓴다:
      art/    화면 높이만큼 큰 그림 (배경·캐릭터 스프라이트) — AI 업스케일 값어치가 큼
      small/  섬네일·버튼·로고 등 작은 것 — 보통 리샘플로 충분

    src 는 보통 **무패치 원본** cg 아카이브(깨끗한 화질)를 쓴다. 다만 콘텐츠 이미지
    중에도 글자가 그려져 번역된 것이 있으므로, `prefer` 에 번역본 아카이브를 주면
    내용이 다른 엔트리는 **번역본을 `translated/` 로 따로** 뽑는다 (원본을 뽑아 넣으면
    번역이 되돌아간다). 글자가 있어 AI 업스케일이 뭉갤 수 있으니 따로 다룬다.

    `_manifest.tsv` 에 폴더·출처(`src`=원본 / `prefer`=번역본)와 원본/목표 치수를 적는다.
    업스케일 결과는 **같은 파일명**으로 두고 `cg --from-dir <폴더>` 로 주입한다
    (하위 폴더까지 재귀 탐색).
    """
    from PIL import Image
    d, e, t = A.load(src)
    pd = pe_ = pt = None; pby = {}
    if prefer:
        pd, pe_, pt = A.load(prefer)
        pby = {n: (o, s) for n, o, s in pe_}
    dirs = {k: os.path.join(outdir, "1x", k) for k in ("art", "small", "translated")}
    for p in dirs.values():
        os.makedirs(p, exist_ok=True)
    rows = []; cnt = {"art": 0, "small": 0, "translated": 0}
    for n, o, s in e:
        if not n.lower().endswith(".png"):
            continue
        data = A.getfile(d, t, o, s)
        origin = "src"
        if n in pby:
            pdata = A.getfile(pd, pt, *pby[n])
            if pdata != data:                          # 번역·수정된 이미지
                data = pdata; origin = "prefer"
        im = Image.open(io.BytesIO(data))
        big = im.height >= ORIG_H                      # 전체 높이 = 배경·스프라이트
        sub = "translated" if origin == "prefer" else ("art" if big else "small")
        open(os.path.join(dirs[sub], n), "wb").write(data)
        rows.append((n, sub, origin, im.width, im.height, im.width * N, im.height * N))
        cnt[sub] += 1
    with io.open(os.path.join(outdir, "_manifest.tsv"), "w", encoding="utf-8") as f:
        f.write("file\tgroup\torigin\tsrc_w\tsrc_h\tdst_w\tdst_h\n")
        for r in rows:
            f.write("%s\t%s\t%s\t%d\t%d\t%d\t%d\n" % r)
    print("  cg export ×%d (전체) -> %s" % (N, outdir))
    print("     1x/art/        %3d개 (배경·스프라이트, AI 업스케일 권장)" % cnt["art"])
    print("     1x/small/      %3d개 (섬네일·버튼·로고)" % cnt["small"])
    if prefer:
        print("     1x/translated/ %3d개 (이미 번역된 이미지: 글자 뭉개짐 주의, 별도 처리)"
              % cnt["translated"])
    print("     업스케일 결과는 다른 폴더(예 %s/%dx/)에 저장하세요. 1x/ 는 입력이라"
          % (outdir, N))
    print("     덮어쓰면 재추출 전까지 원본이 사라집니다. 확인: scale.py cg-check <결과폴더>")
    uniq = {}
    for _n, _g, _o, w, h, _dw, _dh in rows:
        uniq[(w, h)] = uniq.get((w, h), 0) + 1
    top = sorted(uniq.items(), key=lambda kv: -kv[1])[:4]
    print("     크기 분포: " + ", ".join("%dx%d ×%d개" % (wh[0], wh[1], c) for wh, c in top))


def cmd_cg_check(workdir, resdir, N):
    """업스케일 결과를 `_manifest.tsv` 기준으로 검사한다 (주입 전 확인용).

    매니페스트는 `cg-export` 가 쓰고 이 명령이 읽는다 — 손으로 고치지 않는다.
    재추출하면 새로 만들어지므로, 대상 목록이 바뀌면 export 를 다시 돌리면 된다.
    """
    from PIL import Image
    mf = os.path.join(workdir, "_manifest.tsv")
    if not os.path.exists(mf):
        raise SystemExit("매니페스트 없음: %s (scale.py cg-export 를 먼저 실행)" % mf)
    rows = [l.rstrip("\n").split("\t") for l in io.open(mf, encoding="utf-8")][1:]
    cand = collections.defaultdict(list)
    for root, _dirs, fs in os.walk(resdir):
        for f in fs:
            if f.lower().endswith(".png"):
                cand[f].append(os.path.join(root, f))
    ok = miss = wrong = mode_chg = 0
    prob = []
    for n, grp, origin, sw, sh, dw, dh in rows:
        dw, dh = int(dw), int(dh)
        paths = cand.get(n, [])
        hit = None
        for p in paths:
            im = Image.open(p)
            if (im.width, im.height) == (dw, dh):
                hit = (p, im); break
        if hit is None:
            if paths:
                im = Image.open(paths[0])
                wrong += 1
                prob.append("%s [%s] 치수 %dx%d (기대 %dx%d)" % (n, grp, im.width, im.height, dw, dh))
            else:
                miss += 1
                prob.append("%s [%s] 결과 없음" % (n, grp))
            continue
        src_mode = Image.open(os.path.join(workdir, "1x", grp, n)).mode
        if hit[1].mode != src_mode:
            mode_chg += 1
            prob.append("%s [%s] 모드 %s -> %s (알파 손실 주의)" % (n, grp, src_mode, hit[1].mode))
        ok += 1
    print("  cg-check ×%d: 정상 %d / 누락 %d / 치수불일치 %d / 모드변경 %d (전체 %d)"
          % (N, ok, miss, wrong, mode_chg, len(rows)))
    for p in prob[:20]:
        print("     " + p)
    if len(prob) > 20:
        print("     … 외 %d건" % (len(prob) - 20))
    print("  (누락·치수불일치는 주입 시 자동 보정되지만, 그만큼 AI 업스케일 효과가 없다)")


def cmd_cg(src, out, N, from_dir=None):
    """콘텐츠 이미지를 ×N 한 cg 아카이브를 만든다.

    from_dir 가 있으면 그 폴더의 **외부 업스케일 결과**(같은 파일명)를 쓰고,
    없으면 bilinear 로 리샘플한다. 외부 결과는 치수가 정확히 원본×N 이어야 하며
    (서피스 치수와 어긋나면 클램프 크래시), 어긋나면 그 자리에서 리샘플로 맞춘다.
    """
    from PIL import Image
    d, e, t = A.load(src)
    found = collections.defaultdict(list)
    if from_dir:                                        # 하위 폴더까지 재귀
        for root, _dirs, fs in os.walk(from_dir):
            for f in fs:
                if f.lower().endswith(".png"):
                    found[f].append(os.path.join(root, f))
    files = []; nsc = n1 = n_ext = n_fix = 0
    missing = []
    for n, o, s in e:
        data = A.getfile(d, t, o, s)
        if n.lower().endswith(".png") and is_content(n) and N != 1:
            im = Image.open(io.BytesIO(data))
            tw, th = im.width * N, im.height * N
            # 같은 파일명이 여러 곳(1x 입력 폴더 + 업스케일 결과 폴더)에 있을 수 있다.
            # **목표 치수와 정확히 맞는 것**을 고르고, 없으면 가장 큰 것을 쓴다.
            cands = []
            for p in found.get(n, []):
                try:
                    c = Image.open(p); cands.append((c.width * c.height, c, p))
                except Exception:
                    pass
            exact = [c for c in cands if (c[1].width, c[1].height) == (tw, th)]
            pick = (exact or sorted(cands, reverse=True))[0] if cands else None
            if pick is not None:
                im2 = pick[1]
                if (im2.width, im2.height) != (tw, th):     # 외부 결과 치수 보정
                    im2 = im2.resize((tw, th), Image.LANCZOS); n_fix += 1
                if im2.mode != im.mode:
                    im2 = im2.convert(im.mode)
                n_ext += 1
            else:
                if from_dir:
                    missing.append(n)
                im2 = im.resize((tw, th), Image.BILINEAR)
            b = io.BytesIO(); im2.save(b, "PNG"); data = b.getvalue(); nsc += 1
        else:
            n1 += 1
        files.append((n, data))
    blk2 = int.from_bytes(open(src, "rb").read()[8:12], "little")
    open(out, "wb").write(A.pack(files, blk2))
    print("  cg: 콘텐츠 ×%d %d개 (외부 업스케일 %d, 치수보정 %d) / 1× %d개 -> %s"
          % (N, nsc, n_ext, n_fix, n1, out))
    if missing:
        print("     ⚠️ from-dir 에 없어 bilinear 로 처리한 %d개: %s%s"
              % (len(missing), ", ".join(missing[:5]), " …" if len(missing) > 5 else ""))


# ─────────────────────────────────────────── exe 화면 상수
def cmd_exe(src, out, N):
    w_off = getattr(C, "OFF_SCREEN_W", []) or []
    h_off = getattr(C, "OFF_SCREEN_H", []) or []
    if not w_off or not h_off:
        raise SystemExit("config 에 OFF_SCREEN_W / OFF_SCREEN_H 를 채우세요 (SKILL 15-2)")
    b = bytearray(open(src, "rb").read())
    for off, base, new in [(o, ORIG_W, ORIG_W * N) for o in w_off] + \
                          [(o, ORIG_H, ORIG_H * N) for o in h_off]:
        cur = struct.unpack("<I", b[off:off+4])[0]
        assert cur == base, "off 0x%x: %d 예상, 실제 %d" % (off, base, cur)
        b[off:off+4] = struct.pack("<I", new)
    print("  exe: 화면 %d×%d (폭 %d곳, 높이 %d곳)" % (ORIG_W*N, ORIG_H*N, len(w_off), len(h_off)))
    if N >= 2:                               # 큰 서피스 대비 (3× 실측 검증 방식)
        pe = struct.unpack("<I", b[0x3c:0x40])[0]
        ch = pe + 0x16                       # COFF Characteristics
        flags = struct.unpack("<H", b[ch:ch+2])[0]
        b[ch:ch+2] = struct.pack("<H", flags | 0x0020)   # IMAGE_FILE_LARGE_ADDRESS_AWARE
        print("  exe: LARGEADDRESSAWARE 켬")
    open(out, "wb").write(b)


def main():
    ap = argparse.ArgumentParser(prog="ujyu scale",
        description="해상도 N배 스케일 (config 주도)")
    ap.add_argument("mode", choices=["common", "center", "dims", "cg", "cg-export",
                                     "cg-check", "exe"])
    ap.add_argument("paths", nargs="+",
                    help="대상 (common/dims: 아카이브들, cg/cg-export/exe: 원본 파일, "
                         "cg-check: <작업폴더> <결과폴더>)")
    ap.add_argument("--out", default=None,
                    help="cg/exe 출력 경로 (기본: 제자리) · cg-export 는 출력 폴더(필수)")
    ap.add_argument("--from-dir", default=None,
                    help="cg: 외부 업스케일 결과 폴더 (같은 파일명, 치수=원본×N)")
    ap.add_argument("--prefer", default=None,
                    help="cg-export: 번역본 아카이브. 원본과 다른 엔트리는 translated/ 로 분리")
    ap.add_argument("--scale", type=int, default=None, help="배율 (기본: config SCALE)")
    a = ap.parse_args()
    N = a.scale if a.scale is not None else getattr(C, "SCALE", 1)
    if N == 1:
        print("SCALE=1 — 할 일 없음"); return
    if a.mode == "common":
        cmd_common(a.paths, N)
    elif a.mode == "center":
        cmd_center(a.paths, N)
    elif a.mode == "dims":
        cmd_dims(a.paths, N)
        cmd_dims_auto(a.paths, N)
    elif a.mode == "cg":
        cmd_cg(a.paths[0], a.out or a.paths[0], N, a.from_dir)
    elif a.mode == "cg-export":
        if not a.out:
            raise SystemExit("cg-export 는 --out <폴더> 가 필요합니다")
        cmd_cg_export(a.paths[0], a.out, N, a.prefer)
    elif a.mode == "cg-check":
        if len(a.paths) < 2:
            raise SystemExit("사용: scale.py cg-check <작업폴더(_manifest.tsv 있는 곳)> <결과폴더>")
        cmd_cg_check(a.paths[0], a.paths[1], N)
    elif a.mode == "exe":
        cmd_exe(a.paths[0], a.out or a.paths[0], N)


if __name__ == "__main__":
    main()
