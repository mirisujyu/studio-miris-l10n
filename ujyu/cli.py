"""ujyu 통합 CLI - 대상(포맷/exe/번역/빌드)별로 묶은 명령을 각 도구 모듈로 넘긴다.

    ujyu <명령> [하위] [인자...]

각 도구는 자기 argparse 를 그대로 쓴다. CLI 는 명령(+하위)을 떼어 해당 모듈의
main() 으로 디스패치할 뿐이다. 인자·도움말 규칙은 docs/CLI_STYLE.md 참조.
"""
import sys
import importlib

# 콘솔이 CP949 라 도움말에 섞인 em 대시 등이 그대로 죽는다. 인코딩은 그대로 두고
# 표현 못 하는 문자만 치환한다 (도움말 한 줄 때문에 명령이 실패하면 안 된다).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

# ─────────────────────────────────────────── 목록(도움말 표시용)
# (카테고리, [(명령, 한 줄 설명)])
_CATEGORIES = [
    ("설정 관리기", [
        ("inspect", "config 채움 상태 진단 + 다음에 할 일"),
        ("config", "설정 출력·변경: show / get / set(값 유효성 검사)"),
    ]),
    ("전체 프로세스", [
        ("init", "타이틀 리포 스캐폴딩 + 기본 config.py 작성"),
        ("unpack", "한글화 대상 전부 언팩 + 디스어셈블"),
        ("extract", "텍스트·이미지·exe 문자열·화자명 등 전부 추출"),
        ("build", "배포 조립: exe / scenario / title / cg / movie 단계 선택"),
        ("release", "원본 대비 diff 로 인스톨러 패키지 생성"),
    ]),
    ("포맷 처리기", [
        ("axr", "AXRe 아카이브: list / unpack / repack"),
        ("scn", "VNEG(.scn) 스크립트: extract / disasm / relocate"),
        ("dmj", "DMJ0 무비: info / frames / video / export / mjpeg / encode"),
        ("adp", "ADPx 오디오: info / decode"),
        ("csv", "아카이브 안 common.csv: show / get / todo"),
    ]),
    ("유틸리티", [
        ("exe", "exe 패치·분석: (전체) / ui / movie / fontrestore / scan / disasm"),
        ("filter", "번역 대상 선별 + 덤프/반영"),
        ("inject", "CP949 주입·검증 + 시나리오 아카이브 빌드"),
        ("image", "무문자 이미지에 번역 텍스트 렌더 주입(manifest 주도)"),
        ("font", "게임용 한글 폰트 빌드(스펙 주도)"),
        ("jpmap", "미번역 일본어용 CP949 사용자정의영역 문자 매핑"),
        ("scale", "해상도 N배: common / center / dims / cg / exe"),
        ("title", "common.csv 창 제목 설정"),
        ("nameplates", "화자명 대응표 생성"),
        ("save", "테스트용 세이브: show / goto <씬>"),
        ("migrate", "strings.json v1 -> v2 이관(일회성)"),
    ]),
]

# ─────────────────────────────────────────── 디스패치 표
# 단일 명령: 명령 → 모듈
_SIMPLE = {
    "axr":        "ujyu.formats.axr",
    "adp":        "ujyu.formats.adp",
    "csv":        "ujyu.csv",
    "filter":     "ujyu.filter_text",
    "inject":     "ujyu.inject_text",
    "image":      "ujyu.inject_image_text",
    "font":       "ujyu.build_font",
    "jpmap":      "ujyu.jpmap",
    "nameplates": "ujyu.gen_nameplates",
    "migrate":    "ujyu.migrate_strings",
    "build":      "ujyu.build_patch",
    "scale":      "ujyu.scale",
    "title":      "ujyu.patch_title",
    "init":       "ujyu.init_title",
    "save":       "ujyu.save",
    "inspect":    "ujyu.doctor",           # 옛 이름 doctor 는 별칭으로 남긴다
    "config":     "ujyu.config_cmd",
    "unpack":     "ujyu.unpack_all",
    "extract":    "ujyu.extract_all",      # 포맷 단위 추출은 `ujyu scn extract`
    "release":    "ujyu.release",
}

