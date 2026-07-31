#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""배포 패치 패키지 생성 - 원본과 배포본의 차이만 담아 사용자 배포본을 만든다.

게임 데이터는 재배포할 수 없으므로 `config.ORIG_DIR`(무패치 원본)과
`config.GAME_DIR`(패치된 배포본)을 재귀 대조해 **바뀐/추가된 파일만** 담고,
사용자가 자기 설치본에 적용할 수 있는 단독 인스톨러를 함께 넣는다.

  ujyu release                     <WORK_DIR>/release 에 패키지 + zip
  ujyu release --dry-run           무엇이 들어갈지만 보고 (아무것도 쓰지 않는다)
  ujyu release --name kannagi-kr   패키지 이름 지정
  ujyu release --no-zip            폴더만 만들고 zip 은 묶지 않는다

산출 구조 (<out>/<name>/):
  install.py      표준 라이브러리만 쓰는 단독 인스톨러 (사용자 PC 에 ujyu 가 없다)
  manifest.json   항목별 원본/결과 sha256·크기 + 삭제된 파일 목록
  README.txt      설치 방법·주의
  files/...       바뀐/추가된 파일의 실제 내용 (배포본 기준 상대경로 유지)

원본·배포본은 **읽기만** 한다. 쓰는 곳은 --out 아래뿐이다.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile

try:                                   # 콘솔이 CP949 여도 출력이 죽지 않게
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if __package__ in (None, ""):          # 직접 실행(python ujyu/release.py) 대비
    sys.path.insert(0, os.path.dirname(HERE))

BUFSIZE = 1 << 20                      # 큰 파일도 스트리밍으로 읽는다
BIG = 64 << 20                         # 이만큼 넘는 파일은 해시 진행을 알린다
STORE_OVER = 16 << 20                  # 이만큼 넘는 페이로드는 zip 무압축(이미 압축된 아카이브)
MANIFEST_FORMAT = "ujyu-release/1"


