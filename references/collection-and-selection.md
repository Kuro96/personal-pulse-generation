# Collection and selection

## Parallel worker contract

Every producer and drafting worker begins by loading `cooperative-answering`.
The editor passes the day in `user.timezone`, bounded source scope, current preferences,
suppressions, pause constraints, and recent delivered-card records.

| Worker | Routine evidence | Output emphasis |
|---|---|---|
| Conversations | `session_search` on genuine user exchanges in 72h, then two weeks; read surrounding and later messages | User intent, interests, decisions, useful connections, latest outcome |
| Notes | `sources.personal_context_dir` and `sources.notes_roots` from the private configuration | Recent notes plus relevant older ideas, current applicability, corroboration |
| Project state | Existing scoped files, snapshots, Git metadata, accessible issue/job records | Observed state and dates; latest user permissions |
| External | Bounded discovery and direct primary sources | Independent discoveries and timely factual verification |

Start personal-context discovery at `INDEX.md` beneath `sources.personal_context_dir`
when configured and present, then read relevant indexed material. Select people/context notes by
current relevance and keep their private details in internal evidence.

Treat personal-context files according to their provenance: a derived profile or
assistant summary is a lead to check, while a dated user statement establishes
intent. Inspect actual contents and later context before treating file modification
time as an event date. Generated Pulse reports and automated sessions are delivery
history, not fresh user interests. Resolve copied/migrated notes to their original
dates and identities when available.

Read-only collection uses existing artifacts. Explicitly paused tests, builds,
device access, jobs, refreshes, or formal project output remain paused. A useful
comparison or example can be drafted from authorized existing evidence.

Each worker returns a compact packet:

```json
{
  "source_type": "recent_sessions|notes|project_state|external",
  "findings": [{
    "claim": "supported observation",
    "source_ref": "exact internal session/message/file/revision or public URL",
    "observed_at": "dated evidence or unknown",
    "latest_check": "later source checked and resulting status",
    "user_signal": "genuine user interest or context",
    "candidate_angle": "possible useful addition"
  }],
  "sources_unavailable": [{"source": "name", "reason": "concrete limitation"}]
}
```

Search later state for each promising thread: completion, changed preference,
replacement decision, abandonment, suppression, and subsequent Pulse delivery.
Use the latest applicable user instruction. Conflicts and unknowns stay explicit
in internal evidence. A source failure gets one bounded alternative lookup; the
editor continues with available material.

## Candidate value

Eligibility is `(new information OR new change) AND plausible value to the user now`.
Information may be a new synthesis, worked example, tradeoff, cross-source link,
or useful application of existing knowledge to current context. An unchanged
project can yield an informative card. A completed deliverable can have a new
explanation worth reading.

Keep this concise assessment beside drafts in the run directory:

```json
{
  "prior_delivered": "what the user actually already received, with source",
  "new_information": "specific additional understanding, or empty",
  "new_state": "specific observed change, or empty",
  "current_relevance": "why this addition may matter to the user now",
  "evidence_refs": ["source IDs"],
  "novelty_reason": "new_information|new_state|current_relevance",
  "event_key": "underlying event or distinct informational contribution"
}
```

These assessment fields are editorial working data, not required new schema fields.
The published payload carries `novelty_reason`; preserve compatible legacy values
`completed_deliverable`, `real_time_window`, and `unresolved_decision` where accurate.
For `current_relevance`, specify the new applicability or consequence delivered;
elapsed time or a familiar topic title alone supplies no informational addition.

Examples:

- A finished language lesson can support a new minimal-pair example tailored to
  the user's recurring pronunciation confusion.
- A previously compared device can support a new tradeoff under a newly stated
  travel constraint, with current specifications verified when relevant.
- A repository's unchanged release number with the same previously delivered
  description has no additional content to select.

## Identity and history

Compare against recent Pulse payloads and complete journals, plus actual responses
already delivered in conversation. Match source/event identity and substantive
claims rather than title wording. One release, announcement, incident, or project
change produces one card per edition; merge its explanation and implications.
Recheck identity after synthesis because workers may name the same event differently.
A new synthesis gets a stable identity for its actual contribution, supported by
the prior-versus-new assessment; changing a key does not establish new value.

Respect current topic suppression. Reconsider a suppressed topic when explicit
user interest or strong new evidence changes its applicability. Keep the latest
reason in the internal assessment.

## Ranking and coverage

Consider all C1/C2/C3/C4 classes and all three time horizons each day. Give genuine
personal material first consideration and rank eligible cards by marginal value,
current relevance, evidence, and reader effort. This ordering carries no minimum
personal count or forced class split. Prefer `delivered` and `insight`; at most two
cards may be `decision_needed` or `action_needed`.

Use the persisted 6–8 desired ceiling. If ordinary collection yields fewer eligible
cards, perform one additional pass of at most four focused lookups across at least
two underexplored topics/domains. This can revisit conversations or notes for a
missing synthesis as well as seek external material. Check external claims at
primary sources. The pass is separate from refresh discovery. Finish with 1..ceiling
eligible cards; zero is a visible no-qualified-content failure.

Record class concentration and absent-horizon exceptions using actual selected
count, following the payload reference. They explain the eligible set rather than
forcing weak cards into it. Record meaningful omissions briefly in local artifacts.

## Supplemental refresh

Refresh is a cross-cutting internal tag, usually C4 and occasionally C2. Aim for one
credible discovery; `last_preference: Refresh 多一点` aims for two distinct domains.
Both are best-effort preferences within the daily ceiling. Additional independent
eligible discoveries remain possible.

Build an exclusion set from current card topics and recurring Pulse themes. Seek
material outside those clusters: games, design, media, hardware, science, practical
life, or an unfamiliar niche project. Unless explicitly requested, runtime,
notification/subscription plumbing, toolchain release notes, and repository hygiene
belong outside the refresh lane. Notes-only rediscovery is personal material.

Budget normal refresh to two focused searches/pages and roughly ten minutes; the
higher preference allows four and roughly fifteen minutes. Prefer primary sources;
switch provider or direct source after a blocked retrieval. Browser puzzles or
sensitive verification require a different accessible source or a recorded blocker.

Below the preference target, record exact desired/selected counts and a short
reader-safe note in `scope.refresh_status`. Continue with the strongest eligible
set. An external-source shortfall does not stop the image or publication stages.
