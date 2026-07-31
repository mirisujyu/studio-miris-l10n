# ujyu 명령 목록

Studio Miris 엔진 한글 패치 도구. 실제 작업 순서는 [BOOTSTRAP.md](BOOTSTRAP.md),
인자·도움말 작성 규칙은 [CLI_STYLE.md](CLI_STYLE.md), 깊은 원리는 상위 `SKILL.md`.

`ujyu --help` 로 목록, `ujyu <명령> --help` 로 상세를 본다. 옛 이름(`doctor`,
`dmj-encode`, `ui`, `movie`, `fontrestore`, `disasm-x86`, `dims` …)은 숨은 별칭으로
계속 동작한다.

## 먼저 할 일

**타이틀 리포 루트의 `config.py` 만 채우면 된다** (템플릿: `../samples/config.py`).
경로·아카이브 목록·폰트 이름·exe 오프셋이 전부 여기 있다. 오프셋 찾는 법은
`SKILL.md` 6~9절, 대부분은 `ujyu exe scan` 이 찾아 준다.

```bash
ujyu config set ORIG_DIR /path/to/orig     # 무패치 원본 (읽기 전용)
ujyu config set GAME_DIR /path/to/deploy   # 패치본이 놓일 배포 폴더
ujyu inspect                               # 지금 상태와 다음 할 일
```

환경변수 `MIRIS_ORIG_DIR`/`MIRIS_GAME_DIR`/`MIRIS_WORK_DIR` 로도 덮어쓸 수 있다.
명령은 타이틀 리포 루트에서 실행하면 루트의 config.py 를 읽고, 다른 위치에서는
`MIRIS_CONFIG_DIR` 로 지정한다. 원본과 배포 폴더는 **분리**한다 — 빌드는 매번 원본에서
다시 생성하므로 재현 가능하다.

---

## 1. 설정 관리기

| 명령 | 역할 |
|---|---|
| `ujyu inspect` | config 를 단계별로 진단해 OK/미설정/문제로 분류하고 **채우는 명령**을 제시. 진행률도 센다. 문제 있으면 종료 코드 1 |
| `ujyu config show\|get\|set` | 값 확인·변경. `set` 은 **유효성을 검사한 뒤** config.py 의 해당 줄만 고친다(주석·구조 보존) |

## 2. 전체 프로세스

작업의 뼈대. **포맷 처리기·유틸리티를 자동으로 엮어 돌린다** — 어느 프로세스가 무엇을
부르는지는 [BOOTSTRAP.md](BOOTSTRAP.md) 각 절의 표에 있다.

| 명령 | 역할 |
|---|---|
| `ujyu init <dir>` | 타이틀 리포 스캐폴딩 — 폴더 + config·폰트스펙·이미지 manifest·ui_strings 템플릿. 기존 파일은 건너뛴다 |
| `ujyu unpack` | 한글화 대상 아카이브 전부 언팩 + `.scn` 디스어셈블 (`axr unpack`·`scn disasm`·`axr list`) |
| `ujyu extract` | 시나리오 텍스트·common.csv 문자열·exe 문자열·이미지 목록·화자명 대응표 추출 (`scn extract`·`csv todo`·`nameplates`) |
| `ujyu build [단계…]` | 배포 조립 — `exe`/`scenario`/`title`/`cg`/`movie` 중 골라(생략=all), `--no-<단계>` 로 제외 |
| `ujyu release` | 원본 대비 diff 로 인스톨러 패키지 생성 (바뀐 파일 + 해시 검증 `install.py`) |

## 3. 포맷 처리기

`ujyu.formats.*` 라이브러리를 부르는 얇은 래퍼. 정찰·디버깅·부분 작업에 쓴다.

| 명령 | 역할 |
|---|---|
| `ujyu axr list\|unpack\|repack` | AXRe 아카이브. `list` 는 풀지 않고 엔트리·크기·종류(VNEG/PNG/DMJ0) |
| `ujyu scn extract\|disasm\|relocate` | VNEG(.scn) — 텍스트 추출 / 디스어셈블 / 점프테이블 재매핑 |
| `ujyu dmj info\|frames\|video\|export\|mjpeg\|encode` | DMJ0 무비 디코드·인코드 |
| `ujyu adp info\|decode` | ADPx 오디오 정보 확인 / PCM16 WAV 디코드 |
| `ujyu csv show\|get\|todo` | 아카이브 안 common.csv 조회 |

