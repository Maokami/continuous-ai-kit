# PR triage (`cai/triage.py`)

열린 PR의 사실(head SHA, CI, Draft, 충돌, 리뷰 승인, 미해결 리뷰 대화, 리뷰 봇의 head 리뷰 여부)을 결정적으로
모아 관리 라벨을 맞춘다. 사실이 바뀌었을 때만
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
| `review_bots` | `()` (선택) | 현재 head commit을 리뷰해야 하는 봇의 REST login 목록(예: `chatgpt-codex-connector[bot]`). 서로 다르고 10개 이하. 비어 있으면 `needs-bot-review`를 붙이지 않는다. |
| `review_state_markers` | `()` (선택) | 봇이 자기 PR 코멘트에 남기는 리뷰 상태 표식 중 읽을 것. 현재 고를 수 있는 값은 `codex-review-state:v1` 하나다. `review_bots`와 함께만 쓴다. |

파생 이름:
- 관리 라벨: `namespace + {ready, needs-review, changes-requested, unresolved-review, needs-bot-review,
  ci-failing, ci-pending, conflict, blocked, draft, unknown}`
- 보고서 라벨: `namespace + report`
- 보고서 마커: `<!-- {marker_id}-report:v1 -->`
- digest 마커: `<!-- {marker_id}-digest: <64 hex> -->`

라벨 색과 설명, 보고서 본문 문구(한국어)는 v0에서 고정이다.

**버전 있는 JSON 설정** (`TriageConfig.from_mapping`): 모르는 키, 다른 `version`, 배열이 아닌
`required_checks`·`review_bots`·`review_state_markers`는 거부한다. 빠진 키는 기본값이다. `review_bots`가 비어 있으면 `as_dict()`에서
빠지므로 기존 설정의 `config_id`는 그대로다.

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

## 리뷰 대화와 리뷰 봇 사실

`GH.list_prs`는 `gh pr list` 다음에 읽기 전용 GraphQL 질의 하나로 PR 20개씩 `reviewThreads(first: 100)`와
`reviews(last: 100)`를 읽어 각 PR에 `reviewData`로 붙인다. 스레드가 100개를 넘는 PR은 cursor로 다음 쪽을 더
읽어 PR당 500개(5쪽)까지 본다. `totalCount`는 해결된 스레드도 세기 때문이다. 질의 본문에 들어가는 값은 검증한
PR 번호뿐이고, owner·저장소 이름·PR 번호·cursor는 GraphQL 변수다. `gh` 실패(rate limit 포함), GraphQL 오류,
`repository` 누락은 `TriageError`(종료 1)다.

`normalize_pr`가 PR 사실에 더하는 `review` 항목:

| 키 | 뜻 |
| --- | --- |
| `unresolved` | `isResolved == false`인 스레드 수 |
| `unresolved_bot` | 그중 첫 코멘트 작성자가 GitHub App 봇(`__typename == "Bot"`)인 수 |
| `threads` | 미해결 스레드 처음 10개의 `{path, author, bot}`. 경로는 200자에서 자른다. 코멘트 본문은 읽지 않는다. 수는 `unresolved`가 정확히 센다. |
| `threads_complete` | 스레드가 500개를 넘거나 응답이 형식에 맞지 않으면 `false` |
| `head_reviewed` | `review_bots`의 login마다 `true`(그 봇의 review `commit.oid`가 head이거나, 그 봇의 코멘트 속 표식이 head를 가리킴), `false`(받은 review와 코멘트 어디에도 없음), `null`(판단 불가) |

- GraphQL은 봇 login에 `[bot]`을 붙이지 않는다. `_actor`가 REST 형식으로 맞춘 뒤 `review_bots`와 대소문자 구분
  없이 비교한다. 같은 이름의 사람 계정은 봇으로 치지 않는다.
- `head_reviewed`가 `null`인 경우: head를 모름, PR 목록 이후 head가 바뀜, review가 100개를 넘어 앞부분을 못 받았는데
  최근 100개에 없음, GitHub가 PR 객체를 주지 않음.
- 판정 근거는 review의 `commit.oid`와, `review_state_markers`를 켠 경우 봇 코멘트의 리뷰 상태 표식이다.
  반응(👍)과 코멘트의 다른 부분은 쓰지 않는다.

