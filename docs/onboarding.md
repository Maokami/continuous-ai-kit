# 새 repo에 적용하기: agent + CLI

현재 지원 범위는 근거 기반 source scan과 PR triage다. [설계와 사례 비교](template-design.md),
[추가 작업 recipe](../skills/adopt-continuous-ai/references/recipes.md)를 보고 목적에 맞는 것부터 선택한다.

## 에이전트 경로

저장소 안의 `skills/adopt-continuous-ai/SKILL.md`를 지정해서 Codex/Claude에게 요청할 수 있다.
선택한 agent의 skill 경로로 `adopt-continuous-ai` 폴더 전체를 복사해 사용해도 된다.
이 패키지는 사용자 전역 설정에 자동 설치하지 않는다. 설치한 skill에도 신뢰하는 Kit checkout 경로를 알려준다.

> adopt-continuous-ai skill을 사용해서 이 repo의 반복 작업을 조사해줘.
> 문서와 코드 사이의 불일치를 줄이고 싶어. 기존 workflow와 겹치지 않는 작업을 제안하고,
> adoption brief와 CLI preview를 만든 뒤 로컬 설정을 생성해줘.

에이전트는 실제 파일·기존 workflow·현재 CI check를 근거로 범위를 좁힌다.
원격 branch/check 이름을 확인할 수 없으면 이를 추측해 배포하지 않는다. 승인·push·dispatch 범위는 사용자 요청을 따른다.

## CLI 경로

Python 3.10+가 필요하다. `KIT`에는 이 onboarding 코드가 들어 있는 **신뢰하는 로컬 checkout**을 지정한다.
아래 `--ref`는 reusable runtime을 고정하는 기존 공개 commit이며, 새 onboarding 파일의 배포를 뜻하지 않는다.
향후 업데이트 때는 package diff를 검토하고 해당 SHA를 함께 변경한다.

```sh
KIT=/path/to/continuous-ai-kit
REPO=/path/to/consumer
python3 -I "$KIT/onboard.py" inspect "$REPO"

python3 -I "$KIT/onboard.py" scan "$REPO" \
  --ref 3b92523aa657b35f64130c2eed4a5446a1b52423 \
  --default-branch main --lane docs-consistency --profile docs \
  --source-glob 'docs/*.md' --policy-file AGENTS.md
```

명령 결과는 JSON이다. `files`는 생성할 경로와 내용, `scope`는 전체 대상 파일·크기·batch 수다.
기본 동작은 preview다. 같은 명령에 `--write`를 더하면 로컬 파일을 생성한다.
`AGENTS.md`가 없으면 해당 옵션을 빼고, 필요한 다른 정책 파일이 있으면 `--policy-file`을 반복한다.
선택한 소스·정책은 HEAD에 commit되어 있고 현재 파일과 일치해야 한다. 생성 파일은 검토 후 commit해야 runtime이 읽는다.

| Profile | 좋은 시작점 | 확인할 검증 |
| --- | --- | --- |
| `docs` | 실제 행동을 잘못 유도하는 사양·설치·운영 문서 모순 | 상충하는 두 근거와 수정 후 확인 방법 |
| `core` | 작은 순수 로직의 계약·경계값·테스트 누락 | 호출부/테스트 대조, 최소 재현 또는 검사 제안; 실행은 별도 |
| `lean` | 중복 증명·가정·의존성·전술 개선 | 정확한 선언, 명제·가정 보존 여부, 후속 build/audit |
| `general` | 다른 텍스트 기반 소스의 한정된 개선 탐색 | repo에 맞게 prompt의 판단 기준 구체화 |

`--source-glob`은 Python `fnmatch` 규칙이며 `*`는 `/`도 포함한다. Git shell glob과 같다고 가정하지 말고
preview의 실제 목록을 확인한다. policy를 source에 겹쳐 넣지 않는다. 생성될 config/prompt까지 포함하는
넓은 glob도 거부한다. 한 lane은 최대 1,000파일·총 2MB,
파일별 250KB다. `--batch-size`는 1–8(기본 4), `--max-turns`는 1–64(기본 32)다.
core의 호출부·테스트, docs의 구현처럼 인용에 필요한 파일도 source glob에 포함한다. policy는 인용 가능한
source를 대신하지 않는다. 선택 범위에 없는 근거가 필요하면 범위를 다시 설계한다.
한 dispatch는 한 batch만 조사한다. scan의 `--model`은 구독에서 사용할 수 있는 모델을 지정한다.
PR triage의 모델·turn 한도는 pinned reusable workflow에 고정되어 있다.

PR triage는 **필수 check의 정확한 이름**을 반복해서 명시한다:

```sh
python3 -I "$KIT/onboard.py" triage "$REPO" \
  --ref 3b92523aa657b35f64130c2eed4a5446a1b52423 \
  --default-branch main --lane pr-triage \
  --required-check 'Unit tests' --required-check 'Lint'
```

