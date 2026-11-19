---
name: personal-pulse-generation
description: Use when generating or maintaining personal Pulse.
version: 2.0.0
platforms: [linux, macos]
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [pulse, personalization, recommendations, delegation, json, obsidian]
    related_skills: [cooperative-answering, obsidian, codex-gpt-image]
---

# Personal Pulse

Pulse is a personal briefing in `user.language` for `user.name`: useful information drawn primarily from
personal conversations, notes, and existing project state, complemented by external
discovery. Each card gives the reader something worth knowing now.

Load `cooperative-answering` before collecting, drafting, editing, or publishing.
Every producer, draft subagent, and publisher loads it in its own context.

## When to Use

Use for daily or requested personal Pulse editions, card follow-ups, publication
recovery, and scoped Pulse maintenance.

## Operating map

Read the references needed for the current phase:

- `references/collection-and-selection.md`: everyday sources, worker contracts,
  latest-state resolution, informational value, ranking, refresh, and deduplication.
- `references/payload-contract.md`: schema 1.1 fields and runtime validation.
- `references/image-production.md`: collage prompt, executor, verification, fallback.
- `references/native-publication.md`: commands, reader surfaces, journals, recovery.
- `references/maintenance-and-portability.md`: scoped maintenance and Git distribution.
- `INSTALL.md`: agent installation, dependency sources, and paused cron deployment.
- `references/pulse-technical-detail-deepdives.md`: evidence-based card follow-ups.
- `templates/daily_pulse_cron_prompt.md`: canonical daily execution prompt.

## Card follow-ups

A user clicking **展开讲讲** asks for a deeper explanation of the quoted card.
Answer that request directly: explain the subject, supply useful background,
mechanisms and concrete examples, and verify sources when needed. Anchor the
answer to the clicked card and its cited material. Use
`references/pulse-technical-detail-deepdives.md` for technical topics. This is a
card-specific conversation; the daily publication sequence applies to scheduled
or explicitly requested editions.

The bundled `pulse-card-actions` plugin routes this click as the authorized
reader's message with the original card as reply context. Accepted expansions remove both buttons and run in click order within each chat/topic. The next expansion waits for final text delivery; failure or a 15-minute timeout offers **重试展开** on the original card and releases the queue. A timed-out reply may still arrive. Pending requests are held in gateway memory. **关闭按钮** removes
all inline buttons through a direct Telegram API call after the gateway receives
the callback, outside the agent turn. Telegram's inline keyboard has no client-only
hide action: update dispatch and network latency still affect visible removal.
Card content and publication journals remain available. See `INSTALL.md`
for installing the plugin alongside the skill.

## Daily contract

| Setting | Value |
|---|---|
| User / language / timezone | `user.name`, `user.language`, `user.timezone` |
| Schedule / job | `schedule.cron`, `schedule.job_name` |
| Working directory | `paths.workdir` |
| Cards / feed | `delivery.cards_target`, `delivery.feed_target` |
| Cron `deliver` and `failure_deliver` | `delivery.feed_target` |
| Preference / daily-target state | `paths.state_file` |
| Durable journals | `paths.journal_dir/pulse_YYYYMMDD.send.json` |
| Personal context / notes | `sources.personal_context_dir`, `sources.notes_roots` |
| Scratch / run artifacts | `paths.scratch_dir`; runtime-resolved `PULSE_RUN_ROOT/YYYYMMDD/run-id/` |
| Image executor / Python | `paths.image_executor`, `paths.python_executable` |
| Authentication | environment variable named by `token_env`; optional `paths.env_file` |

Resolve `skill_dir` from the installed skill. Load `PULSE_CONFIG` when set,
otherwise `skill_dir/config.local.json`. Agents installing this skill follow
`INSTALL.md`; `scripts/pulse_cron.py` renders private scheduler arguments from
`templates/cron_job.example.json`. Run `scripts/pulse_config.py [--config PATH]
check` before collection; `show` emits resolved private configuration for local
agent consumption. Treat its output as private. Every runtime command uses this
same configuration. See `INSTALL.md` for installation and `README.md` for Git maintenance.

Confirm the deployed scheduler's effective timezone and next occurrence against
`user.timezone` and `schedule.cron`, including any host-timezone conversion.

Persist one random desired ceiling of 6, 7, or 8 per day in `user.timezone` before collection.
Retries reuse it. Actual selection is 1..ceiling; zero produces a visible
`PULSE_SEND_FAILED: no qualified content`. Completion requires every selected
card and the single feed. A reduced complete edition is the day's edition.

Published editions use actual cover images by default. Use `codex-gpt-image`
or another installed image-generation skill meeting the image-production contract. Details precede the feed. Current messages are retained.
Historical publication journals remain durable duplicate-prevention evidence.

## What qualifies

Evaluate every candidate against:

> 是否带来新的信息，或者是否有了新的变动；这些内容对当前的我是否可能有价值？

Judge the exact information delivered previously against the proposed card.
A topic already discussed or a deliverable already finished can still yield a
useful synthesis, worked example, tradeoff, connection, or application to today's
context. That concrete addition is the selection unit.

Personal material is a regular primary source every day. External refresh
supplements it. Rank credible personal candidates first, then balance by value;
there is no fixed personal-card quota. Reader interest and useful understanding
count alongside practical decisions and work saved.