### 포맷 라이브러리 (`../ujyu/formats/`)

포맷 하나 = 모듈 하나, 동일 골격(구조 → parse → serialize → 상위연산). 타이틀 설정에
의존하지 않는 순수 엔진 계층. 상위 리포는 `from ujyu.formats import …` 로 쓴다.

| 모듈 | 역할 |
|---|---|
| `ujyu.formats.axr` | AXRe 아카이브 — `load` / `getfile` / `pack` |
| `ujyu.formats.vneg` | VNEG(.scn) — `disasm`(심볼 전체·점프테이블·flow) / `extract` / `relocate_jumptable` |
| `ujyu.formats.dmj` | DMJ0 무비 — `DMJ`(decode) / `frame_to_jpeg` / `to_mjpeg` |
| `ujyu.formats.adp` | ADPx 오디오 — `parse_header` / `decode` / `decode_file` |
| `ujyu.formats.common_csv` | common.csv — `fields` / `get_field` / `set_field` |

## 4. 유틸리티

프로세스 명령이 부르거나, 사람이 직접 쓰는 도구.

**exe** (전부 원본 exe 하나에서)

| 명령 | 역할 |
|---|---|
| `ujyu exe` | **전체 패치** — UI·엔진 바이트·코드케이브 |
| `ujyu exe scan` | **오프셋 후보 스캔** — config 스니펫 출력(검증 타이틀 21/22 적중, 오탐 0) |
| `ujyu exe ui` | Windows UI 리소스/인라인 문자열 |
| `ujyu exe movie` | 무비 2배 확대 끄기 |
| `ujyu exe fontrestore` | 글꼴 저장/복원 코드 케이브 |
| `ujyu exe disasm` | x86 디스어셈블 분석 (capstone) |

**번역·에셋**

| 명령 | 역할 |
|---|---|
| `ujyu filter` | 번역 데이터 다루기 — `stats`(분류·분량) / `dump`(대상만 TSV) / `context`(씬 전체를 문맥과 함께, 미번역 표시) / `propagate`(같은 원문에 기번역 전파) / `apply`(반영) |
| `ujyu inject` | CP949 인코딩·검증·**전체 시나리오 아카이브 빌드** (유효본 arc별 주입 + 점프테이블 재매핑, 제어코드 보존) |
| `ujyu image` | Markdown 내 JSON manifest 를 읽어 무문자 이미지에 번역 텍스트를 렌더링 |
| `ujyu font` | 게임용 한글 폰트 빌더 (`fonts/<face>.py` 스펙 주도) |
| `ujyu nameplates` | 화자명 대응표 생성. 번역표는 타이틀 종속 — 채워 넣을 것 |
| `ujyu migrate` | 구(휴리스틱) strings.json → v2 이관 + 커버리지 QA (일회성) |

**해상도·기타**

| 명령 | 역할 |
|---|---|
| `ujyu scale` | 해상도 N× — `common`/`center`/`dims`/`cg`/`exe`. `center` 는 스케일 비대상 창(선택지) 가운데 정렬, `cg-export`/`cg --from-dir` 로 외부 AI 업스케일러 연동 |
| `ujyu title` | `common.csv` 창 제목 설정 |
| `ujyu save show\|goto` | 테스트용 세이브 — 특정 씬으로 바로 진입(씬 이름은 같은 길이) |

## 양식·템플릿 (`../samples/`)

| 파일 | 용도 |
|---|---|
| `config.py` | 타이틀 설정 템플릿 (`ujyu init` 이 복사) |
| `font_spec.py` | 폰트 빌드 스펙 (`ujyu font` 입력) |
| `images.sample.md` | 이미지 텍스트 manifest (`ujyu image` 입력) |
| `ui_strings.sample.json` | Windows UI 번역표 (`ujyu exe ui` 입력) |
| `nameplates.sample.json` | 화자명 대응표 (`ujyu nameplates` 입력) |
