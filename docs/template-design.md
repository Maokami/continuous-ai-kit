# Continuous AI 도입 템플릿 설계 — 2026-11-16

목표는 새 저장소에서 반복되는 실제 병목을 찾아, 근거 있는 제안이 검토·채택되는 루프를 쉽게 만드는 것이다.
에이전트가 저장소를 이해하고 작업을 설계하며, CLI가 그 결정을 재현 가능한 설정으로 만든다.
Claude Code Action + 구독 OAuth는 실행 수단이고, 유용한 작업 선택과 결과의 채택 과정이 설계의 중심이다.

## 검토한 사례와 추출한 원칙

아래는 2026-11-16 조사 시점의 소스, GitHub 상태 및 과거 실행 기록을 대조한 결과다. 모든 과거 세션이나
모든 실행을 재현한 것은 아니다. 비공개 저장소는 공개 가능한 역할 수준으로만 기술한다.
기존 상세 실행 근거는 [adoption-evidence.md](adoption-evidence.md)에 별도로 남아 있다.

| 사례 | 실제로 구성한 루프 | 관찰 범위 | 공통 템플릿에 가져올 것 |
| --- | --- | --- | --- |
| [Kuratowski Lean](https://github.com/Maokami/kuratowski-lean) | Lean 개선 탐색, blueprint/PR 검토, 특정 조건의 병합·브랜치 정리 | 개선 후보의 후속 PR #34 및 활성 주간 탐색 확인; 모든 자동 병합 경로 재실행은 아님 | 정확한 선언·인용, 공개 명제 보존, 현재 head의 CI. 병합 정책은 해당 repo에 유지 |
| [Smarandache–Wellin](https://github.com/Maokami/smarandache-wellin-lean) | 질문, 증명 리뷰, 문헌 탐색, 연구 계획, 승인된 lemma 작업, PR triage | planner #18, 승인 이슈 #19/#20에서 draft PR #23/#24; 연구 결과의 수학적 완결성은 별도 | 제안과 실행 분리, 같은 입력 no-op, 작업량 상한, 단일 현황 보고서 |
| [Continuous AI Kit](https://github.com/Maokami/continuous-ai-kit) | scan/triage 공통 실행·검증·게시 | 두 reusable workflow와 consumer 이전; gh-aw 비교는 compile까지만 | 얇은 caller, SHA pin, 구조화된 출력, 결정적 게시기, 배치·중복·stale 처리 |
| 비공개 Rust 애플리케이션 | specs/ADR/core 탐색, PR triage, advisory/calendar 감시 | 6개 workflow 활성 및 성공 실행; 제안의 실제 가치 전부를 측정한 것은 아님 | 코드 외 설계 문서도 입력, 정확한 literal anchor, 같은 finding 중복 억제. 규칙으로 충분한 감시는 비AI로 |
| 비공개 클러스터 운영 repo | 야간 운영 점검, 주간 roadmap 제안, 알림 | draft PR #9와 과거 dry-run; timer 설치·주기 운영은 미확인 | 운영 사실은 script oracle로 수집, 분석과 실행 분리. systemd 배포는 Actions 템플릿과 별도 |
| 비공개 교재 repo | research/edit/organize, 독자 피드백 수집→분류→작은 수정→draft PR→출판 확인 | lifecycle와 작성 workflow 소스, PR/Actions 기록 확인 | feedback 소유권·fingerprint, 변경 시 재검토, merge와 실제 반영 구분, 범위 제한 |
| 비공개 Lean companion | build/audit CI와 교재↔Lean 대응 관계 점검 | 주간 감사 소스 및 token 개행 수정; 실제 감사 보고 성공은 아직 미확인 | 증명 성립과 교재 주장·구현 대응을 서로 다른 검사로 유지 |
| 비공개 SDK 연구 lab | frozen case, 독립 checker, translation validation·증명 실험 | 7개 기록된 실험과 결과; fork Actions 비활성, 운영 배포 아님 | 검사 대상·명세 적절성·실제 코드 대응을 분리. 실행 시간과 사람 시간 절약을 혼동하지 않음 |

공개 기준 소스는 Kuratowski `1c550f9c4b332a54b92628752508d0b88bea97a7`,
Smarandache–Wellin `1dd94a506e3bd4af9fc328a6cec994b57fb9a771`,
Kit `3b92523aa657b35f64130c2eed4a5446a1b52423`이다. 비공개 사례의 소스나 내부 실험 데이터는 이 패키지로 복사하지 않는다.

## 구조: 에이전트와 CLI가 같은 경로를 사용

```text
사용자의 목적 + repo의 코드/문서/기존 자동화
                  ↓
adopt-continuous-ai skill: 반복할 작업 선정 + adoption brief
                  ↓
onboard.py inspect → scan/triage preview → 검토 → --write
                  ↓
consumer PR: caller YAML + lane JSON + prompt + adoption brief
                  ↓
수동 pilot → 결과 검증 → 담당자 채택/기각 → 효과 확인 → 필요시 주기 실행
```

CLI 직접 사용도 같은 preview/write 경로를 따른다. 별도의 에이전트 전용 생성기나 범용 workflow DSL을 만들지 않는다.
저장소마다 다른 작업은 작은 prompt/profile 또는 별도 adapter로 표현한다. AI가 필요 없는 수집·계산·상태 전이는
일반 CI나 script에 남긴다. 운영체제 scheduler, 외부 feedback 시스템, Lean build는 별도 adapter의 책임이다.

작업마다 [adoption brief](../skills/adopt-continuous-ai/assets/adoption-brief.md)에 다음 계약을 남긴다:
반복 병목, 필요한 판단, 입력과 근거, trigger, 허용 결과, 검증 방식, 담당자, 채택 기준, 비용/시간 예산, 중단 기준.
이 문서는 실행 엔진이 강제하는 정책 파일이 아니다. JSON의 실제 상한, Actions 권한, reviewer의 운영 결정을 구분한다.

## 이번 구현 범위

| 기능 | 상태 | 확인할 결과 |
| --- | --- | --- |
| Agent adoption skill | 패키지에 포함 | 실제 repo 근거로 목적·범위·검증·pilot을 정리하고 CLI 사용 |
| `inspect` | 구현 | Git에 추적된 정책 파일, workflow, 기존 설정을 읽기 전용으로 열거 |
| `scan` profiles: general/docs/core/lean | 구현 | 명시한 scope·정책을 검증하고 수동 실행 caller/JSON/prompt 생성 |
| `triage` | 구현 | 해당 repo의 필수 check 이름을 명시해서 caller/JSON 생성 |
| 파일 충돌·소스 pin·구성 검증 | 구현 | 미리보기부터 실패 이유 확인, 기존 파일을 덮어쓰지 않음 |
| 수동 pilot과 효과 기록 | 절차·양식 제공 | 채택률, 검토 부담, 근거 오류, 비용을 실제 관찰해 기록 |
| 연구 planner·문헌 watch·CI diagnosis·bounded draft writer | 사례별 recipe만 제공 | 별도 adapter 구현 및 검증 전까지 설치 가능한 기능으로 표시하지 않음 |
| 자동 병합·운영 명령 실행 | 공통 패키지 범위 밖 | repo별 명시적 운영 정책과 독립 검증 필요 |

현재 scanner의 kind는 Lean에서 출발한 고정 열거값이다. core profile의 테스트 근거 부족은 `documentation`으로
표현하며 이유에 이를 명시한다. 테스트 실행기나 보안 감사기가 아니다. 새 domain schema는 별도 버전 변경으로 다룬다.

## 후속 작업 순서와 완료 기준

1. **도입 기반을 pilot으로 검증:** 기존 자동화와 충돌하지 않는 새 repo에서 docs 또는 core 한 작업과 필요시 triage를
   수동 실행한다. source SHA, 검증 결과, 실제 보고서, unchanged rerun, 검토자의 채택 여부를 기록한다.
   로컬 테스트 통과만으로 OAuth 인증·Actions 실행·제안 품질이 검증되었다고 하지 않는다.
2. **연구 planner 추출:** SW의 단일 보고서·body hash·승인 상태를 독립 adapter로 추출한다.
   승인되지 않은 task 실행 0건, 동일 입력 no-op, stale 거부, 잘린 조회 거부를 fixture와 실제 pilot으로 검증한다.
3. **피드백→draft PR 추출:** 교재 사례에서 수집기·fingerprint·제안·writer·반영 확인을 분리한다.
   원본 삭제/변경, 중복 요청, 실패한 검증, 병합 후 미출판을 시험하고 PR 수/수정 범위를 제한한다.
4. **CI 진단과 대응 관계 감사:** 실패 로그 또는 spec↔implementation의 기준을 독립적으로 고정한다.
   단순 실패 분류와 원인 증명을 구분하고, 알려진 사례를 숨긴 평가로 근거 정확성을 확인한다.

후속 adapter를 한꺼번에 만드는 대신 실제 다음 consumer의 병목에 맞춰 하나씩 선택한다.
각 단계의 완료는 파일 생성량이나 실행 횟수가 아니라 검증된 결과가 검토·채택되는지로 판단한다.

## 유지할 한계

- source glob은 분석 대상 목록이지 읽기 격리 경계가 아니다. 읽기 권한과 출력 검증은 별도로 검토한다.
- 정확한 인용과 schema 통과는 제안의 진실성·완전성을 보장하지 않는다. 빈 결과도 유효하다.
- scan은 한 실행에 한 batch다. 전체 범위 탐색을 완료했다고 기록하려면 해당 digest의 모든 batch를 확인한다.
- 변경 없는 입력의 재실행을 억제해도 네트워크 실패까지 포함한 exactly-once 게시를 약속하지 않는다.
- 구독 usage, Actions 실행 시간, API 환산 추정치, 실제 청구, 사람 검토 시간은 별도로 기록한다.
- 현재 모델 출력의 Markdown 링크/이미지 전체를 sanitize하는 기능은 없다. 외부 링크의 정확성과 공개 범위도 검토한다.

공식 [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions)와
[Action examples](https://github.com/anthropics/claude-code-action/blob/main/docs/solutions.md)는
실행 도구의 참고 자료다. 이 문서의 사례 성과는 해당 예제가 아니라 위 repo의 관찰에서 도출했다.
