# PR triage (`cai/triage.py`)

열린 PR의 사실(head SHA, CI, Draft, 충돌, 리뷰 승인)을 결정적으로 모아 관리 라벨을 맞춘다. 사실이 바뀌었을 때만
모델이 제안한 우선순위·다음 조치를 검증해 봇 소유 보고서 이슈 하나에 게시한다. PR을 병합·종료하거나 브랜치를
고치지 않는다.

- **원본:** `smarandache-wellin-lean`의 `scripts/pr_triage.py`(sha256 `33438382…`), 시험
  `tests/test_pr_triage.py`(`47dab9a7…`).
- **v0의 변경:** 저장소별 값을 `TriageConfig`로 받게 한 것뿐이다. GitHub I/O는 원본처럼 `gh` CLI를 쓴다.

## 설정 `TriageConfig`

| 필드 | 기본값 (SW 설정) | 규칙 |
| --- | --- | --- |
| `label_namespace` | `pr-triage/` | 영숫자로 시작하고 `/`로 끝난다. 가장 긴 라벨 이름이 50자(GitHub 상한) 이하. |
| `report_title` | `PR 현황 보고서 (자동)` | 앞뒤 공백 없는 한 줄, 1–256자 |
| `required_checks` | `("Lean build and audits",)` | 서로 다른 한 줄 이름이 하나 이상. ruleset의 필수 검사 이름과 맞춘다. |
| `marker_id` | `pr-triage` | `[a-z0-9]`와 단일 `-`만. HTML 주석 안에 `--`가 생기지 않는다. |

파생 이름:
- 관리 라벨: `namespace + {ready, needs-review, changes-requested, ci-failing, ci-pending, conflict,
  blocked, draft, unknown}`
- 보고서 라벨: `namespace + report`
- 보고서 마커: `<!-- {marker_id}-report:v1 -->`
- digest 마커: `<!-- {marker_id}-digest: <64 hex> -->`

라벨 색과 설명, 보고서 본문 문구(한국어)는 v0에서 고정이다.

**버전 있는 JSON 설정** (`TriageConfig.from_mapping`): 모르는 키, 다른 `version`, 배열이 아닌
`required_checks`는 거부한다. 빠진 키는 기본값이다.

```json
{"version": 1, "label_namespace": "pr-triage/", "report_title": "PR 현황 보고서 (자동)",
 "required_checks": ["Lean build and audits"], "marker_id": "pr-triage"}
```

같은 저장소에 레인이 둘 이상이면 `label_namespace`와 `marker_id`를 모두 다르게 둔다. 보고서 이슈는 다음을 모두
만족해야 재사용된다:
- 봇 작성(`app/github-actions`, `github-actions[bot]`, `github-actions`)
- 열린 상태
- 보고서 라벨, 제목, 보고서 마커 일치

그래서 다른 레인의 보고서를 고르지 않는다.

## API

모든 함수는 마지막 인자 `cfg: TriageConfig = DEFAULT`를 받는다. 기본값으로 부르면 원본과 같다.

- 분류: `check_state(check)`, `ci_state(checks, cfg)`, `normalize_pr(raw, cfg)`, `classify(pr, cfg)`,
  `managed(labels, cfg)`, `plan_labels(pr, cfg)`
- 사실 digest: `facts_digest(prs)`. 설정과 무관하다. 단, `ci` 값은 `required_checks`로 정해진다.
- 보고서: `find_report_issue(issues, cfg)`, `parse_digest(body, cfg)`, `render(snapshot, items, run_url, now, cfg)`
- 모델 출력: `validate_ai(ai, snapshot)`. 우선순위 열거값 `높음/보통/낮음`은 고정이다.
- 실행 단위:
  - `collect(gh, cfg) -> snapshot`
  - `publish(gh, snapshot, ai, run_url, now, cfg) -> "published" | "labels-only" | "already-published"`
    또는 `Stale`
- GitHub 래퍼: `GH(repo, dry_run=False, runner=..., cfg=DEFAULT)`. `gh`의 인자 목록은 원본과 같다.

스냅샷 키:
- 원본과 같은 것: `version`, `repo`, `digest`, `prs`, `report_issue`, `facts_changed`, `label_drift`
- 새 키: `config_id`(설정 JSON의 sha256)

## 멱등과 stale (원본과 같음)

