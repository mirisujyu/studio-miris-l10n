# -*- coding: utf-8 -*-
"""텍스트 폰트 빌드 스펙 **템플릿** — 타이틀 리포의 `fonts/<face>.py` 로 복사해 채운다.

`ujyu font <이 파일> <출력.ttf> [--mode proportional|fullwidth]` 가 이 스펙을 읽어
게임에 넣을 한글 폰트를 만든다. 하는 일은 세 가지다(SKILL.md 16절).

1. **세로 시프트** — 엔진이 GetGlyphOutlineA(GGO_GRAY8) 로 그려 글자 아래가 잘린다.
   한글 최저점을 TARGET_BOTTOM_EM 위치로 끌어올린다.
2. **전각 ASCII 재매핑 + CMAP_ALIAS** — 모드와 무관하게 항상 적용한다.
3. **가변폭 조정**(SPACE/OPEN/OPEN_RSB/CLOSE/FIXED) — `--mode proportional` 일 때만.
   `--mode fullwidth` 면 건너뛰고 모든 글자가 원래 전각 고정폭으로 남는다.
   어느 쪽을 쓸지는 config.FONT_WIDTH_MODE 로 정한다(값이 갈리는 항목이라 문답으로 결정).

값은 안전한 기본값이다. 새 글꼴을 시험할 땐 이 파일을 복사해 값만 바꾼다.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────── SOURCE : 바탕이 될 한글 폰트 파일
# 이 폰트의 글리프를 손봐서 결과물을 만든다. 반드시 **한글 글리프가 든 폰트**여야 한다
# (없으면 시프트 계산이 실패한다). .ttf/.otf 모두 되고, OTF(CFF)는 빌더가 TrueType 으로
# 바꿔 준다(게임의 글꼴 목록 콜백이 OpenType 을 걸러내기 때문).
# 고르는 법: 본문이 작게(16~20px) 그려지므로 획이 가늘지 않은 고딕 계열이 안전하다.
# 배포까지 생각하면 라이선스가 자유로운 글꼴(예: Noto Sans KR, 나눔고딕)을 타이틀
# 리포 `fonts/` 에 두고 첫 후보로 적는다. 시스템 폰트는 시험용 기본값일 뿐이다.
_SOURCE_CANDIDATES = [
    os.path.join(_HERE, "SampleKR.otf"),        # 타이틀 리포 fonts/ 에 둔 소스 (권장)
    os.path.join(_HERE, "SampleKR.ttf"),
    r"C:\Windows\Fonts\malgun.ttf",             # 맑은 고딕 — 시험용 폴백
]
SOURCE = next((p for p in _SOURCE_CANDIDATES if os.path.isfile(p)), None)
if SOURCE is None:
    raise SystemExit(
        "소스 폰트를 찾을 수 없습니다. 이 스펙의 _SOURCE_CANDIDATES 에 실제 폰트 경로를 "
        "적으세요. 찾아본 곳:\n  " + "\n  ".join(_SOURCE_CANDIDATES))

# ─────────────────────────────────────────── FACE : 게임이 찾는 글꼴 이름
# 게임은 CreateFontIndirectA(FACE) 로 이 이름을 찾는다. 결과 .ttf 의 name 레코드가
# 이 값으로 통째로 교체되므로 **원본 글꼴과 다른 고유한 이름**을 써야 한다
# (같은 이름이면 시스템에 이미 설치된 원본과 충돌한다).
# 제약: config.FILTER_PREFIX(글꼴 목록에 노출할 접두어, 최대 7자+널)로 시작해야
# 게임 안 글꼴 선택 목록에 뜬다. 예) FILTER_PREFIX="Sample" → FACE="SampleKR".
FACE = "SampleKR"

# ─────────────────────────────────────────── 세로 시프트
# 한글 최저점(받침 아래)을 em 좌표계의 이 높이로 올린다. 0 이면 베이스라인,
# 값이 클수록 글자가 위로 뜬다. 대사창에서 받침이 잘리면 **키우고**, 글자가 위로
# 떠 보이면 줄인다. 0.03 안팎에서 시작해 실제 화면을 보고 0.005 씩 조정한다.
# `ujyu font ... --shift-em N` 으로 이 계산을 무시하고 직접 지정할 수도 있다.
TARGET_BOTTOM_EM = 0.031

# 최저점을 재는 표본 음절. 받침이 깊은 글자를 섞어야 기준이 안정적이다.
# 기본값 = 가(AC00)·힣(D7A3)·그(ADF8)·장(C7A5)·뵵(B755).
SAMPLE = [0xAC00, 0xD7A3, 0xADF8, 0xC7A5, 0xB755]

# ─────────────────────────────────────────── 가변폭 조정 (--mode proportional 전용)
# 원문이 전각 고정폭을 전제로 짜여 있어 한국어를 그대로 넣으면 괄호·공백이 너무 넓다.
# 아래 표는 {유니코드 코드포인트: 값} 이고, 값 단위는 폰트 유닛(unitsPerEm 기준)이다.
# 한글 한 글자의 advance 를 기준(예: upm=1000 이면 1000, Noto Sans KR 계열은 920)으로
# 잡고 그 분수로 정한다. --mode fullwidth 면 이 절은 통째로 무시된다.

# 소스 폰트의 한글 한 글자 advance. 폰트마다 unitsPerEm 이 달라(1000·2048…) 값을 그대로
# 베끼면 안 되므로 '가'(U+AC00) 의 실제 advance 를 읽어 쓴다. 소스가 고정이면 아래 표에
# 상수를 직접 적어도 된다(예: Noto Sans KR = 920).
def _hangul_advance(path, default=1000):
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(path, fontNumber=0, lazy=True)
        gn = f.getBestCmap().get(0xAC00)
        return f["hmtx"][gn][0] if gn else default
    except Exception:
        return default

_EM = _hangul_advance(SOURCE)

# 전각공백 등 '빈' 글자의 advance. 어절 구분에 U+3000 을 쓰므로 너무 넓으면
# 문장이 뚝뚝 끊겨 보인다. 한글 폭의 1/4 정도가 무난하다.
SPACE = {0x3000: _EM // 4}

# 여는 괄호의 오른쪽(안쪽) 여백. 획을 셀 오른끝에 붙일 때 남길 틈이다.
# 값이 작을수록 괄호가 뒤 글자에 바짝 붙는다.
OPEN_RSB = 55

# 여는 괄호: advance 를 줄이고 획을 오른쪽으로 정렬한다. 남는 왼쪽 여백(lsb)이
# 문두 들여쓰기 노릇을 한다. 「 『 ( 처럼 대사 첫 글자로 오는 것들.
OPEN = {0x300C: _EM // 2, 0x300E: _EM // 2, 0x0028: _EM // 2}

# 닫는 괄호: advance = 획의 오른끝 + 이 값. 즉 '트인 쪽'에 남길 여백만 적는다.
# 값이 크면 문장 끝이 헐거워 보인다.
CLOSE = {0x300D: 83, 0x300F: 83}

# 고정 advance. 획이 셀보다 넓으면 왼쪽으로 밀어 맞춘다. 【】처럼 획이 두꺼워
# 좁히면 뭉개지는 괄호는 한글 폭 그대로 두는 편이 낫다.
FIXED = {0x3010: _EM, 0x3011: _EM}

# 소스에 글리프가 없는 문자를 있는 글리프로 돌려 쓴다. {없는 cp: 대신 쓸 cp}.
# 빈 네모(.notdef)로 나오는 문자를 발견하면 여기에 추가한다.
# 예) ～(U+FF5E 전각 물결)가 없으면 ~(U+007E)로.
CMAP_ALIAS = {0xFF5E: 0x007E}
