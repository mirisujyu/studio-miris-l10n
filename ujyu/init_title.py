#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""타이틀 리포 스캐폴딩 - 새 타이틀의 작업 폴더와 템플릿을 만든다.

docs/BOOTSTRAP.md 0단계(리포 셋업)를 대신한다. 폴더를 만들고 엔진의 `samples/` 템플릿을
제자리에 복사할 뿐이다. **git 명령은 실행하지 않고 안내만 한다** - 원격·서브모듈 주소는
사람이 정할 일이다.

기존 파일은 절대 덮어쓰지 않는다(`--force` 를 줘야 덮어쓴다).

사용:
  ujyu init <dir> [--title 이름] [--force]
"""
import argparse
import os
import shutil

# 엔진 리포 루트 = 이 파일(ujyu/init_title.py)의 두 단계 위
ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(ENGINE_DIR, "samples")

# 만들 폴더 (BOOTSTRAP.md 0단계의 mkdir -p)
DIRS = [
    ("translation", "번역 데이터·가이드 문서 (strings.json / IMAGES.md / GLOSSARY.md …)"),
    ("fonts",       "폰트 빌드 스펙과 소스 폰트"),
    ("docs",        "타이틀 고유 문서 (진행 현황·분석 기록)"),
    ("tools",       "타이틀 전용 일회성 스크립트 (범용은 엔진에 넣는다)"),
]

# 복사할 템플릿: (samples 안 이름, 타이틀 리포 안 상대경로, 설명)
FILES = [
    ("config.py",              "config.py",
     "타이틀 설정 정본 - 경로·아카이브·exe 오프셋"),
    ("font_spec.py",           os.path.join("fonts", "Sample.py"),
     "폰트 빌드 스펙 (ujyu font 입력)"),
    ("images.sample.md",       os.path.join("translation", "IMAGES.md"),
     "이미지 텍스트 manifest (ujyu image 입력)"),
    ("ui_strings.sample.json", os.path.join("translation", "ui_strings.json"),
     "Windows UI 문자열 번역 (ujyu exe ui 입력)"),
]


def _tag_title(text, name, ext):
    """복사한 템플릿 머리에 타이틀 이름을 주석으로 남긴다. 주석이 없는 포맷은 그대로."""
    if ext == ".py":
        lines = text.split("\n")
        i = 0                                   # 셔뱅·coding 선언 뒤에 끼운다
        while i < len(lines) and i < 2 and lines[i].startswith("#"):
            i += 1
        lines.insert(i, "# 타이틀: %s" % name)
        return "\n".join(lines)
    if ext == ".md":
        return "<!-- 타이틀: %s -->\n%s" % (name, text)
    return text                                 # .json 등 주석 불가


def _copy(src, dst, title, force):
    """템플릿 하나 복사. 반환: 'copy' | 'skip'."""
    if os.path.exists(dst) and not force:
        return "skip"
    ext = os.path.splitext(src)[1].lower()
    if title and ext in (".py", ".md"):
        with open(src, encoding="utf-8") as f:
            text = f.read()
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(_tag_title(text, title, ext))
    else:
        shutil.copyfile(src, dst)
    return "copy"


def scaffold(root, title=None, force=False):
    """폴더·템플릿을 만든다. 반환: (만든 것 수, 건너뛴 것 수)."""
    root = os.path.abspath(root)
    if os.path.normcase(root) == os.path.normcase(ENGINE_DIR):
        raise SystemExit("엔진 리포 자신에는 스캐폴딩할 수 없습니다: %s" % root)
    if not os.path.isdir(SAMPLES_DIR):
        raise SystemExit("엔진의 samples 폴더를 찾을 수 없습니다: %s" % SAMPLES_DIR)

    made = skipped = 0
    print("타이틀 리포: %s" % root)
    if title:
        print("타이틀 이름: %s" % title)
    print()

    if os.path.isdir(root):
        print("이미 있음: .  (기존 파일은 건드리지 않습니다)")
    else:
        os.makedirs(root)
        print("생성: .")
        made += 1
    for name, desc in DIRS:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            print("이미 있음: %-13s %s" % (name + "/", desc))
        else:
            os.makedirs(path)
            print("생성: %-17s %s" % (name + "/", desc))
            made += 1

    print()
    for sample, rel, desc in FILES:
        src = os.path.join(SAMPLES_DIR, sample)
        dst = os.path.join(root, rel)
        rel_show = rel.replace(os.sep, "/")
        if not os.path.isfile(src):
            print("건너뜀: %-17s 엔진에 템플릿이 없습니다 (%s)" % (rel_show, sample))
            skipped += 1
            continue
        if _copy(src, dst, title, force) == "skip":
            print("건너뜀: %-17s 이미 있음 (덮어쓰려면 --force)" % rel_show)
            skipped += 1
        else:
            print("복사: %-19s %s" % (rel_show, desc))
            made += 1

    print()
    print("만든 것 %d개, 건너뛴 것 %d개." % (made, skipped))
    return made, skipped


def print_next_steps(root):
    """다음에 할 일. git 은 대신 실행하지 않는다 - 순서만 안내한다."""
    root = os.path.abspath(root)
    print()
    print("다음에 할 일 (%s 에서 순서대로):" % root)
    print("  1. git init && git add . && git commit -m \"타이틀 리포 초기화\"")
    print("  2. git submodule add <엔진 리포 주소> engine")
    print("  3. pip install -e engine            # ujyu 명령 설치")
    print("  4. config.py 의 ORIG_DIR(무패치 원본, 읽기 전용)·GAME_DIR(배포본)을 채운다")
    print("  5. ujyu inspect                      # config 채움 상태와 다음 단계 확인")
    print("  6. ujyu axr list <ORIG_DIR>/scenario.axr    # 정찰 시작")
    print()
    print("이후 모든 ujyu 명령은 이 폴더(루트 config.py 가 있는 곳)에서 실행한다.")
    print("다른 위치에서 돌릴 땐 MIRIS_CONFIG_DIR=<이 폴더>.")
    print("전체 순서는 engine/docs/BOOTSTRAP.md, 작업 원칙은 engine/GUIDE.md.")


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu init",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="새 타이틀 리포 스캐폴딩 (폴더 + samples 템플릿 복사)",
        epilog="예:\n"
               "  ujyu init ../mytitle --title \"타이틀 이름\"\n"
               "  ujyu init . --title \"타이틀 이름\"      # 이미 만든 폴더에 채워 넣기\n"
               "기존 파일은 덮어쓰지 않는다(--force 로만 덮어쓴다). git 은 안내만 하고 "
               "실행하지 않는다.")
    ap.add_argument("dir", help="만들 타이틀 리포 폴더 (있으면 빈 폴더가 아니어도 된다)")
    ap.add_argument("--title", help="복사한 템플릿 주석에 남길 타이틀 이름")
    ap.add_argument("--force", action="store_true",
                    help="이미 있는 파일도 덮어쓴다 (기본: 건너뜀)")
    a = ap.parse_args()
    scaffold(a.dir, a.title, a.force)
    print_next_steps(a.dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