# 그룹 명령: 명령 → {하위: (모듈, 모듈에 넘길 첫 인자)}
#   None 키 = 하위 없이 불렀을 때의 기본 동작(없으면 그룹 도움말)
_GROUP = {
    "scn": {                                   # scn.py 가 자체 서브커맨드를 파싱한다
        "extract":  ("ujyu.scn", "extract"),
        "disasm":   ("ujyu.scn", "disasm"),
        "relocate": ("ujyu.scn", "relocate"),
    },
    "exe": {
        None:          ("ujyu.patch_exe", None),          # 전체 패치
        "ui":          ("ujyu.patch_ui", None),
        "movie":       ("ujyu.patch_movie", None),
        "fontrestore": ("ujyu.patch_fontrestore", None),
        "scan":        ("ujyu.exe_scan", None),           # 오프셋 후보 스캔
        "disasm":      ("ujyu.disasm", None),             # x86 디스어셈블 분석
    },
    "dmj": {
        None:     ("ujyu.formats.dmj", None),             # info 가 기본
        "encode": ("ujyu.dmj_encode", None),
    },
}

# 옛 이름 → 새 경로 (하위호환. 도움말 목록엔 넣지 않는다)
_ALIAS = {
    "doctor":     ["inspect"],             # 옛 이름
    "disasm":     ["scn", "disasm"],
    "relocate":   ["scn", "relocate"],
    "disasm-x86": ["exe", "disasm"],
    "ui":         ["exe", "ui"],
    "movie":      ["exe", "movie"],
    "fontrestore": ["exe", "fontrestore"],
    "dmj-encode": ["dmj", "encode"],
    "dims":       ["_dims"],                   # 아래 _LEGACY 로 처리
}
_LEGACY = {"_dims": "ujyu.scn_dims"}           # 단독 실행만 남긴 옛 도구

_HELP = ("-h", "--help", "help")


def _usage():
    print("ujyu - Studio Miris 엔진 한글 패치 도구\n")
    for cat, items in _CATEGORIES:
        print("%s:" % cat)
        for name, desc in items:
            print("  %-11s %s" % (name, desc))
        print()
    print("'ujyu <명령> --help' 로 상세 보기.")


def _group_usage(cmd):
    table = _GROUP[cmd]
    subs = [s for s in table if s is not None]
    print("ujyu %s <하위> [인자...]\n" % cmd)
    print("하위 명령: %s" % " / ".join(sorted(subs)))
    if None in table:
        print("(하위 없이 'ujyu %s' 만 쓰면 기본 동작)" % cmd)
    print("\n'ujyu %s <하위> --help' 로 상세 보기." % cmd)


def _run(mod_name, prog, label, rest):
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, "main", None)
    if fn is None:
        print("%s 에 main() 이 없다" % mod_name)
        return 1
    sys.argv = [prog] + ([label] if label else []) + rest
    return fn() or 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in _HELP:
        _usage()
        return 0

    cmd, rest = argv[0], argv[1:]
    if cmd in _ALIAS:                          # 옛 이름을 새 경로로 펼친다
        path = _ALIAS[cmd]
        cmd, rest = path[0], path[1:] + rest

    if cmd in _LEGACY:
        return _run(_LEGACY[cmd], "ujyu dims", None, rest)

    if cmd in _GROUP:
        table = _GROUP[cmd]
        if rest and rest[0] in table:          # ujyu <그룹> <하위> ...
            sub = rest[0]
            mod, label = table[sub]
            return _run(mod, "ujyu %s %s" % (cmd, sub), label, rest[1:])
        if None in table:                      # 기본 동작 (ujyu exe / ujyu dmj info)
            mod, label = table[None]           # --help 도 그대로 넘긴다 (그 모듈의
            return _run(mod, "ujyu " + cmd, label, rest)   # epilog 가 하위를 안내)
        if rest and rest[0] in _HELP:          # ujyu <그룹> --help (기본 동작 없음)
            _group_usage(cmd)
            return 0
        if rest:
            print("알 수 없는 하위 명령: %s" % rest[0])
        _group_usage(cmd)
        return 0 if not rest else 1

    if cmd in _SIMPLE:
        return _run(_SIMPLE[cmd], "ujyu " + cmd, None, rest)

    print("알 수 없는 명령: %s\n" % cmd)
    _usage()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
