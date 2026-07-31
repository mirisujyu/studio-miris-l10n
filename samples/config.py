#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
타이틀별 설정 **템플릿** — 새 타이틀에 적용할 때 이 파일만 채운다.

주소·오프셋은 타이틀마다 다르다. 각 항목의 **찾는 법**은 `SKILL.md` 해당 절에 있고,
검증 타이틀(神無ノ鳥 2002)의 실측값은 `SKILL.md` 부록 A 에 표로 있다.

`None` 인 항목은 해당 패치를 건너뛴다. 채우기 전에는 그 기능이 동작하지 않는다.
"""
import os

# ─────────────────────────────────────────── 경로
GAME_DIR = os.environ.get("MIRIS_GAME_DIR", ".")   # 배포 폴더 (패치본이 놓일 곳)
ORIG_DIR = os.environ.get("MIRIS_ORIG_DIR", ".")   # 무패치 원본 게임 폴더 (읽기 전용)
WORK_DIR = os.environ.get("MIRIS_WORK_DIR",        # strings.json 등 작업 파일
                          os.path.dirname(os.path.abspath(__file__)))

def game(*p):  return os.path.join(GAME_DIR, *p)   # 배포본
def orig(*p):  return os.path.join(ORIG_DIR, *p)   # 원본
def work(*p):  return os.path.join(WORK_DIR, *p)

# ─────────────────────────────────────────── 아카이브  (SKILL.md 1절)
# 시나리오 아카이브를 **우선순위 오름차순**으로 나열한다.
# 수정(패치) 아카이브가 있으면 뒤쪽이 앞쪽의 같은 이름 파일을 덮어쓴다.
ARCHIVES      = ["scenario.axr"]        # 텍스트를 주입할 시나리오 아카이브 (오름차순)
BASE_ARCHIVE  = "scenario.axr"          # 주입 기준 아카이브 이름 (ORIG_DIR 에서 읽음)
OUT_ARCHIVE   = "scenario.axr"          # 주입 결과 이름 (GAME_DIR 에 씀)

# 이미지 텍스트 지역화 (14절). 폴더 컨벤션:
#   CG_ORIG_DIR    원본 이미지 (텍스트 있음)      — 참조/측정용
#   CG_NOTEXT_DIR  텍스트 제거 + 배경 복원한 베이스 — 렌더 입력
#   _cgui - {폰트}  폰트별 렌더 출력               — 여러 개 만들어 비교
#   CG_TRANS_DIR   그중 **빌드에 실제로 쓸 폴더** — build_patch 가 이걸로 리팩
CG_ARCHIVE    = "cg.axr"                 # 이미지가 든 아카이브 이름
CG_ORIG_DIR   = None                     # 원본 이미지 폴더
CG_NOTEXT_DIR = None                     # 무문자 베이스 폴더
CG_TRANS_DIR  = None                     # 최종 채택 폴더 (파일명 = 아카이브 엔트리명)

# `ujyu image` (manifest 주도 렌더) 가 읽는 경로. 위 CG_* 와 별개로, 렌더러는
# 애셋을 한 폴더 아래 모아 두는 구조를 쓴다. 템플릿: samples/images.sample.md
IMAGE_ASSET_DIR     = None                        # 애셋 루트 (아래 경로들의 기준)
IMAGE_SPEC          = None                        # manifest 가 든 Markdown (예: repo("translation","IMAGES.md"))
IMAGE_VARIANT       = None                        # 빌드에 쓸 글꼴 변형 이름 (manifest 의 fonts[] 중 하나)
# 아래 넷은 IMAGE_ASSET_DIR 하위 컨벤션. IMAGE_ASSET_DIR 를 정하면 그대로 두면 된다.
if IMAGE_ASSET_DIR:
    IMAGE_ORIGINAL_DIR  = os.path.join(IMAGE_ASSET_DIR, "original")   # 원문(글자 有), 측정 기준
    IMAGE_TEXTLESS_DIR  = os.path.join(IMAGE_ASSET_DIR, "textless")   # 글자 지운 베이스 = 렌더 입력
    IMAGE_FONT_DIR      = os.path.join(IMAGE_ASSET_DIR, "fonts")      # 렌더에 쓸 폰트 파일들
    IMAGE_TEXTED_PREFIX = os.path.join(IMAGE_ASSET_DIR, "texted-")    # 렌더 출력 접두(+변형명)
else:
    IMAGE_ORIGINAL_DIR = IMAGE_TEXTLESS_DIR = IMAGE_FONT_DIR = IMAGE_TEXTED_PREFIX = None

# ─────────────────────────────────────────── 텍스트/번역
STRINGS       = work("strings.json")    # [{file, off, bytelen, jp, kr, speaker}]
UI_STRINGS    = work("ui_strings.json")
NAMEPLATES    = work("nameplates.json") # 화자명 대응표 (gen_nameplates.py 입력)
NAMEPLATES_MD = work("NAMEPLATES.md")   # 생성될 대응표 문서

# 심볼 테이블에서 화자로 오인하면 안 되는 리소스명 패턴.       (SKILL.md 4절)
# 타이틀의 리소스 명명 규칙을 보고 조정한다.
RESOURCE_RE   = r'^(bg\d|se_|movie/|.*_se$|se$)'

# 텍스트처럼 디코드되지만 실제론 마커인 문자열.               (SKILL.md 5절)
# 빈도·문맥으로 식별한다 (대사 문맥 없이 수백 회 반복되는 1자 한자 등).
MARKERS       = set()

# 여러 글자로 된 커맨드 시퀀스 — 번역문에서 {n} 토큰으로 보존된다. (SKILL.md 2절)
# 꼬리의 연속 제어코드(0x30 미만)는 자동 처리되므로 여기 넣지 않아도 된다.
CMD_SEQS      = [chr(92) + "n"]         # 줄바꿈 커맨드 (백슬래시 + n)

# ─────────────────────────────────────────── common.csv  (SKILL.md 1절)
# 아카이브 안 common.csv 의 `string,<키>,<값>` 을 이 값으로 설정한다.
COMMON_CSV = {
    # "title": "한국어 제목 ",
}

# ─────────────────────────────────────────── 해상도 (docs/formats/RESOLUTION.md · SKILL 15절)
# 도구: ujyu scale (common/dims/cg/exe). build_patch 가 SCALE>1 이면 자동 수행.
SCALE = 1               # 정수 배율. 1=원본 해상도, 2/3=화면·레이아웃·콘텐츠 이미지 N× 확대
ORIG_W, ORIG_H = 640, 480   # 원본 해상도

SCALE_DIALOG_1X  = []   # 크기 1× 유지 + 우하단 시프트할 창 이름들 (15-6. 예: textwin namewin face)
SCALE_FS_WINDOWS = []   # 풀스크린 textwindow 이름들 — w/h·패딩 ×N (예: logwin)
SCN_DIMS         = {}   # {scn파일: [(오프셋, 바이트폭, 원본값), ...]} — 명시 치수 씬 (15-5)
SCN_DIMS_AUTO    = ()   # 좌표가 전부 int 심볼인 씬 — 빌드 시점에 도출해 ×N (예: save.scn config.scn)
# 스케일 **비대상**이라 1× 크기로 남는 창을 확대 화면 가운데로 옮긴다.
# {창이름: 세로정렬 기준 높이}. 선택지 창은 common.csv 의 textwindow,select +
# int,select_* 로 그려지는데 이 값들은 버튼·글꼴 계산에 쓰여 ×N 하면 어긋난다.
COMMON_CENTER    = {}   # 예: {"select": 320}
CG_UPSCALE_DIR   = None # 외부 AI 업스케일 결과 폴더(같은 파일명·원본×N). 없으면 bilinear
CG_CONTENT_PREFIX = ()  # ×N 할 콘텐츠 이미지 프리픽스 (배경·캐릭터·CG)
CG_UI_1X_PREFIX   = ()  # 1× 유지할 UI 프리픽스 (대사창·메뉴·얼굴)
CG_FORCE_1X       = ()  # 1× 강제할 개별 파일명
OFF_SCREEN_W = []       # exe 화면 폭(=ORIG_W) dword 파일오프셋들 (15-2)
OFF_SCREEN_H = []       # exe 화면 높이(=ORIG_H) dword 파일오프셋들

# 무비 네이티브 재생 (RESOLUTION.md §6-1, ujyu exe movie)
# 엔진은 무비를 항상 2배로 확대해 그린다. 아래를 켜면 1:1 로 바꿔 화면과 같은
# 치수의 .dmj 를 네이티브 해상도로 재생한다(무비도 그 치수로 다시 인코딩할 것).
MOVIE_NATIVE   = False  # True = 2배 확대 끔
OFF_MOVIE_SCALE = None  # 무비 ctor 의 `push 2` 즉치 파일오프셋
MOVIE_ARCHIVE  = "movie.axr"   # 무비가 든 아카이브 이름
# 화면 치수로 다시 인코딩한 무비(`ujyu dmj encode` 결과)를 담은 폴더.
# 지정하면 `ujyu build movie` 가 여기서 가져오고, 없으면 원본을 그대로 복사한다.
MOVIE_SRC_DIR  = None

# ─────────────────────────────────────────── 배포(release)
# `ujyu release` 가 원본 대비 diff 를 낼 때 **제외**할 glob 패턴.
# 사용자 데이터(세이브)와 작업 부산물(백업·중간 산출물)은 패치에 들어가면 안 된다.
# 배포 README 에 덧붙일 타이틀별 주의사항(config 로는 알 수 없는 것).
RELEASE_NOTES = []          # 예: ["exe 에 Windows 8 호환 모드를 설정해야 합니다."]

RELEASE_EXCLUDE = [
    "save/*", "*.sav",              # 사용자 세이브 — 절대 배포 금지
    "*.bak", "*.orig", "*.tmp", "*.log",
    "*.pre*", "*.goto",             # 작업 중 만든 스냅샷
    "_*",                           # 언더스코어로 시작하는 작업 폴더/파일
]

# ─────────────────────────────────────────── exe 패치
EXE_IN   = orig("game.exe")             # 무패치 원본 exe (patch_exe 입력)
EXE_OUT  = game("game.exe")             # 패치본 배포 위치
DLGFONT  = "맑은 고딕"                   # 다이얼로그 리소스 폰트          (10절)

FONT_FACE     = None    # 게임에 넣을 폰트 face 이름. FILTER_PREFIX 로 시작해야 목록에 뜬다
FONT_FALLBACK = None    # case-C 폴백 face                             (SKILL.md 8-3)
FILTER_PREFIX = None    # 글꼴 목록에 노출할 face 접두어 (최대 7자+널)   (SKILL.md 8-1)

IMAGE_BASE = 0x400000   # ASLR 없는 빌드면 보통 이 값. 파일오프셋 = VA - IMAGE_BASE

# ── 아래는 전부 **파일 오프셋**. 찾는 법은 SKILL.md 의 표기된 절 참조.
OFF_LEAD_BITMAP     = None   # 32B 리드바이트 비트맵 — 한글 렌더의 핵심   (6-1)
OFF_CHARSET_BODY    = None   # 본문 폰트 charset  0x80 -> 0x81           (6-2)
OFF_CHARSET_ENUM    = None   # 글꼴 열거 charset  0x80 -> 0x81           (6-2)
# SJIS 리드 idiom → CP949 리드 인정. (파일오프셋, 교체바이트hex) 쌍.   (6-3)
# 교체는 sub ...,0x81; cmp ...,0x7E (sub 즉치는 imm32). 앞뒤 여분은 NOP.
SJIS_IDIOM          = []

# 코드에 박힌 짧은 2바이트 문자 상수(괄호·기호)를 CP949 로 재인코딩.  (파일오프셋, hex) 쌍.
# 1~2자는 같은 바이트열이 여러 곳에 있어 내용 검색이 위험하므로 오프셋을 명시한다.
INLINE_RECODE       = []     # 예: [(0x761CC, "a1 bd")]

OFF_FILTER_PITCH    = []     # 비례/FIXED_PITCH 필터 → NOP 6B            (8-1)
OFF_FILTER_PATTERN  = None   # 목록 필터 패턴 문자열 자리 (8바이트)       (8-1)
OFF_FILTER_PUSH     = None   # 그 패턴을 push 하는 imm32
OFF_FILTER_JCC      = None   # jne(제외) -> je(포함) 6B

OFF_FONT_GOTHIC     = None   # GOTHIC 기본 글꼴 슬롯 (16B)               (8-2)
OFF_FONT_MINCHO     = None   # MINCHO 기본 글꼴 슬롯 (16B) — 본문이 쓰는 쪽
OFF_FONT_FALLBACK   = None   # case-C 폴백 face (16B)                    (8-3)

# ── 글꼴 저장/복원 코드 케이브                                          (9절)
CAVE_VA         = None       # .text 끝 패딩 (실행 가능, 200B 이상 여유)
BUF_VA          = None       # .data 여유 구간 (읽기 버퍼 64B)
NBYTES_VA       = None       # 〃 (읽은 바이트 수 4B)
SAVE_REL_PATH   = r"save\systemdata.dat"
SAVE_NAME_OFF   = 8          # 세이브 파일에서 글꼴명이 시작하는 오프셋
IAT_CreateFileA = None
IAT_ReadFile    = None
IAT_CloseHandle = None

# ─────────────────────────────────────────── 폰트 빌드  (SKILL.md 7·16절)
# 빌드 스펙은 config 와 분리해 `fonts/<face>.py` 에 둔다 (템플릿: samples/font_spec.py).
# 렌더링 폭 정책 — **값이 갈리는 선택 항목이므로 사용자와 문답으로 정한다**:
#   "fullwidth"    : 전 글자 전각 고정폭. `。`·`、` 를 그대로 둔다(간격이 자연스럽다).
#   "proportional" : 가변폭. 괄호·기호를 스펙대로 조정하고, 주입 시 `。`·`、` 를
#                    `．`·`，`(+뒤 공백)로 정규화한다.
FONT_WIDTH_MODE = "fullwidth"
# 세로 시프트·표본 음절 등 나머지 값은 스펙 파일(TARGET_BOTTOM_EM / SAMPLE)에 있다.
# 빌드: ujyu font fonts/<face>.py <face>.ttf   (--mode 를 생략하면 FONT_WIDTH_MODE 사용)


# ─────────────────────────────────────────── 검사
def require(*names):
    """필수 설정이 채워졌는지 확인. 도구가 시작할 때 호출한다."""
    missing = [n for n in names if globals().get(n) in (None, [], "")]
    if missing:
        raise SystemExit(
            "config.py 에 다음 항목을 먼저 채우세요: %s\n"
            "  찾는 법은 SKILL.md, 검증 타이틀 실측값은 부록 A 참조."
            % ", ".join(missing))