`Unit tests`와 `Lint`는 예시다. workflow 파일명이나 job ID를 추측해 넣지 말고 ruleset/최근 PR의 check context와
대조한다. 필수 check가 아직 없는 repo는 먼저 결정적인 CI를 정한다. 판정은 보고서와 관리 라벨로만 사용되며
merge 권한이나 승인이 되지 않는다. 공개/비공개 repo 모두를 위해 caller에 `actions: read`도 포함한다.

기존 config/prompt/workflow 중 하나라도 같은 경로에 있으면 전체 생성을 거부한다. 기존 lane 변경은 일반 코드
리뷰로 처리한다. 새 lane 이름을 붙여 같은 원장에 연결된 중복 schedule을 만들지 않는다.
기존 `install.py` 인터페이스는 호환성을 위해 유지하지만, 새 도입에는 scope preflight가 있는 `onboard.py`를 권한다.

## 실제 도입과 평가

1. [Adoption brief](../skills/adopt-continuous-ai/assets/adoption-brief.md)를 작성한다. 입력·검증·결과 담당자가
   불명확하면 해결할 병목부터 좁힌다. 자동화가 유익하지 않으면 일반 CI 또는 수동 작업을 선택한다.
2. 생성된 diff와 prompt를 검토하고 기존 repo 규칙에 따라 PR로 반영한다. 조직 Actions 정책과 public callee
   접근, 기본 branch를 확인한다. 현재 지원하는 branch 이름은 영숫자·`_`·`.`·`-`로 제한된다.
3. 사용자에게 허용된 절차로 공식 `claude setup-token` 흐름에서 얻은 OAuth token을 consumer의
   `CLAUDE_CODE_OAUTH_TOKEN` Actions secret에 등록한다. 값은 config/로그/채팅에 넣지 않는다.
   절차의 최신 조건은 [공식 문서](https://code.claude.com/docs/en/github-actions)를 확인한다.
4. 기본 branch에서 수동 pilot을 실행한다. 로그의 source/package SHA, validator, 게시된 결과를 확인한다.
   preview는 OAuth, 원격 권한, 모델 응답 품질을 검사하지 않는다.
5. scan은 같은 batch·같은 digest, triage는 같은 사실로 다시 실행해 불필요한 모델 호출·게시가 없는지 확인한다.
   scan에서 `auto`는 **다음 batch**로 갈 수 있으므로 no-op 확인에는 첫 batch 번호를 명시한다.
6. 담당자가 후보별 채택/기각/중복/보류와 검토 시간을 기록한다. 초기에 한두 작업으로 시작하는 것을 권하지만
   사용자 목적에 맞춰 조정한다. 성공 기준을 만족한 뒤에만 일정과 작업량을 늘린다.

실패한 scan은 원인을 진단하고 같은 batch의 명시적 retry 한 번까지만 허용한다. source/policy/package 변경은
새 digest를 만든다. 이를 무제한 재시도 수단으로 쓰지 않는다. schedule 제거와 새 dispatch 중지는 운영자가 한다.
현재 CLI는 schedule을 추가하거나 제거하지 않는다.

기술적 제약은 [security](security.md), [scan](scan.md), [triage](triage.md)에 있다.
시간·비용·정확성의 pilot 결과는 brief의 표에 실제 값으로 남긴다. 빈 후보 결과를 억지로 채우지 않는다.

## 로컬 검증 기록 — 2026-11-16

- Python 3.12에서 기존 시험 90개와 신규 회귀시험 18개, 총 108개 통과.
- 전체 Kit workflow 및 생성한 5개 caller(4 profiles + triage) `actionlint` 통과.
  생성 caller 검사는 `tests/render_callers.py`와 Kit CI에도 연결했다.
- 패키지 skill의 `quick_validate.py` 통과.
- 실제 세 저장소에서 preview만 실행: Kuratowski 50파일/534,162bytes/13batches,
  Smarandache–Wellin 6파일/46,255bytes/2batches, 비공개 Rust 앱의 ADR 8파일/34,486bytes/2batches.
  실행 전후 작업 트리 상태는 동일했다.
- 공식 Claude Code CLI의 읽기 전용 소스 리뷰를 받았다. 생성 입력의 scope 혼입, tuple 인자 호환성,
  Git fsmonitor 실행 가능성, 오류 진단 및 시험 공백을 검토하고 수정했다. fsmonitor는 임시 repo에서
  실제 hook 호출을 먼저 재현한 뒤, onboarding이 호출하지 않는 것을 회귀시험으로 확인했다.
- 이 기록은 로컬 생성·입력 검증의 근거다. 새 onboarding 경로의 실제 OAuth/Actions pilot,
  prompt별 후보 정확성, 채택률은 아직 측정하지 않았다.
