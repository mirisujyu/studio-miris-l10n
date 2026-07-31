# 이미지 텍스트 manifest **템플릿**

타이틀 리포의 `translation/IMAGES.md` 로 복사해 채운다. `config.IMAGE_SPEC` 이 이 파일을
가리키고, `ujyu image` 가 문서 끝의 `image-text-manifest` JSON 블록을 직접 읽는다.
중간 변환 파일은 없다 — **이 문서가 이미지 렌더 설정의 단일 원본이다.**

```
ujyu image --list                  # 글꼴 변형 목록
ujyu image --check                 # manifest·입력·글꼴만 검사 (렌더 안 함)
ujyu image --variant <글꼴변형>     # 한 변형 렌더
ujyu image --all                   # 모든 변형 렌더
ujyu image --spec samples/images.sample.md --check   # 이 템플릿 검증
```

## 경로는 여기 적지 않는다

물리 경로는 전부 타이틀 리포 `config.py` 에서 온다. manifest 에는 **파일명만** 쓴다.

| config 키 | 뜻 |
|---|---|
| `IMAGE_ORIGINAL_DIR` | 원본 이미지(일본어 글자 있음). 측정 기준 · `copy_original` 입력 |
| `IMAGE_TEXTLESS_DIR` | 글자를 지우고 배경을 복원한 무문자 베이스. **렌더 입력이자 대상 파일 목록** |
| `IMAGE_FONT_DIR` | manifest 의 `regular`/`bold` 글꼴 파일이 있는 폴더 |
| `IMAGE_TEXTED_PREFIX` | 출력 폴더 접두. 실제 출력 = `접두 + 글꼴변형명` |
| `IMAGE_VARIANT` | `--variant` 를 안 줬을 때 쓸 기본 글꼴 변형 |

번역 대상 집합은 **무문자 폴더에 있는 PNG** 다. 원본에만 있고 무문자에 없는 파일은
번역하지 않기로 한 것이므로 manifest 에 넣지 않는다(넣으면 `--check` 가 걸러낸다).

## 좌표·측정 규칙

- bbox 표기는 `(좌상 x, y) - (우하 x, y)`, 우하 좌표는 미포함.
- 표에 적는 bbox 는 여유 영역이 아니라 **원본에서 실제로 보이는 글자·외곽선의 픽셀 바운딩 박스**다.
- `text` 의 `x`/`y` 는 렌더된 글자 비트맵(여백을 잘라낸 뒤)의 **좌상단을 붙일 좌표**다.
  글꼴마다 획 두께가 달라 같은 bbox 라도 변형별로 값이 조금씩 달라진다.
- 글자가 캔버스 밖으로 나가면 렌더가 에러로 멈춘다. 줄바꿈·잘라내기 대신 **`size` 를 줄인다.**

## operation 종류와 필드

`operations` 는 **위에서 아래로 순서대로** 같은 캔버스에 합성된다. `id` 는 문서 전체에서
고유해야 한다(관례: `<파일>.<번호>`).

| type | 하는 일 | 필수 | 선택 |
|---|---|---|---|
| `text` | 가로 한 줄 렌더 | `file` `text` `render.<변형>.{x,y,size}` | `fill` `stroke` `stroke_width` `weight` `render.<변형>.tracking` |
| `vertical_text` | 한 글자씩 세로로 쌓아 `box` 중앙에 배치 | `file` `text` `box` `size` | `fill` `stroke` `stroke_width` `weight` `gap` `character_scale` |
| `copy_original` | 원본 이미지의 한 영역을 그대로 덮어 붙임(번역 안 할 로고·저작권 표기) | `file` `source_box` | `destination` (기본: `source_box` 좌상단) |
| `line_relative` | 앞선 op 의 폭을 따라가는 가로줄(밑줄) | `file` `relative_to` `center_x` `y` | `width_add` `height` `fill` |
| `resize_from` | 다른 파일을 축소·변환해 이 파일을 만듦(섬네일) | `file` `source_file` `size` | `resample` `convert` |

- `fill`/`stroke` : `"#RRGGBB"` · `"#RRGGBBAA"` · `[r,g,b]` · `[r,g,b,a]`. 기본 `#FFFFFFFF`,
  `stroke` 는 `null` 이면 외곽선 없음. `stroke_width` 는 픽셀.
