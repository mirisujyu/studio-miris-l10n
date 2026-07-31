# 제로 부트스트랩 — 원본 게임 하나에서 한글 패치까지

타이틀 리포가 **하나도 없는 상태**에서 무패치 원본 게임 폴더만 가지고 배포 패치까지
만들어 가는 순서. [GUIDE.md](../GUIDE.md)가 "어떻게 일하는가"(결정 주체·검증·진단)라면 이 문서는
**"어떤 명령을 어떤 순서로, 그 결과 config 의 무엇이 채워지는가"** 다.
명령 전체 목록은 [COMMANDS.md](COMMANDS.md), 작성 규칙은 [CLI_STYLE.md](CLI_STYLE.md).

## 작업은 5개 프로세스다

```
init  ->  unpack  ->  extract  ->  [폰트·이미지·번역·주입]  ->  build  ->  release
```

> **폰트를 번역보다 먼저 만든다.** `ujyu font` 는 빌드하면서 `jpmap`(미번역 일본어를
> CP949 빈 자리에 싣는 표)을 같이 만드는데, 그 표는 **아직 번역이 안 된 글자**로
> 만들어진다. 번역이 진행되면 대상이 줄어 표가 바뀌고, 표가 바뀌면 폰트도 다시
> 만들어야 한다(어긋나면 화면의 글자가 다른 글자로 바뀐다).
> **번역 0% 일 때 한 번 만들어 두면 그 뒤로는 자리가 줄기만 하므로 안전하다.**

**전체 프로세스 명령은 포맷 처리기·유틸리티를 자동으로 엮어 돌린다.** 각 프로세스가
안에서 무엇을 부르는지는 해당 절에 표로 적었다. 개별 명령을 직접 쓸 일은 정찰·디버깅·
부분 재빌드뿐이다.

곁에 두고 쓰는 **설정 관리기**가 둘 있다:

| 명령 | 역할 |
|---|---|
| `ujyu inspect` | 지금 config 에 뭐가 채워졌고 **다음에 뭘 해야 하는지**. 문제 있으면 종료 코드 1 |
| `ujyu config show\|get\|set` | 값 확인·변경. `set` 은 **값이 유효한지 검사한 뒤** config.py 를 고친다 |

각 항목의 **결정 주체**를 함께 적는다 — `문답`(값이 갈리는 선택지, 사용자에게 물어
정함) / `분석`(리버싱·데이터 대조로 찾음) / `자동`(명령이 산출).

---

## 0. 준비 — ujyu 설치

`ujyu` 는 엔진 리포에 들어 있다. **타이틀 리포를 만들기 전에 먼저 설치해야** 명령을
쓸 수 있다(그래서 순서가 git -> submodule -> pip -> init 이다).

```bash
mkdir <title> && cd <title>
git init
git submodule add <engine repo> engine
pip install -e engine            # 여기서 `ujyu` 명령이 생긴다
```

무비 인코딩·exe 분석까지 쓸 거면 `pip install -e "engine[all]"`.

## 1. `ujyu init` — 리포 골격과 기본 config.py

```bash
ujyu init . --title "<타이틀 이름>"
```

- 폴더(`translation` `fonts` `docs` `tools`)와 템플릿을 깐다:
  `config.py`, `fonts/Sample.py`(폰트 스펙), `translation/IMAGES.md`(이미지 manifest),
  `translation/ui_strings.json`. **기존 파일은 덮지 않고 건너뛴다**(`--force` 로 허용).
- 그다음 경로 세 개를 채운다. 이것만 있으면 `unpack` 이 돌아간다.

| config | 주체 |
|---|---|
| `ORIG_DIR` (무패치 원본, **읽기 전용**) · `GAME_DIR` (배포본) · `WORK_DIR` | 문답 |

```bash
ujyu config set ORIG_DIR /path/to/orig      # 경로가 실제로 있는지 검사한다
ujyu config set GAME_DIR /path/to/deploy
ujyu inspect                                # 지금 상태와 다음 할 일
```

원본과 배포 폴더는 반드시 분리한다. 빌드는 매번 원본에서 다시 만들므로 재현 가능하다.
이후 모든 명령은 **타이틀 리포 루트**에서 실행한다(루트 `config.py` 를 자동으로 읽는다).
다른 위치에서 돌릴 땐 `MIRIS_CONFIG_DIR=<config.py 폴더>`.