# ─────────────────────────────────────────── 유틸
def _hsize(n):
    """사람이 읽는 크기. CP949 밖 문자를 쓰지 않는다."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def _sha256(path, note=None):
    """파일 sha256 (스트리밍)."""
    if note and os.path.getsize(path) > BIG:
        print("    해시 계산 %s (%s)" % (note, _hsize(os.path.getsize(path))))
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(BUFSIZE)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _excluded(rel, patterns):
    """상대경로를 제외 패턴과 맞춘다. 전체경로·파일명·폴더 접두 모두 인정."""
    base = rel.rsplit("/", 1)[-1]
    for p in patterns:
        p = p.replace("\\", "/")
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(base, p):
            return True
        if rel.startswith(p.rstrip("/") + "/"):        # 폴더 통째로 (예: save)
            return True
    return False


def _scan(root, exclude=()):
    """폴더를 재귀 훑어 ({상대경로(/구분): 크기}, 제외된 수)."""
    out, skipped = {}, 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = os.path.join(dirpath, fn)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root).replace("\\", "/")
            if exclude and _excluded(rel, exclude):
                skipped += 1
                continue
            try:
                out[rel] = os.path.getsize(full)
            except OSError:
                pass
    return out, skipped


# ─────────────────────────────────────────── 대조
def diff_dirs(orig_dir, game_dir, exclude=()):
    """원본/배포본을 대조해 (변경, 추가, 삭제, 동일수, 제외수) 를 낸다.

    변경 = 양쪽에 있고 내용이 다름, 추가 = 배포본에만, 삭제 = 원본에만.
    크기가 다르면 즉시 변경, 같으면 sha256 으로 판정한다.
    """
    o, n_o = _scan(orig_dir, exclude)
    g, n_g = _scan(game_dir, exclude)
    changed, added, removed, same = [], [], [], 0

    for rel in sorted(g):
        gsize = g[rel]
        gpath = os.path.join(game_dir, *rel.split("/"))
        if rel not in o:
            added.append({"path": rel, "action": "add", "size": gsize,
                          "sha256": _sha256(gpath, rel),
                          "orig_size": None, "orig_sha256": None})
            continue
        osize = o[rel]
        opath = os.path.join(orig_dir, *rel.split("/"))
        if osize == gsize:
            oh = _sha256(opath, rel + " (원본)")
            gh = _sha256(gpath, rel + " (배포본)")
            if oh == gh:
                same += 1
                continue
        else:
            oh, gh = _sha256(opath, rel + " (원본)"), _sha256(gpath, rel + " (배포본)")
        changed.append({"path": rel, "action": "modify", "size": gsize, "sha256": gh,
                        "orig_size": osize, "orig_sha256": oh})

    for rel in sorted(set(o) - set(g)):
        opath = os.path.join(orig_dir, *rel.split("/"))
        removed.append({"path": rel, "orig_size": o[rel],
                        "orig_sha256": _sha256(opath, rel + " (원본)")})

    return changed, added, removed, same, n_o + n_g


# ─────────────────────────────────────────── 인스톨러 (사용자 PC 용 단독 스크립트)
_INSTALLER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""한글 패치 인스톨러. 파이썬 표준 라이브러리만 사용한다.

  python install.py <게임폴더>              설치 (검증 -> .orig 백업 -> 교체)
  python install.py <게임폴더> --dry-run    검사만. 아무것도 쓰지 않는다
  python install.py <게임폴더> --force      해시 불일치를 무시하고 강행
  python install.py <게임폴더> --uninstall  .orig 백업에서 되돌리기

이 스크립트는 manifest.json / files 폴더와 같은 자리에 있어야 한다.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")
PAYLOAD = os.path.join(HERE, "files")
BUFSIZE = 1 << 20

# 대상 파일의 현재 상태
ST_ORIG = "원본"          # 손대지 않은 원본 = 적용 가능
ST_DONE = "이미적용"      # 이미 이 패치가 적용됨 = 건너뜀
ST_DIFF = "불일치"        # 원본도 패치본도 아님 = 다른 버전이거나 다른 패치
ST_NONE = "없음"          # 파일이 아예 없음


def hsize(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%d B" % n if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(BUFSIZE)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_manifest():
    """manifest.json 을 읽는다. 없으면 {} (--help 는 그래도 되게)."""
    if not os.path.isfile(MANIFEST):
        return {}
    with open(MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)


def tgt_path(game, rel):
    return os.path.join(game, *rel.split("/"))


def src_path(rel):
    return os.path.join(PAYLOAD, *rel.split("/"))


def inspect(game, files):
    """각 항목의 현재 상태를 조사해 [(entry, 상태)] 로 낸다."""
    rows = []
    for e in files:
        tgt = tgt_path(game, e["path"])
        if not os.path.exists(tgt):
            rows.append((e, ST_ORIG if e["action"] == "add" else ST_NONE))
            continue
        h = sha256(tgt)
        if h == e["sha256"]:
            rows.append((e, ST_DONE))
        elif e["action"] == "modify" and h == e["orig_sha256"]:
            rows.append((e, ST_ORIG))
        else:
            rows.append((e, ST_DIFF))
    return rows


def report(rows, removed):
    n = {}
    for _e, st in rows:
        n[st] = n.get(st, 0) + 1
    print("검사 결과: 적용가능 %d / 이미적용 %d / 불일치 %d / 없음 %d"
          % (n.get(ST_ORIG, 0), n.get(ST_DONE, 0), n.get(ST_DIFF, 0), n.get(ST_NONE, 0)))
    for e, st in rows:
        if st == ST_DIFF:
            print("  [불일치] %s" % e["path"])
        elif st == ST_NONE:
            print("  [없음]   %s" % e["path"])
    if n.get(ST_DIFF):
        print("\n불일치: 그 파일이 원본과도, 이 패치의 결과와도 다릅니다.")
        print("  - 다른 한글 패치나 수정 파일이 이미 적용된 상태일 수 있습니다.")
        print("  - 게임 버전(재판/염가판 등)이 이 패치가 만들어진 원본과 다를 수 있습니다.")
        print("  원본을 다시 설치한 폴더에 적용하는 것을 권합니다.")
    if n.get(ST_NONE):
        print("\n없음: 게임 폴더에 그 파일이 없습니다. 폴더 경로가 맞는지 확인하세요.")
    if removed:
        print("\n참고: 원본에는 있으나 패치본에는 없는 파일이 %d개 있습니다."
              % len(removed))
        print("      인스톨러는 이 파일들을 지우지 않습니다. 그대로 두어도 됩니다.")
        for r in removed[:20]:
            print("      - %s" % r["path"])
        if len(removed) > 20:
            print("      ... 외 %d개" % (len(removed) - 20))
    return n


def check_payload(files):
    missing = [e["path"] for e in files if not os.path.isfile(src_path(e["path"]))]
    if missing:
        raise SystemExit("패치 파일이 빠졌습니다(files 폴더 확인, %d개):\n  %s"
                         % (len(missing), "\n  ".join(missing[:10])))


def do_install(game, rows, force):
    done = skipped = backed = 0
    for e, st in rows:
        rel = e["path"]
        tgt, src = tgt_path(game, rel), src_path(rel)
        if st == ST_DONE:
            skipped += 1
            continue
        if st in (ST_DIFF, ST_NONE) and not force:
            skipped += 1
            continue
        parent = os.path.dirname(tgt)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        if os.path.exists(tgt):                    # 원본 백업 (있으면 덮지 않는다)
            bak = tgt + ".orig"
            if os.path.exists(bak):
                print("  백업 유지 %s.orig (이미 있음)" % rel)
            else:
                shutil.copy2(tgt, bak)
                backed += 1
        shutil.copy2(src, tgt)
        done += 1
        print("  적용 %s (%s)" % (rel, hsize(e["size"])))
    print("\n설치 완료: 적용 %d / 백업 %d / 건너뜀 %d" % (done, backed, skipped))
    if done:
        print("원본은 <파일>.orig 로 남아 있습니다. 되돌리려면:")
        print("  python install.py \"%s\" --uninstall" % game)
    return done


def do_uninstall(game, files):
    back = gone = kept = 0
    for e in files:
        rel = e["path"]
        tgt = tgt_path(game, rel)
        bak = tgt + ".orig"
        if os.path.exists(bak):
            shutil.copy2(bak, tgt)
            back += 1
            print("  복원 %s" % rel)
            if e["action"] == "modify" and sha256(tgt) != e["orig_sha256"]:
                print("    경고: 백업이 원본과 다릅니다(--force 로 설치했거나 파일을 "
                      "직접 고친 경우). 백업 %s.orig 를 지우지 않고 남겨 둡니다." % rel)
            else:
                os.remove(bak)
            continue
        if e["action"] == "add" and os.path.exists(tgt):
            if sha256(tgt) == e["sha256"]:         # 패치가 넣은 파일 = 지운다
                os.remove(tgt)
                gone += 1
                print("  삭제 %s (패치가 추가한 파일)" % rel)
            else:
                kept += 1
                print("  보존 %s (패치본과 달라 손대지 않음)" % rel)
        elif e["action"] == "modify":
            kept += 1
            print("  보존 %s (백업 .orig 이 없어 되돌릴 수 없음)" % rel)
    print("\n되돌리기 완료: 복원 %d / 삭제 %d / 보존 %d" % (back, gone, kept))
    return back + gone


def main():
    man = load_manifest()
    ap = argparse.ArgumentParser(
        prog="install.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="%s 를 게임 폴더에 적용한다." % man.get("name", "패치"),
        epilog="예)\n"
               "  python install.py \"C:\\Games\\game\"\n"
               "  python install.py \"C:\\Games\\game\" --dry-run\n"
               "  python install.py \"C:\\Games\\game\" --uninstall")
    ap.add_argument("game_dir", help="게임이 설치된 폴더 (원본 그대로인 폴더)")
    ap.add_argument("--dry-run", action="store_true",
                    help="검사만 하고 아무것도 쓰지 않는다")
    ap.add_argument("--force", action="store_true",
                    help="해시 불일치를 무시하고 강행한다 (권장하지 않음)")
    ap.add_argument("--uninstall", action="store_true",
                    help="<파일>.orig 백업에서 원본으로 되돌린다")
    a = ap.parse_args()

    if not man:
        raise SystemExit("manifest.json 을 찾을 수 없습니다: %s\n"
                         "압축을 푼 패치 폴더 안에서 실행하세요." % MANIFEST)
    game = os.path.abspath(a.game_dir)
    if not os.path.isdir(game):
        raise SystemExit("게임 폴더가 없습니다: %s" % game)
    files = man["files"]
    removed = man.get("removed", [])

    print("%s" % man.get("name", "패치"))
    if man.get("title"):
        print("대상: %s" % man["title"])
    print("게임 폴더: %s" % game)
    print("항목 %d개 (%s)\n" % (len(files), hsize(man.get("total_bytes", 0))))

    if a.uninstall:
        n = do_uninstall(game, files)
        return 0 if n else 1

    check_payload(files)
    print("원본 확인 중...")
    rows = inspect(game, files)
    n = report(rows, removed)

    if a.dry_run:
        print("\n--dry-run: 아무것도 쓰지 않았습니다.")
        return 0
    bad = n.get(ST_DIFF, 0) + n.get(ST_NONE, 0)
    if bad and not a.force:
        raise SystemExit("\n중단했습니다. 파일 %d개가 예상과 달라 아무것도 쓰지 "
                         "않았습니다.\n검사만 하려면 --dry-run, 그래도 적용하려면 "
                         "--force 를 쓰세요." % bad)
    if bad:
        print("\n--force: 불일치 %d개를 덮어씁니다." % bad)
    if not n.get(ST_ORIG, 0) and not a.force:
        print("\n적용할 것이 없습니다 (이미 패치된 폴더로 보입니다).")
        return 0
    print("\n적용합니다...")
    do_install(game, rows, a.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


# ─────────────────────────────────────────── README
def build_readme(C, name, title, n_items, total, removed):
    """config 에서 알 수 있는 것만 적는다. 모르는 주의사항은 쓰지 않는다."""
    L = []
    L.append("%s 한글 패치" % (title or name))
    L.append("=" * 40)
    L.append("")
    L.append("이 패치는 원본 게임 파일 중 바뀐 파일만 담고 있습니다.")
    L.append("게임 본체는 포함되어 있지 않으므로 정품 설치본이 필요합니다.")
    L.append("")
    L.append("설치 방법")
    L.append("---------")
    L.append("1. 게임을 원본 그대로 설치합니다(이미 다른 패치를 적용했다면 재설치 권장).")
    L.append("2. 파이썬 3 이 필요합니다 (https://www.python.org).")
    L.append("3. 명령 프롬프트에서:")
    L.append("")
    L.append("     python install.py \"C:\\게임\\설치폴더\"")
    L.append("")
    L.append("   먼저 확인만 하려면 --dry-run 을 붙입니다.")
    L.append("4. 원본 파일은 <파일이름>.orig 로 백업됩니다.")
    L.append("   되돌리려면:  python install.py \"C:\\게임\\설치폴더\" --uninstall")
    L.append("")
    L.append("패치 내용")
    L.append("---------")
    L.append("교체/추가 파일 %d개, 총 %s" % (n_items, _hsize(total)))
    scale = getattr(C, "SCALE", 1) or 1
    ow, oh = getattr(C, "ORIG_W", None), getattr(C, "ORIG_H", None)
    if scale > 1 and ow and oh:
        L.append("화면 해상도를 %d배(%dx%d -> %dx%d)로 확대합니다."
                 % (scale, ow, oh, ow * scale, oh * scale))
    if getattr(C, "FONT_FACE", None):
        L.append("한글 글꼴 '%s' 이 게임에 내장됩니다." % C.FONT_FACE)
    if getattr(C, "MOVIE_NATIVE", False):
        L.append("동영상을 화면 해상도에 맞춰 다시 인코딩했습니다.")
    if getattr(C, "CG_TRANS_DIR", None):
        L.append("이미지 안의 글자도 번역본으로 교체됩니다.")
    if removed:
        L.append("")
        L.append("참고: 원본에만 있는 파일 %d개는 패치가 지우지 않습니다"
                 " (그대로 두어도 됩니다)." % len(removed))
    L.append("")
    L.append("주의")
    L.append("----")
    L.append("- 세이브 데이터는 원본과 호환되지 않을 수 있습니다. 미리 백업하세요.")
    L.append("- 패치를 적용한 폴더에 다시 적용하면 이미 적용된 파일은 건너뜁니다.")
    # config 로는 알 수 없는 타이틀별 주의사항(호환 모드 등)은 여기서 받는다.
    for line in (getattr(C, "RELEASE_NOTES", None) or []):
        L.append("- %s" % line)
    L.append("")
    return "\n".join(L)


# ─────────────────────────────────────────── 패키징
def _copy_payload(items, game_dir, files_dir):
    total = 0
    for e in items:
        rel = e["path"]
        src = os.path.join(game_dir, *rel.split("/"))
        dst = os.path.join(files_dir, *rel.split("/"))
        parent = os.path.dirname(dst)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        shutil.copy2(src, dst)
        total += e["size"]
        print("  담음 %-40s %s" % (rel, _hsize(e["size"])))
    return total


def _make_zip(stage, out_dir, name):
    zpath = os.path.join(out_dir, name + ".zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _dirs, files in os.walk(stage):
            for fn in sorted(files):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, stage).replace("\\", "/")
                arc = "%s/%s" % (name, rel)
                big = os.path.getsize(full) > STORE_OVER
                z.write(full, arc,
                        compress_type=zipfile.ZIP_STORED if big else zipfile.ZIP_DEFLATED)
    return zpath


def _print_list(label, items, key="size"):
    if not items:
        return 0
    tot = sum(i[key] or 0 for i in items)
    print("%s %d개 (%s)" % (label, len(items), _hsize(tot)))
    for e in items:
        print("    %-44s %s" % (e["path"], _hsize(e[key] or 0)))
    return tot


# ─────────────────────────────────────────── main
def main():
    ap = argparse.ArgumentParser(
        prog="ujyu release",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="배포용 패치 패키지 생성 - 원본 대비 바뀐 파일 + 단독 인스톨러.",
        epilog="예)\n"
               "  ujyu release --dry-run           무엇이 들어갈지 규모만 확인\n"
               "  ujyu release --name kannagi-kr   이름 지정 + zip 생성\n"
               "  ujyu release --no-zip            폴더만 만든다\n"
               "\n원본·배포본은 읽기만 하고, 쓰는 곳은 --out 아래뿐이다.")
    ap.add_argument("--orig", help="무패치 원본 폴더 (기본: config.ORIG_DIR)")
    ap.add_argument("--game", help="패치된 배포 폴더 (기본: config.GAME_DIR)")
    ap.add_argument("-o", "--out", help="패키지를 만들 폴더 (기본: <WORK_DIR>/release)")
    ap.add_argument("--name", help="패키지 이름 = 폴더/zip 이름 (기본: patch)")
    ap.add_argument("--zip", dest="zip", action="store_true", default=True,
                    help="전체를 zip 하나로 묶는다 (기본)")
    ap.add_argument("--no-zip", dest="zip", action="store_false",
                    help="zip 을 만들지 않고 폴더만 남긴다")
    ap.add_argument("--dry-run", action="store_true",
                    help="무엇이 들어갈지만 보고한다. 아무것도 쓰지 않는다")
    ap.add_argument("--exclude", action="append", metavar="GLOB", default=[],
                    help="대조에서 뺄 경로 패턴(여러 번 지정 가능). config.RELEASE_EXCLUDE "
                         "에 더해진다. 파일명·전체경로·폴더 이름에 맞춘다. "
                         "예: --exclude \"*.bak\" --exclude save")
    ap.add_argument("--no-config-exclude", action="store_true",
                    help="config.RELEASE_EXCLUDE 를 무시하고 --exclude 만 쓴다")
    a = ap.parse_args()

    from ujyu.titleconfig import config as C

    # 기본 제외는 config 가 정본이다 — 세이브(사용자 데이터)와 작업 스냅샷이
    # 패치에 섞여 들어가면 사용자 데이터를 덮어쓴다.
    cfg_excl = [] if a.no_config_exclude else list(getattr(C, "RELEASE_EXCLUDE", None) or [])
    a.exclude = cfg_excl + list(a.exclude)

    orig_dir = os.path.abspath(a.orig or C.ORIG_DIR)
    game_dir = os.path.abspath(a.game or C.GAME_DIR)
    name = a.name or "patch"
    out_dir = os.path.abspath(a.out or os.path.join(getattr(C, "WORK_DIR", "."), "release"))

    if not os.path.isdir(orig_dir):
        raise SystemExit("원본 폴더가 없다: %s  (config.ORIG_DIR 또는 --orig)" % orig_dir)
    if not os.path.isdir(game_dir):
        raise SystemExit("배포 폴더가 없다: %s  (config.GAME_DIR 또는 --game)" % game_dir)
    if orig_dir == game_dir:
        raise SystemExit("원본과 배포 폴더가 같다: %s\n"
                         "무패치 원본과 패치본을 분리하라 (config.ORIG_DIR / GAME_DIR)."
                         % orig_dir)
    for d in (orig_dir, game_dir):                  # 원본·배포본 안에 쓰지 않는다
        try:
            inside = os.path.commonpath([os.path.normcase(out_dir),
                                         os.path.normcase(d)]) == os.path.normcase(d)
        except ValueError:                          # 드라이브가 다르면 무관
            inside = False
        if inside:
            raise SystemExit("--out 이 %s 안이다. 원본·배포본 밖으로 지정하라." % d)

    title = (getattr(C, "COMMON_CSV", {}) or {}).get("title", "").strip() or None

    print("원본 : %s" % orig_dir)
    print("배포본: %s" % game_dir)
    if a.exclude:
        print("제외 : %s%s" % (" ".join(a.exclude),
                              "  (config.RELEASE_EXCLUDE 포함)" if cfg_excl else ""))
    else:
        print("경고: 제외 패턴이 없다. 세이브·작업 백업이 패치에 섞일 수 있으니"
              " config.RELEASE_EXCLUDE 를 채워라.")
    print("대조 중 (크기·sha256)...")
    changed, added, removed, same, n_excl = diff_dirs(orig_dir, game_dir, a.exclude)

    items = changed + added
    total = sum(e["size"] for e in items)
    print("")
    _print_list("변경", changed)
    _print_list("추가", added)
    _print_list("삭제(패키지에 담지 않음)", removed, key="orig_size")
    print("동일 %d개" % same)
    if n_excl:
        print("제외 %d개 (--exclude)" % n_excl)
    print("")
    print("패키지에 담을 항목 %d개, 총 %s" % (len(items), _hsize(total)))
    if removed:
        print("경고: 원본에만 있는 파일 %d개는 manifest 에만 기록하고 인스톨러가 "
              "지우지 않는다." % len(removed))
    if not items:
        raise SystemExit("담을 것이 없다. 배포본이 원본과 같다 - 먼저 ujyu build 를 하라.")

    if a.dry_run:
        print("")
        print("--dry-run: 아무것도 쓰지 않았다. (출력 예정 위치: %s)"
              % os.path.join(out_dir, name))
        return 0

    stage = os.path.join(out_dir, name)
    if os.path.isdir(stage):
        print("기존 %s 를 지우고 다시 만든다." % stage)
        shutil.rmtree(stage)
    files_dir = os.path.join(stage, "files")
    os.makedirs(files_dir)

    print("")
    print("파일 복사 -> %s" % files_dir)
    _copy_payload(items, game_dir, files_dir)

    man = {
        "format": MANIFEST_FORMAT,
        "name": name,
        "title": title,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "orig_dir": os.path.basename(orig_dir),
        "game_dir": os.path.basename(game_dir),
        "scale": getattr(C, "SCALE", 1),
        "exclude": list(a.exclude),
        "counts": {"modify": len(changed), "add": len(added),
                   "remove": len(removed), "same": same, "excluded": n_excl},
        "total_bytes": total,
        "files": items,
        "removed": removed,
    }
    mpath = os.path.join(stage, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)

    ipath = os.path.join(stage, "install.py")
    with open(ipath, "w", encoding="utf-8", newline="\n") as f:
        f.write(_INSTALLER)

    rpath = os.path.join(stage, "README.txt")
    with open(rpath, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(build_readme(C, name, title, len(items), total, removed))

    print("")
    print("manifest.json / install.py / README.txt 생성")
    print("패키지 폴더: %s" % stage)
    print("  항목 %d개, 총 %s" % (len(items), _hsize(total)))

    if a.zip:
        print("zip 생성 중 (%s 초과 파일은 무압축)..." % _hsize(STORE_OVER))
        zpath = _make_zip(stage, out_dir, name)
        zsize = os.path.getsize(zpath)
        print("zip: %s" % zpath)
        print("  크기 %s (원본 합계 %s)" % (_hsize(zsize), _hsize(total)))
    else:
        print("(--no-zip: zip 은 만들지 않았다)")
    print("")
    print("사용자 설치: python install.py <게임폴더>   (먼저 --dry-run 권장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
