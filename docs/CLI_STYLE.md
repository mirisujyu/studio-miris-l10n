# ujyu CLI 스타일 가이드

`ujyu` 명령의 **체계·인자·`--help` 규칙**. 명령을 새로 만들거나 옵션을 더할 때 이 문서를 따른다.

## 0. 명령 체계 — 대상별로 묶는다

**모듈 하나 = 최상위 명령 하나로 두지 않는다.** 사용자는 파일 구조가 아니라 *무엇을 다루는지*로 명령을 찾는다. 최상위는 **대상**이고, 그 아래가 동작이다.

| 대상 | 최상위 | 하위 |
|---|---|---|
| 파일 포맷 | `axr` `scn` `dmj` `csv` | `unpack`/`extract`/`encode` … |
| exe 패치 | `exe` | (기본=전체) `ui` `movie` `fontrestore` `disasm` |
| 번역 작업 | `filter` `inject` `image` `font` `nameplates` `migrate` | `stats`/`dump`/`check` … |
| 빌드·해상도 | `build` `scale` `title` | `cg`/`common`/`center` … |

- **같은 대상을 다루면 한 명령 아래로.** 무비 인코더가 `dmj-encode` 로 따로 있으면 안 된다 → `dmj encode`. exe 를 건드리는 것은 전부 `exe` 아래로.
- **이름이 비슷한데 대상이 다르면 반드시 갈라 놓는다.** `disasm`(VNEG 스크립트) vs `disasm-x86`(exe) 처럼 헷갈리는 짝은 `scn disasm` / `exe disasm` 으로 대상을 앞세운다.
- **한 동작에 경로는 하나.** 같은 일을 하는 최상위 명령과 하위 명령이 공존하면 안 된다.
- **옛 이름은 숨은 별칭으로 유지한다** (`cli.py` 의 `_ALIAS`). 문서·`--help` 목록에는 새 경로만 싣는다. 깨뜨리지 말고 조용히 넘겨준다.

## 1. 인자

- **argparse 만 쓴다.** 날 `sys.argv` 인덱싱 금지. `main()` 은 `parse_args()` 결과만 본다.
  ```python
  ap = argparse.ArgumentParser(prog="ujyu exe ui", description="<한 줄 설명>")
  ```
- **`prog` 는 전체 명령 경로를 그대로 적는다** (`"ujyu exe ui"`). 디스패처가 `sys.argv[0]` 을 덮어쓰므로 자동으로 맞춰지지 않는다.
- **모듈 계약**: `def main():` 를 두고 파일 끝은 `if __name__ == "__main__": raise SystemExit(main())`. 디스패처는 이 `main()` 만 부른다.
- **필수 입력 = positional.** 소문자·의미 있는 이름(`src`, `scn`, `out_dir`, `archive`). 경로는 positional.
- **선택 = `--kebab-case`.** 단축은 흔한 것만(`-o`, `-v`).
- **하위 동작이 있으면 `subparsers`.** 하위 이름은 동사(`unpack`, `dump`, `apply`, `encode`).
- **불리언 제외는 `--no-<x>`** (`ujyu build all --no-exe`).
- **새 옵션을 만들 때 이름을 통일한다** (기존에 없다고 다른 이름을 지어내지 말 것):

  | 뜻 | 이름 |
  |---|---|
  | 출력 경로 | `--out` / `-o` |
  | 상세 로그 | `--verbose` / `-v` |
  | 원본(무패치) 폴더 | `--orig` (기본 = `config.ORIG_DIR`) |
  | 배포 폴더 | `--game` (기본 = `config.GAME_DIR`) |
  | 해상도 배율 | `--scale N` (기본 = `config.SCALE`) |
  | 쓰지 않고 점검만 | `--check` |

- **기본값은 `config` 에서 온다.** CLI 층에서 경로·오프셋을 하드코딩하지 않는다.
- **리팩토링 때 옵션을 새로 만들지 않는다.** 인자 개수·기본값·의미를 그대로 옮기는 것이 원칙이다.

## 2. `--help` 텍스트

- **`description` = 한국어 한 줄, 명령형.** `docs/COMMANDS.md` 표 문구와 같은 톤("AXRe 아카이브 언팩/리팩").
- **모든 인자에 한국어 `help=` 한 줄.** 기본값이 config 에서 오면 밝힌다(`(기본: config.GAME_DIR)`).
- **비자명하면 `epilog` 에 예시 1~2줄.** `argparse.RawDescriptionHelpFormatter` 로 줄바꿈 보존.
- **모든 명령에 `--help` 가 있어야 한다.** 인자를 받지 않는 도구(`ujyu exe`, `ujyu nameplates`)도 파서를 만들어 `parse_args()` 를 부른다.

## 3. 출력·종료 코드

- 성공 0. 실패는 `raise SystemExit("<한국어 에러>")`. argparse 사용법 에러는 2(기본).
- 사람이 읽는 로그는 stdout, 경고는 `⚠` 접두.
- **콘솔은 CP949 다. print 하는 문자열에 CP949 에 없는 문자를 넣지 말 것.**
  특히 em 대시 `—`(U+2014)·en 대시 `–`·카타카나 장음 `ー` 는 인코딩이 안 돼 **그 자리에서 죽는다**. 대시가 필요하면 `-` 나 전각 대시 `―`(U+2015)를 쓴다.
  일본어 원문 등 CP949 밖 문자를 확인해야 하면 UTF-8 파일로 쓰고 읽는다.

## 4. 파괴적 명령

- 원본(`ORIG_DIR`)은 **읽기 전용**이다. 쓰는 곳은 `GAME_DIR`·작업 폴더뿐.
- 덮어쓰는 동작(빌드·주입·패치)은 **인자 없이 우연히 실행되지 않게** 한다. 기본 동작이 파괴적이면 `--help` 에 무엇을 덮어쓰는지 한 줄로 밝힌다.

## 5. 디스패처(`cli.py`)

- `ujyu` / `ujyu --help` → **카테고리별** 목록 + 각 한 줄 설명, 끝에 `'ujyu <명령> --help' 로 상세 보기.`
- `ujyu <그룹> --help` → 그 그룹의 하위 목록.
- 알 수 없는 명령 → `알 수 없는 명령: <x>` + 전체 목록, 종료 코드 1.
- 디스패처는 파싱하지 않는다. 명령(+하위)만 떼어 모듈 `main()` 에 넘긴다.

```
$ ujyu --help
ujyu - Studio Miris 엔진 한글 패치 도구

포맷:
  axr         AXRe 아카이브 언팩/리팩
  scn         VNEG(.scn) 스크립트: extract / disasm / relocate
  dmj         DMJ0 무비: info / frames / video / export / mjpeg / encode
  csv         아카이브 안 common.csv 조회/편집

exe 패치:
  exe         exe 전체 패치(UI·엔진·코드케이브). 하위: ui / movie / fontrestore / disasm
...
'ujyu <명령> --help' 로 상세 보기.
```
