# studio-miris-engine

Studio Miris(すたじおみりす) VN 엔진 — **AXRe 아카이브 + VNEG 스크립트** — 의
한글 패치·리버싱 **재사용 도구와 지식**. 특정 타이틀에 종속되지 않는 엔진 계층만 담는다.

> **엔진 식별**: 아카이브 첫 4바이트 `AXRe`, 스크립트(`.scn`) 첫 4바이트 `VNEG` 이면
> 이 도구로 한글 패치가 가능할 것으로 예상된다(`ujyu axr list` 로 확인).
> 검증 타이틀: 神無ノ鳥 (2002).

## 시작하기

```bash
mkdir <title> && cd <title>
git init && git submodule add <이 리포 URL> engine
pip install -e engine          # `ujyu` 명령 설치
ujyu init . --title "<타이틀>"  # config.py + 폴더 + 템플릿
ujyu inspect                   # 다음에 할 일
```

전체 순서는 [docs/BOOTSTRAP.md](docs/BOOTSTRAP.md).

## 문서

읽는 순서대로:

| 문서 | 내용 |
|---|---|
| **[docs/BOOTSTRAP.md](docs/BOOTSTRAP.md)** | **어떤 명령을 어떤 순서로** — init → unpack → extract → [번역] → build → release. 각 프로세스가 안에서 무엇을 부르는지, 어느 config 가 채워지는지 |
| **[GUIDE.md](GUIDE.md)** | **어떻게 일하는가** — 문답/분석/자동으로 갈리는 결정 주체, 작업 원칙, 검증 방법, 증상별 진단, 실무 함정 |
| **[SKILL.md](SKILL.md)** | **엔진은 어떻게 생겼는가** — 주소가 아닌 "찾는 법" 중심의 정본 지식(Claude 스킬 형식) |
| [docs/COMMANDS.md](docs/COMMANDS.md) | 명령 전체 목록 (설정 관리기 / 전체 프로세스 / 포맷 처리기 / 유틸리티) |
| [docs/formats/](docs/formats/) | 포맷·엔진 구조 스펙 — 구조의 **단일 출처** |
| [docs/CLI_STYLE.md](docs/CLI_STYLE.md) | 명령을 추가·수정할 때의 인자·도움말 규칙 |
| [docs/UPSCALE.ja.md](docs/UPSCALE.ja.md) | **日本語** — 画面を大きくする手順（번역과 무관하게 해상도만 올리려는 일본어 사용자용） |

포맷 스펙: [AXR](docs/formats/AXR.md)(아카이브) · [VNEG](docs/formats/VNEG.md)(스크립트) ·
[MOVIE](docs/formats/MOVIE.md)(DMJ0) · [COMMON_CSV](docs/formats/COMMON_CSV.md)(설정) ·
[TEXT_RENDER](docs/formats/TEXT_RENDER.md)(CP949 렌더·폰트) ·
[WINDOWS_UI](docs/formats/WINDOWS_UI.md)(OS UI) · [RESOLUTION](docs/formats/RESOLUTION.md)(N× 확대) ·
[SAVE](docs/formats/SAVE.md)(세이브)

## 코드

| 위치 | 내용 |
|---|---|
| [ujyu/formats/](ujyu/formats/) | **포맷 라이브러리** — 포맷 하나 = 모듈 하나, 동일 골격(구조 → parse → serialize → 상위연산). 타이틀 설정에 의존하지 않는 순수 엔진 계층. `axr` · `vneg` · `dmj` · `common_csv` |
| [ujyu/](ujyu/) | **명령 구현**. 포맷 CLI 는 위 라이브러리의 얇은 래퍼, 나머지는 타이틀 설정(`config.py`)에 결합된 한글 패치 작업 |
| [samples/](samples/) | 타이틀별 양식 — `config.py` · `font_spec.py` · `images.sample.md` · `ui_strings.sample.json` · `nameplates.sample.json` |
| [tests/](tests/) | `pytest -q tests` |

타이틀별 실제 작업(번역 데이터·해상도 설정 등)은 이 리포를 **서브모듈로 갖는 상위 리포**에서
한다. 예: [kannagi-no-tori](https://github.com/mirisujyu/kannagi-no-tori).