Use internal `novelty_reason` values `new_information`, `new_state`, or
`current_relevance`; existing `completed_deliverable`, `real_time_window`, and
`unresolved_decision` remain compatible. A reason names the kind of value; the
candidate packet explains the actual addition and its evidence.

## Sequence

### 1. Establish the day and publication state

Read the local date with a tool in `user.timezone`. Resolve the runtime scratch root.
Inspect preferences, suppressions, the day's journal, and active publisher evidence.
A complete matching journal ends the run successfully. An ambiguous `in_flight`
operation requires operator reconciliation. A consistent partial journal resumes
its exact payload and targets; see the publication reference.

Create a unique run directory beneath the resolved root. Reuse the persisted
ceiling and preserve historical journals and preference state.

### 2. Collect in parallel

Dispatch bounded workers for:

1. Recent genuine user conversations: last 72 hours, then two weeks and relevant
   older context, including later outcomes and expressed preferences.
2. Notes: configured personal-context material and notes roots, current interests and longer
   goals, including older notes with a fresh useful interpretation.
3. Existing project state: relevant files, snapshots, repository metadata, and
   already available issue/job state within the user's permissions.
4. Supplemental external discovery: primary-source verification and refresh.

Give every worker the current day, source scope, pause/suppression constraints,
recent delivered-card context, and `cooperative-answering` loading instruction.
Collectors return evidence and candidate opportunities; the editor selects.
If delegation is unavailable, perform the same independent reads in parallel and
record the limitation. Missing optional sources are recorded and collection
continues through accessible sources.

### 3. Resolve current context

For each promising thread, search for later messages and artifacts. Preserve the
latest genuine user decision over an earlier plan. Distinguish an assistant
proposal, a generated report, an executed result, and the user's actual intent.
Read-only project inspection observes existing state. User pauses and operational
restrictions carry into collection exactly; use existing evidence for those areas.

### 4. Assess and synthesize

Record the previously delivered information, the proposed addition, supporting
sources, and its relevance to the configured user now. Use a stable event identity. Merge angles
of one event, then draft the complete useful explanation.

Consider C1 conversation continuation, C2 useful everyday information, C3 long-term
goal progress, and C4 external information, across `today_72h`, `recent_2w`, and
`long_term`. Personal-source priority is editorial ordering, not a numerical gate.
Select by marginal value; prefer directly consumable results and insights.
At most two selected cards require user action.

If below the ceiling, make one additional bounded discovery pass: up to four
focused lookups across at least two underexplored topics or domains, including
personal conversations/notes when useful. Verify external claims at primary
sources. Then select the eligible set; record actual separately from desired.

### 5. Draft for the reader

Write a useful title followed by natural paragraphs in `user.language`. Open with the result,
explanation, or comparison. Name the subject and give the detail that makes the
result intelligible on its own: the prior state, a worked example, or the tradeoff
that matters now. For example, replace “the route changed again” with “the winter
bus skips the lakeside stop, so the library is now the nearest stop to the trail.”
When a public source helps, use its supported sentence as the external evidence
`claim` so the detail renderer places the link beside that paragraph. Personal
insights can stand on the strength of the explanation alone.

The reader sees the substance. Candidate assessments, uncertainty/confidence fields,
private provenance, and QA discussion stay internal. Detail prose uses ordinary
paragraphs rather than recurring labels such as `已交付洞察`, `摘要`, or `为什么现在`.
The feed uses linked titles and short teasers, with natural-language replies for
feedback. Run the publisher's own `cooperative-answering` edit after merging drafts.

### 6. Produce images

Finalize selected content and privacy-safe briefs first. Pulse chooses style,
palette, layout, and panel order, then passes the complete prompt unchanged to the
selected image-generation skill (`codex-gpt-image` or an equivalent). Verify the exact decoded collage visually;
keep it for the feed and crop that file for individual cards. A one-card edition
uses the same 1×1 image for both surfaces.

Use `references/image-production.md` for the required command, dimensions,
attempt policy, and approved text fallback conditions.

### 7. Validate, render, publish

Use strict schema 1.1 validation with the persisted day/target and final cover
metadata. Render complete details and the feed, reviewing prose, links, privacy,
lengths, and actual count. Native send preflights local media and exact targets.

Send through `pulse_runtime.py send` with the explicit durable journal. The runtime
locks the journal, records each pending operation, persists returned message IDs,
and publishes the feed last. Read back the completed journal before reporting
success. Commands and recovery rules are in `references/native-publication.md`.

### 8. Finish

Scheduled success, including an already-complete matching daily journal, returns
exactly `[SILENT]`. An unpublished or ambiguous outcome returns a concise
`PULSE_SEND_FAILED` with day, stage, and reader-safe concrete reason. Detailed
candidate diagnostics remain in the run directory.

## Privacy and scope

Cards are private by default. Keep secrets, private paths, raw session quotes,
and source identifiers in internal evidence rather than reader surfaces or image
prompts. Public citations require safe HTTPS URLs; image briefs use abstract
privacy-safe scenes. Sensitive advice needs appropriate verification and bounded
claims.

Daily collection reads existing personal/project material and writes task-owned
Pulse artifacts. Project execution, note edits, scheduler changes, historical
message removal, and public sharing require their own authorized task scope.

## Maintenance boundary

Keep the main skill, references, canonical template, runtime contract, and deployed
job aligned when changing behavior. Historical journals retain their original
payload bindings and compatibility fields. Use focused offline validation and
rendering for maintenance; live image generation and publication are separate
operations. See the maintenance reference for precise verification scope.