- `weight` : `"Regular"` 또는 `"Bold"` 만. 글꼴 변형 정의의 같은 이름 파일을 쓴다.
- `tracking` : 자간(픽셀, 실수 가능). 원본이 자간을 벌려 놓은 제목류에 쓴다.
- `character_scale` : 세로쓰기에서 특정 글자만 축소/확대. 예) 조사 `의` 를 작게.
- `resample` : `nearest` / `bilinear` / `bicubic` / `lanczos`(기본).
- `convert` : 저장 전 모드 변환(`"RGB"` 등). 원본이 RGB 인 파일에 맞출 때.
- `source_text` 는 코드가 읽지 않는다. **원문을 남겨 두는 주석용**이므로 꼭 적어 둔다.

## 렌더링 글꼴

`fonts` 배열이 글꼴 변형 목록이다. 여러 개를 두고 렌더해 비교한 뒤, 채택한 폴더를
`config.CG_TRANS_DIR` 에 지정하면 `ujyu build` 가 그 폴더로 아카이브를 만든다.
단일 굵기 글꼴이면 `regular` 와 `bold` 에 같은 파일을 적는다.

| 출력 폴더(접두 뒤) | Regular | Bold |
|---|---|---|
| `SampleSans` | `SampleSans-Regular.ttf` | `SampleSans-Bold.ttf` |
| `SampleSerif` | `SampleSerif-Regular.ttf` | `SampleSerif-Regular.ttf` |

## 명세 블록

아래 JSON 이 정본이다. 위의 표·설명과 값이 어긋나면 JSON 을 기준으로 삼는다.

<!-- image-text-manifest:start -->
```json
{
  "schema": 1,
  "description": "이미지 텍스트 렌더 manifest 템플릿 - 타이틀 이름으로 바꿔 쓴다",
  "fonts": [
    {
      "name": "SampleSans",
      "regular": "SampleSans-Regular.ttf",
      "bold": "SampleSans-Bold.ttf"
    },
    {
      "name": "SampleSerif",
      "regular": "SampleSerif-Regular.ttf",
      "bold": "SampleSerif-Regular.ttf"
    }
  ],
  "operations": [
    {
      "id": "sample_title.1",
      "type": "text",
      "file": "sample_title.png",
      "source_text": "設定",
      "text": "설정",
      "fill": "#F5F5F0FF",
      "stroke": "#050505FF",
      "stroke_width": 2,
      "weight": "Regular",
      "render": {
        "SampleSans": { "x": 40, "y": 38, "size": 30, "tracking": 1 },
        "SampleSerif": { "x": 41, "y": 38, "size": 29, "tracking": 1 }
      }
    },
    {
      "id": "sample_title.rule",
      "type": "line_relative",
      "file": "sample_title.png",
      "relative_to": "sample_title.1",
      "center_x": 320,
      "y": 78,
      "width_add": 8,
      "height": 1,
      "fill": "#F2F2EEFF"
    },
    {
      "id": "sample_button_off.1",
      "type": "text",
      "file": "sample_button_off.png",
      "source_text": "戻る",
      "text": "뒤로",
      "fill": "#FFFFFFFF",
      "stroke": "#000000FF",
      "stroke_width": 1,
      "weight": "Bold",
      "render": {
        "SampleSans": { "x": 2, "y": 2, "size": 22, "tracking": 0 },
        "SampleSerif": { "x": 2, "y": 3, "size": 21, "tracking": 0 }
      }
    }
  ]
}
```
<!-- image-text-manifest:end -->

세로쓰기·원본 복사·섬네일까지 쓰는 경우의 예시(필요할 때 위 `operations` 에 붙여 쓴다):

```json
{
  "id": "sample_logo.vertical",
  "type": "vertical_text",
  "file": "sample_logo.png",
  "source_text": "神無ノ鳥",
  "text": "칸나기의새",
  "box": [483, 82, 532, 246],
  "size": 38,
  "fill": "#ED1C24FF",
  "stroke": "#C6C0BDFF",
  "stroke_width": 1,
  "gap": 2,
  "character_scale": { "의": 0.7 }
}
```

```json
{
  "id": "sample_logo.copyright",
  "type": "copy_original",
  "file": "sample_logo.png",
  "source_box": [350, 458, 625, 480],
  "destination": [350, 458]
}
```

```json
{
  "id": "sample_thumb.derived",
  "type": "resize_from",
  "file": "sample_thumb.png",
  "source_file": "sample_logo.png",
  "size": [78, 58],
  "resample": "lanczos",
  "convert": "RGB"
}
```