## 2. `ujyu unpack` — 한글화 대상 전부 풀고 디스어셈블

```bash
ujyu unpack                              # WORK_DIR 아래로
ujyu unpack --only scenario.axr --no-disasm
ujyu unpack --all                        # 음성·BGM 등 대용량까지 (기본 제외)
```

안에서 자동으로 하는 일:

| 하는 일 | 대응 개별 명령 |
|---|---|
| 아카이브를 전부 풀어 `<WORK>/unpack/<아카이브>/` | `ujyu axr unpack` |
| `.scn` 을 디스어셈블해 `<WORK>/disasm/` | `ujyu scn disasm` |
| 아카이브별 엔트리 수·종류 분포 요약 | `ujyu axr list` |

- 아카이브 첫 4바이트가 `AXRe`, `.scn` 첫 4바이트가 `VNEG` 이면 이 도구 대상이다.
- 종류 분포로 어느 아카이브가 **시나리오/이미지/무비**인지 가른다.
- 디스어셈블 산출 첫 줄의 `defs=`·`runtime_symbols=` 와 `!!! SYMBOL TABLE PARSE FAILED !!!`
  유무를 본다. 파싱이 깨지면 추출도 깨진다 — 여기서 멈추고 [VNEG.md](formats/VNEG.md).

| config | 주체 |
|---|---|
| `ARCHIVES` (시나리오, **우선순위 오름차순**) · `BASE_ARCHIVE` · `OUT_ARCHIVE` | 분석 -> 문답 확인 |
| `CG_ARCHIVE` · `MOVIE_ARCHIVE` · `PASSTHROUGH_ARCHIVES` | 분석 |
| `EXE_IN` / `EXE_OUT` | 분석 |

패치 증분(`.ax2`, `.ax3`…)이 있으면 **base -> 증분 순서**가 중요하다. 뒤엣것이 앞엣것의
같은 이름 `.scn` 을 덮어쓴다(유효본).

## 3. `ujyu extract` — 번역할 것 전부 추출

```bash
ujyu extract
ujyu extract --only scenario,nameplates
ujyu extract --force                     # 이미 있는 산출물 덮어쓰기(.bak 남김)
```

안에서 자동으로 하는 일:

| 하는 일 | 산출 | 대응 개별 명령 |
|---|---|---|
| 시나리오 텍스트(화자 포함) 구조적 추출 | `STRINGS` (strings.json v2) | `ujyu scn extract` |
| common.csv 의 일본어 표시 문자열 | `COMMON_CSV` 후보 | `ujyu csv todo` |
| exe 리소스·인라인 문자열 | `UI_STRINGS` 초안 | (`ujyu exe ui` 의 입력) |
| 이미지 엔트리·치수 목록 | `IMAGE_SPEC` manifest 골격 | `ujyu axr list` |
| 화자 집계 -> 대응표 | `NAMEPLATES_MD` | `ujyu nameplates` |

**번역이 든 `strings.json` 은 덮어쓰지 않는다** — 이미 있으면 건너뛰고, `--force` 를
줘도 `.bak` 을 남긴다.

| config | 주체 |
|---|---|
| `STRINGS` · `UI_STRINGS` · `NAMEPLATES` · `IMAGE_SPEC` | 문답(경로) |
| `RESOURCE_RE`(리소스명 제외) · `MARKERS` · `CMD_SEQS`(줄바꿈 등 커맨드) | 분석 |

확인: `ujyu filter stats` (분량·파일별 분포)

## 4. 번역·이미지·주입 — 사람이 하는 구간

여기가 실제 작업이다. 프로세스 명령이 아니라 유틸리티를 직접 쓴다.

### 4-1. 착수 전에 정할 것 — **문답으로**

값이 갈리는 항목은 임의로 정하지 말고 물어본다(GUIDE §1). 특히 **이미지는 잊기 쉽다**:

| 물어볼 것 | 안 물으면 생기는 일 |
|---|---|
| **이미지 번역 여부·범위** | 타이틀 로고·버튼·CG 안 글자가 일본어로 남는다 |
| **이미지 업스케일 소스** | `SCALE>1` 인데 그림만 흐릿하게 늘어난다 |
| `SCALE` · `FONT_WIDTH_MODE` · `MOVIE_NATIVE` | 기본값으로 굳어 되돌리기 번거로워진다 |
| 글꼴 face 이름 | 동명 폰트가 이미 설치돼 있으면 GDI 가 그쪽을 골라 새 폰트가 무시된다 |

번역 사전 — `CHARACTERS.md`(인물·말투) · `GLOSSARY.md`(고유명사) ·
`NAMEPLATES.md`(화자명 표기)를 `translation/` 에 만든다. 조사·표기 결정이라 명령이
없다. **번역을 시작하기 전에** 만들어 둬야 스타일이 흔들리지 않는다.

### 4-2. exe 패치와 폰트 (처음 한 번)

한국어가 화면에 나오게 하는 선행 조건. **먼저 스캐너**를 돌린다:

```bash
ujyu exe scan <ORIG_DIR>/game.exe --config     # config 에 붙여넣을 스니펫
```

스캐너가 못 찾는 것만 손으로 분석한다(아래 "적중 범위"):

```bash
ujyu exe disasm <exe> info | at <va> | fn <va> | xref <va> | imm <값>
```

찾는 법은 SKILL.md 6~10절, 구조는 [TEXT_RENDER.md](formats/TEXT_RENDER.md) ·
[WINDOWS_UI.md](formats/WINDOWS_UI.md).

| config | 주체 |
|---|---|
| `IMAGE_BASE` · `OFF_LEAD_BITMAP`(한글 렌더의 핵심) · `OFF_CHARSET_BODY/ENUM` · `SJIS_IDIOM` | 분석 |
| `OFF_FILTER_*`(글꼴 목록 필터 해제) · `OFF_FONT_GOTHIC/MINCHO/FALLBACK` | 분석 |
| `CAVE_VA` · `BUF_VA` · `NBYTES_VA` · `SAVE_REL_PATH` · `SAVE_NAME_OFF` | 분석 |
| `INLINE_RECODE`(코드에 박힌 2바이트 문자 상수) | 분석 |
| `DLGFONT` · `UI_STRINGS` 내용 | 문답(번역 문구) |
| `FONT_WIDTH_MODE` (`fullwidth` 전각 고정 / `proportional` 가변) | **문답** |
| `FONT_FACE` · `FONT_FALLBACK` · `FILTER_PREFIX` | 문답 + 분석 |

```bash
ujyu exe                                 # 전체 패치 (ui + 엔진 바이트 + 코드케이브)
ujyu font fonts/<face>.py <face>.ttf     # 스펙 주도. --mode 생략 시 FONT_WIDTH_MODE
```

`FONT_WIDTH_MODE` 는 문장부호 처리까지 결정한다(가변폭이면 주입 시 `。`·`、` 가
`．`·`，`+공백 으로 정규화된다). 값이 갈리는 항목이라 반드시 문답으로 정한다.

### 4-3. 텍스트 번역

> ⛔ **시작하기 전에 사용자에게 묻는다.** 냅다 번역을 시작하지 않는다.
>
> 번역은 이 작업에서 가장 크고 오래 걸리는 구간이다. 한번 굴러가기 시작하면 수천
> 조각에 스타일이 굳어서, 나중에 표기 하나를 바꾸려면 전수 수정이 된다. 그래서
> **아래가 다 끝났는지 확인하고, 시작해도 되는지 물은 뒤** 진행한다:
>
> | 선행 조건 | 왜 |
> |---|---|
> | 폰트 빌드 (`ujyu font`) | 번역이 쌓이면 `jpmap` 표가 바뀌어 폰트를 다시 만들어야 한다 (첫머리) |
> | 번역 사전 3종 | 없으면 화자별 말투·고유명사가 조각마다 흔들린다 |
> | 이미지 방침 | 텍스트만 하고 끝나는 사고를 막는다 (§4-1) |
> | 표기 규칙 (`STYLE.md`) | 종결부·들여쓰기·기호 처리는 되돌리기가 가장 비싸다 |
>
> 물을 때는 **범위와 규모를 같이 제시한다** — 몇 조각·몇 자인지(`ujyu filter stats`),
> 어디부터 할지, 한 번에 얼마나 할지. "번역할까요?" 만으로는 사용자가 판단할 수 없다.