- 사실 digest가 보고서 마커의 digest와 같고 라벨도 맞으면 `already-published`다. 쓰기와 모델 호출이 없다.
- 사실은 같은데 라벨만 어긋나면 `labels-only`다. 모델 없이 관리 라벨만 복구하고, 관리 밖 라벨은 건드리지 않는다.
- 게시 직전 다시 수집한 digest가 스냅샷과 다르면 `Stale`이다. 아무것도 쓰지 않고, CLI는 종료 코드 3을 낸다.
- 봇 소유 보고서가 여럿이거나 목록이 상한(PR 100, 보고서 라벨 이슈 20)을 넘으면 `TriageError`(종료 1)다.
  잘린 목록으로는 게시하지 않는다.

## v0에서 더한 안전장치

- **설정 일치:**
  - `publish`는 스냅샷의 `config_id`가 게시 설정과 같아야 진행한다. 쓰기 전에 `ValueError`로 거부한다.
  - `config_id`가 없는 스냅샷은 원본 스크립트가 만든 것으로 보고 기본 설정과만 맞는다.
  - `GH(cfg=…)`와 함수의 `cfg`가 다르면 `collect`가 거부한다.
- **설정 오류:** CLI는 GitHub 래퍼를 만들기 전에 종료 코드 1로 끝난다. `--config`와 개별 인자를 함께 쓰는 것도
  설정 오류다.

## CLI

```sh
python3 -I cai/triage.py collect  --repo O/R --out snap.json [--github-output F] [설정]
python3 -I cai/triage.py validate --snapshot snap.json --ai ai.json
python3 -I cai/triage.py publish  --repo O/R --snapshot snap.json [--ai ai.json] [--run-url U] [--dry-run] [설정]
# 설정: --config triage.json  또는  --label-namespace NS/ --report-title T --required-check C ... --marker-id ID
```

- 종료 코드:
  - 0: 성공
  - 1: 설정 오류, 수집 불가, 검증 실패, 설정 불일치
  - 3: stale
- workflow는 원본처럼 publish의 3을 "다음 실행이 다시 수집"으로 보고 성공 처리해야 한다. 원본의
  `tests/test_pr_triage_shell.py`가 이 `bash -e` 처리를 시험했다. 그 시험은 workflow 파일을 읽으므로 kit의
  workflow 쪽(Root 소유)으로 옮겨야 한다.
- collect와 publish에는 같은 설정 인자를 넘긴다.

## 검증 결과 (2026-11-10)

- **SW 원본 시험 25개:** `tests/test_triage_sw_compat.py`로 옮겼다. 원본과 다른 곳은 docstring과 import 경로 두 곳
  뿐이고, 25개 모두 통과한다. 26번째인 셸 시험은 위에 적은 대로 workflow 쪽으로 옮길 대상이다.
- **설정 시험 17개:** `tests/test_triage_config.py`. 다룬 내용은 다음과 같다.
  - 기본값의 원본 상수, 원본으로 계산한 golden digest와 보고서 본문 sha256
  - 다른 namespace·제목·필수 검사·마커
  - 다른 레인의 라벨·보고서를 건드리거나 재사용하지 않음
  - 사용자 설정에서의 멱등과 stale
  - 설정 검증과 불일치 거부
  - CLI 종료 코드
- **차분 시험 (저장소 밖 scratch):** 원본과 `cai/triage.py`(기본 설정)를 무작위 3000개 시나리오(PR 8,969건)로
  비교했다. 불일치는 0건이다. 비교 대상은 `normalize_pr`, `classify`, `plan_labels`, `collect`(`config_id` 제외),
  `publish`의 결과와 mutation 목록, `validate_ai`, `render`, `gh` 인자 목록, 모듈 상수다.

## 알려진 한계 (v0)

- **사실 digest에 설정이 들어가지 않는다.** namespace를 바꾸면 라벨은 `label_drift`로 고쳐지지만, 보고서 본문은
  사실이 바뀔 때까지 다시 쓰지 않는다. `marker_id`를 바꾸면 새 보고서를 만들고 옛 보고서는 닫지 않는다.
  정리는 사람이 한다.
- **보고서 문구, 라벨 설명, 우선순위 값은 한국어로 고정이다.**
- **원장 신뢰 근거:** 봇 작성 여부는 "저장소 쓰기 권한"을 뜻할 뿐 이 레인을 뜻하지 않는다. 형식 검사와
  fail-closed로 피해를 줄인다.
- **GITHUB_TOKEN으로 쓴 라벨·이슈는 다른 workflow를 깨우지 않는다.** 연쇄 실행이 없으며, 의도한 동작이다.