### 리뷰 상태 표식 (opt-in)

Codex는 지적할 것이 없으면 review를 남기지 않는다. 대신 PR에 요약 이슈 코멘트를 하나 두고, 그 첫머리의 기계 판독용
표식을 갱신한다(2026-11-18 consumer PR에서 확인한 형식):

```
<!-- codex-pull-request-review-summary -->
<!-- codex-review-state:v1 {"code_review":{"commit_sha":"<40 hex>","has_any_findings":false,"has_unresolved_findings":null,"status":"completed"},"security_review":{...}} -->
```

`review_state_markers: ["codex-review-state:v1"]`이면 다음을 모두 만족할 때 그 봇이 head를 리뷰한 것으로 본다.
- 코멘트 작성자가 `review_bots`의 봇이다(GraphQL `__typename == "Bot"`). 사람이나 다른 봇이 같은 표식을 써도 무시한다.
- 본문 첫 4,096자 안에 `<!-- codex-review-state:v1 {JSON} -->`이 한 줄로 있고, JSON이 2,000자 이하이며 파싱된다.
- `code_review.status == "completed"`이고 `code_review.commit_sha`가 PR 목록의 head SHA와 글자 그대로 같다.

이 밖의 경우(표식 없음, 깨진 JSON, 길이 초과, 다른 commit, 진행 중)는 "리뷰 안 함"이다. 코멘트는 PR마다 최근
50개만 읽고, 이 질의는 `review_state_markers`가 있을 때만 보낸다. 본문은 수집 단계 안에서만 쓰고 스냅샷·모델 입력·
보고서에는 표식 판정 결과(`head_reviewed`)만 남는다. 표식 이름마다 JSON 안 어느 절을 읽을지는
`REVIEW_STATE_MARKERS`에 코드로 고정되어 있어, 설정으로 임의의 표식을 추가할 수 없다.

라벨:
- `unresolved-review`: `unresolved > 0`
- `needs-bot-review`: `head_reviewed`에 `false`가 하나라도 있음
- `unknown`: `threads_complete == false` 또는 `head_reviewed`에 `null`이 있음
- 위 두 라벨 중 하나라도 있으면 다른 사실이 모두 좋아도 `ready`나 `blocked`를 붙이지 않는다.

보고서에는 `미해결 대화`(예: `2 (봇 2)`)와 `봇 head 리뷰`(예: `name[bot] 대기`, 봇 설정이 없으면 `-`, 120자에서
자름) 열이 붙고, 미해결 스레드가 있으면 표 아래 `미해결 리뷰 대화` 절에 PR마다 경로와 첫 작성자를 10개까지 적는다.
경로와 login은 제목과 같은 방식으로 한 줄로 만들고 표·HTML·멘션·digest 마커를 무력화한다. Markdown 링크·강조는
제목처럼 그대로 남는다.

**본문 길이:** GitHub 이슈 본문 상한은 65,536자다. 스레드 절은 15,000자에서 멈추고 나머지 PR 수만 적는다. PR 100개,
봇 10개, PR마다 미해결 15개·긴 경로인 최악의 입력에서 본문은 약 60,600자다(`tests/test_triage_review.py`).
`publish`는 본문을 라벨보다 먼저 만들고, 상한을 넘으면 아무것도 쓰지 않고 `TriageError`로 끝난다.

`review` 항목은 사실 digest에 들어간다. 스레드가 생기거나 해결되면 사실이 바뀐 것으로 보고 보고서를 다시 쓴다.
게시 직전 다시 읽은 스레드가 다르면 stale(종료 3)이다.

**원본과의 호환:** `reviewData` 키가 없는 raw PR(원본 스크립트의 입력, 원본 시험의 대역)에는 `review` 항목이 없고,
digest·라벨·보고서가 원본과 같다. 실제 `GH` 래퍼는 항상 `reviewData`를 붙인다.

**권한:** GraphQL 질의는 `reviewThreads`와 `reviews`만 읽으므로 collect의 `pull-requests: read`, publish의
`pull-requests: write`로 충분하다. job 권한과 caller 권한은 바뀌지 않는다.