```bash
ujyu filter dump 0 200                   # id·화자·원문 TSV
ujyu filter dump --file 0506_01.scn --kind dlg,narr
#   -> kr 열을 채운다
ujyu filter apply out.tsv
ujyu inject check                        # 제약 전수 검증
ujyu inject preview <idx>                # 한 조각 인코딩 미리보기
```

`inject check` 가 **CP949 인코딩 불가 문자·제어코드 불일치**를 전부 잡는다.
여기서 0건이 되기 전에는 빌드하지 않는다.

### 4-4. 이미지 텍스트

```bash
ujyu image --list                        # manifest 의 글꼴 변형 목록
ujyu image --check                       # 렌더 없이 명세·입력 검사
ujyu image --variant <글꼴변형>
```

**무문자 베이스 이미지**(글자 지우고 배경 복원)와 **bbox·색 측정**은 그림·판단 작업이라
명령이 없다. manifest 형식은 `samples/images.sample.md`.

### 4-5. 해상도 확대 (선택)

`SCALE` 을 2/3 으로 두면 `build` 가 아래를 **자동으로 포함**한다. 개별 실행:

```bash
ujyu scale exe|common|center|dims|cg ...
ujyu scale cg-export <cg.axr> --out _upscale     # 외부 AI 업스케일러로 보낼 것만 추출
ujyu scale cg-check  _upscale _upscale/2x        # 결과 검사(누락·치수·알파)
ujyu scale cg <cg.axr> --out <출력> --from-dir _upscale/2x
```

| config | 주체 |
|---|---|
| `SCALE` (1/2/3) | **문답** |
| `OFF_SCREEN_W` / `OFF_SCREEN_H` | 분석 |
| `SCN_DIMS` · `SCN_DIMS_AUTO` | 분석 |
| `SCALE_DIALOG_1X` · `SCALE_FS_WINDOWS` · `CG_CONTENT_PREFIX` · `COMMON_CENTER` · `CG_UPSCALE_DIR` | 분석 -> 문답 확인 |

### 4-6. 무비 (선택)

```bash
ujyu dmj --file <ORIG_DIR>/opening.dmj info
ujyu dmj encode in.mp4 out.dmj --ref <ORIG_DIR>/opening.dmj --size 1280x960
ujyu exe movie                           # 엔진의 2배 확대를 끄고 네이티브 재생
```

| config | 주체 |
|---|---|
| `MOVIE_NATIVE` · `OFF_MOVIE_SCALE` | 문답 + 분석 |
| `MOVIE_SRC_DIR` (다시 인코딩한 무비 폴더) | 문답 |

## 5. `ujyu build` — 배포본 조립

```bash
ujyu build                    # 전체
ujyu build cg                 # cg.axr 만
ujyu build scenario movie     # 여러 단계만
ujyu build all --no-exe       # exe 빼고 전체
```

단계와 각 단계가 자동으로 부르는 것:

| 단계 | 하는 일 | 안에서 부르는 것 |
|---|---|---|
| `exe` | 원본 exe -> UI·엔진 바이트·코드케이브 | `ujyu exe`, `SCALE>1` 이면 `ujyu scale exe` |
| `scenario` | 유효본 아카이브별 텍스트 주입 + **점프테이블 재매핑** | `ujyu inject build`, `SCALE>1` 이면 `ujyu scale common`·`center`·`dims` |
| `title` | common.csv 창 제목·기본 글꼴 | `ujyu title apply` |
| `cg` | 번역 PNG 주입 + 콘텐츠 이미지 ×N | `ujyu scale cg --from-dir CG_UPSCALE_DIR` |
| `movie` | 무비 아카이브 배치(`MOVIE_SRC_DIR` 있으면 거기서) | (복사) |

**순서는 강제된다** — 제목은 텍스트 주입 뒤여야 한다(아카이브 재팩에 덮인다).
`--no-<단계>` 로 빼고, 단계를 나열하면 그것만 돈다.

