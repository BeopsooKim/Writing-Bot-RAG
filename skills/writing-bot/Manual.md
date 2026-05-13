# Writing Bot Manual

**Version:** v1.0.0  
**Created by:** Beopsoo Kim, Department of Electrical and Computer Engineering, Inha University  
**License:** Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)

> **Important notice:** This project is intended for non-commercial research, education, and laboratory training. It is not offered for commercial products, paid services, proprietary internal tooling, or monetized redistribution without separate written permission.

## Suite

This Skill belongs to **Writing Bot**.

## Role

Master router for the Writing Bot suite. It classifies a writing task, detects the stage, and recommends the correct specialized writing skill.

## When to use

- When the user is unsure which writing workflow to use.
- When a task involves writing, editing, review, response planning, or communication design.
- When the work may involve Korean, English, or mixed-language writing.

## When not to use

- Do not use this Skill when another specialized Skill clearly matches the task better.
- Do not use this Skill to bypass academic, professional, research-integrity, or institutional requirements.
- Do not use this Skill to fabricate evidence, data, sources, credentials, or results.

## Recommended prompt

```text
$writing-bot
Task: [writing task]
Language: [Korean / English / mixed]
Audience: [reader]
Current material: [none / outline / draft / reviewer comments]
Constraint: [deadline / length / format]
Please route me to the right Writing Bot skill.
```

## Expected output

A good response from this Skill should identify the task stage, state assumptions, provide structured guidance, and give the next concrete action. If the request raises ethical or integrity risks, the Skill should stop unsafe work and redirect to a transparent, legitimate workflow.

---

# Writing Bot 사용 설명서

**버전:** v1.0.0  
**작성자:** 김법수, 인하대학교 전기컴퓨터공학과  
**라이선스:** 크리에이티브 커먼즈 저작자표시-비영리-동일조건변경허락 4.0 국제 라이선스(CC BY-NC-SA 4.0)

> **중요 고지:** 이 프로젝트는 비상업적 연구, 교육, 연구실 훈련을 위한 자료입니다. 별도의 서면 허가 없이 상업 제품, 유료 서비스, 독점적 내부 도구, 수익화된 재배포에 사용할 수 없습니다.

## Suite

이 Skill은 **Writing Bot**에 포함됩니다.

## 역할

Writing Bot 전체의 대표 라우터입니다. 글쓰기 작업을 분류하고 현재 단계를 진단한 뒤 적절한 전문 Skill을 추천합니다.

## 사용해야 하는 경우

- 어떤 글쓰기 워크플로우를 써야 할지 확실하지 않을 때.
- 글쓰기, 편집, 리뷰, 답변 계획, 커뮤니케이션 설계가 포함될 때.
- 한국어, 영어 또는 혼합 언어 글쓰기 작업일 때.

## 사용하지 말아야 하는 경우

- 다른 전문 Skill이 작업에 더 명확하게 맞는 경우 이 Skill을 사용하지 마십시오.
- 학술, 직업, 연구윤리, 기관 요구사항을 우회하기 위해 사용하지 마십시오.
- 근거, 데이터, 출처, 경력, 결과를 조작하기 위해 사용하지 마십시오.

## 권장 프롬프트

```text
$writing-bot
Task: [writing task]
Language: [Korean / English / mixed]
Audience: [reader]
Current material: [none / outline / draft / reviewer comments]
Constraint: [deadline / length / format]
Please route me to the right Writing Bot skill.
```

## 기대 출력

좋은 응답은 작업 단계를 식별하고, 가정을 명시하며, 구조화된 지침을 제공하고, 다음의 구체적 행동을 제시해야 합니다. 요청에 윤리 또는 무결성 위험이 있으면 안전하지 않은 작업을 중단하고 투명하고 정당한 워크플로우로 전환해야 합니다.