**알려진 한계:**
- 봇이 지적할 것이 없을 때 review를 남기지 않고, 알려진 표식도 쓰지 않으면 `needs-bot-review`가 계속 붙는다.
  Codex는 `review_state_markers`로 처리한다. 다른 봇은 표식 형식을 확인해 `REVIEW_STATE_MARKERS`에 추가해야 한다.
- 표식 형식이 바뀌면(예: `v2`) 판정은 "리뷰 안 함"으로 떨어진다. 라벨이 다시 늘면 형식을 확인한다.
- 스레드의 `isOutdated`는 쓰지 않는다. 낡은 줄에 달린 대화도 해결 처리 전까지 미해결이다.
- 미해결 대화 판정은 끌 수 없다. `review_bots`와 상관없이 미해결 대화가 있으면 `ready`가 빠진다.
- review는 최근 100개만 본다. review가 100개를 넘고 그 안에 봇의 head review가 없으면 `false`가 아니라 `null`이다.
- GraphQL 비용: PR 20개 질의 한 번이 약 20 point다. PR 100개면 collect·publish 한 번씩 합쳐 약 200 point로,
  `GITHUB_TOKEN`의 시간당 GraphQL 한도를 다른 호출과 나눠 쓴다. 하루 몇 번 도는 일정에서는 문제가 없다.

**업그레이드 시 바뀌는 것 (2026-11-18):**
- 관리 라벨이 2개 늘었다. 둘 다 17자 이하라 `label_namespace` 상한(33자)은 그대로다.
- 실제 `GH`로 모은 사실에는 `review`가 들어가므로 digest가 원본 스크립트와 달라진다. 올린 뒤 첫 실행에서 보고서를
  한 번 다시 쓰고 모델을 한 번 부른다.
- 원본 스크립트로 되돌리면 `unresolved-review`, `needs-bot-review` 라벨은 원본이 관리하지 않으므로 PR에 남는다.
  사람이 지운다.

## API

모든 함수는 마지막 인자 `cfg: TriageConfig = DEFAULT`를 받는다. 기본값으로 부르면 원본과 같다.

- 분류: `check_state(check)`, `ci_state(checks, cfg)`, `review_facts(data, head_sha, cfg)`,
  `normalize_pr(raw, cfg)`, `classify(pr, cfg)`, `managed(labels, cfg)`, `plan_labels(pr, cfg)`
- 사실 digest: `facts_digest(prs)`. 설정과 무관하다. 단, `ci` 값은 `required_checks`로,
  `review.head_reviewed`의 키는 `review_bots`로 정해진다.
- 보고서: `find_report_issue(issues, cfg)`, `parse_digest(body, cfg)`, `render(snapshot, items, run_url, now, cfg)`
- 모델 출력: `validate_ai(ai, snapshot)`. 우선순위 열거값 `높음/보통/낮음`은 고정이다.
- 실행 단위:
  - `collect(gh, cfg) -> snapshot`
  - `publish(gh, snapshot, ai, run_url, now, cfg) -> "published" | "labels-only" | "already-published"`
    또는 `Stale`
- GitHub 래퍼: `GH(repo, dry_run=False, runner=..., cfg=DEFAULT)`. 원본에 있던 호출의 `gh` 인자 목록은 같고,
  `review_data(numbers)`의 `gh api graphql` 호출이 더해졌다.

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
#       [--review-bot LOGIN ...]
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

리뷰 대화 사실을 더하기 전 revision의 결과다. 이후 바뀐 점은 위 "업그레이드 시 바뀌는 것"에 있다. `reviewData`가
없는 입력에 대해서는 원본 시험 25개와 golden 값이 그대로 통과한다.

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

## 사용량 한도 복구

Reusable workflow는 [quota recovery](quota-recovery.md)를 사용한다. 한도 초과 시 별도의 봇 소유
복구 이슈에 다음 재개 시각을 저장하고, 이후 trigger에서 현재 PR 사실을 새로 수집한다.
생성 caller의 매시간 schedule은 대기 작업 복구만 수행한다. 대기 중에는 AI 호출이나 보고서 게시가 없다.
수집 job은 수동 `reset_quota` 처리를 위해 `issues: write`를 가지며, AI job은 계속 읽기 전용이다.