## 6. `ujyu release` — 인스톨러 패키지

```bash
ujyu release --dry-run        # 무엇이 들어갈지·용량만
ujyu release --name kr-patch
```

- `ORIG_DIR` 과 `GAME_DIR` 을 재귀 비교해 **바뀐·추가된 파일만** 담는다
  (게임 데이터는 재배포할 수 없으므로 전체를 담지 않는다).
- 원본 sha256 을 manifest 에 기록하고, 표준 라이브러리만 쓰는 `install.py` 를 함께 넣는다.
  인스톨러는 적용 전 사용자 파일 해시를 검증하고, 다르면 **아무것도 쓰지 않고** 중단한다
  (이미 패치됨 / 다른 버전을 구분해 안내). `.orig` 백업 + `--uninstall` 복원 지원.

## 검증 루프

```bash
ujyu inspect                                              # config 문제 0 확인
ujyu inject check                                         # 번역 제약 위반 0 확인
ujyu scn disasm <GAME_DIR>/scenario.axr ... -o _verify     # KR 텍스트·심볼·점프테이블
ujyu csv show <GAME_DIR>/scenario.axr                     # 제목·해상도 반영
```

특정 씬을 바로 열어 확인하려면 테스트 세이브를 만든다:

```bash
ujyu save show <GAME_DIR>/save/savedata1.dat --archive <GAME_DIR>/scenario.axr
ujyu save goto <GAME_DIR>/save/savedata1.dat 0506_02 -o <GAME_DIR>/save/savedata9.dat
```

씬 이름은 **같은 길이**여야 한다(엔진 제약). 길이가 다르면 `goto` 가 쓸 수 있는 같은
길이 씬 목록을 보여준다.

실게임에서 오프닝·선택지·무비 구간을 확인한다. 증상별 진단과 실무 함정은
[GUIDE.md](../GUIDE.md) §4·§5.

---

## `ujyu exe scan` 의 적중 범위

검증 타이틀(神無ノ鳥) 원본 exe 대조: 확실 등급 22개 중 **21개가 사람이 확정한 값과
바이트 단위로 일치**, 확실 등급 오탐 0.

| 찾는다 | 못 찾는다 |
|---|---|
| `OFF_LEAD_BITMAP`(후보 1개, 오탐 0) · `OFF_CHARSET_BODY`/`ENUM` · `OFF_FONT_GOTHIC`/`MINCHO`/`FALLBACK` · `OFF_SCREEN_W`/`H`(개수·순서까지) · `SJIS_IDIOM`(교체 바이트까지) · `CAVE_VA`(±0x10) · `BUF_VA`/`NBYTES_VA` · IAT 3개 | `OFF_FILTER_PITCH`/`PATTERN`/`PUSH`/`JCC`(글꼴 목록 필터 — 콜백 구조 판단 필요) · `OFF_MOVIE_SCALE` · `INLINE_RECODE` |

즉 exe 단계에서 손으로 찾을 것은 **글꼴 목록 필터 4개, 무비 스케일, 인라인 문자 상수**로
줄었다. 스캔 결과는 `--config` 로 붙여넣을 스니펫이 나오지만 **값을 config 에 자동으로
쓰지는 않는다** — 사람이 확인하고 넣는다.

## 사람의 판단이 필요한 지점

명령으로 닫을 수 있는 공백은 닫았다. 남은 것은 자동화 대상이 아닌 판단이다.

| 지점 | 왜 명령이 아닌가 |
|---|---|
| 번역 그 자체 | 말투·표기·문맥 판단 |
| 인물·용어 사전(`CHARACTERS.md`·`GLOSSARY.md`) | 외부 출처 조사 + 표기 결정 |
| 무문자 베이스 이미지 | 글자를 지우고 배경을 복원하는 그림 작업 |
| 이미지 manifest 의 bbox·색 | 원본을 보고 재는 일 (템플릿은 `samples/images.sample.md`) |
| 문답으로 정하는 옵션 | `FONT_WIDTH_MODE`·`SCALE`·표기 정책 — 값이 갈린다 |
| 스캐너가 못 찾는 exe 오프셋 | 콜백 구조·의미 판단 (`ujyu exe disasm` 으로 분석) |
