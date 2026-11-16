Generate and publish the daily Personal Pulse for `user.name` in `user.language`.
Each scheduled invocation executes the authorized daily publication workflow.
Delivery has two owners: `pulse_runtime.py send` publishes the edition's cards and
feed to the configured targets using the durable daily journal; the scheduler
delivers the final run-status response. Verified publication returns `[SILENT]`.
Configuration maintenance has its own scope in the maintenance conversation.
Resolve `skill_dir` from skill discovery, then load `PULSE_CONFIG` or the default
`skill_dir/config.local.json`. Run `scripts/pulse_config.py [--config PATH] check`;
consume `show` locally as private configuration. All runtime commands share this
configuration. Work in `paths.workdir`, with day boundaries in `user.timezone`.
The deployed job uses `schedule.job_name` and `schedule.cron`; verify its effective
timezone and next occurrence against the configuration when installing or changing
it, accounting for the host scheduler timezone.

Load `cooperative-answering` and `personal-pulse-generation`. Read the skill's
collection/selection, payload, image, and native-publication references for the
phase being executed. Every evidence producer, draft subagent, and publisher must
load `cooperative-answering` in its own context.

1. Resolve today's date in `user.timezone` and `pulse_runtime.PULSE_RUN_ROOT` with tools.
   Read `paths.state_file`, the day's durable
   `pulse_YYYYMMDD.send.json` beneath `paths.journal_dir`, and active-publisher
   evidence. A complete matching journal ends successfully. An unresolved
   `in_flight` or conflicting journal needs operator reconciliation. A valid partial
   journal resumes its exact payload and targets. Persist/reuse the day's random
   6–8 desired ceiling with `daily-target get-or-create`. Create a unique run
   directory beneath the runtime-resolved scratch root.
2. Collect in parallel every day from genuine recent user conversations, notes in
   `sources.personal_context_dir` and `sources.notes_roots`, and
   read-only existing project state. Cover 72 hours, two weeks, and relevant older
   material. Resolve later outcomes, completion, user preferences, pauses, and
   suppressions before drafting. Automated reports supply delivery history rather
   than fresh user intent. Record exact evidence and source limitations. When
   delegation is unavailable, perform equivalent independent reads in parallel.
3. Make supplemental external refresh discovery: aim for one credible item, or two
   distinct-domain items for `Refresh 多一点`. Budget two searches/pages and roughly
   ten minutes normally, four and fifteen for the higher preference. Seek outside
   recurring personal/toolchain clusters and verify at primary sources. Record
   desired/selected counts and a short safe note for any shortfall; continue.
4. Evaluate candidates by “是否带来新的信息” OR “是否有了新的变动”, together with
   “当前的我是否可能有价值”. Compare exact previously delivered information with the
   proposed addition. New synthesis, examples, tradeoffs, connections, and current
   applications can qualify even for discussed topics or completed work. Record
   concise internal prior-delivered/new-information/new-state/current-relevance
   assessments and evidence. Use `novelty_reason` new_information, new_state, or
   current_relevance; compatible legacy values remain available when accurate.
5. Give personal material first consideration, then rank by marginal value and
   evidence across C1/C2/C3/C4 and today_72h/recent_2w/long_term. There is no personal
   card quota. Merge one underlying event into one card and compare against actual
   earlier delivery. If below ceiling after ordinary collection, perform one
   additional pass of up to four focused lookups across at least two underexplored
   topics/domains, including personal material where useful. Verify external
   claims. Select 1..ceiling eligible cards; zero is PULSE_SEND_FAILED. Preserve the
   desired ceiling and record selected count separately. At most two cards require
   user action. Recheck deduplication and exact coverage exceptions after selection.
6. Draft schema 1.1 cards with evidence, delivery result, uncertainty/confidence,
   privacy, feedback association, and illustration briefs. Write title and natural
   paragraphs in `user.language` with the useful substance first. Keep internal assessment,
   provenance, QA discussion, and recurring 已交付洞察/摘要/为什么现在 labels out of
   the reading format. The publisher applies its own cooperative-answering edit
   to the complete merged detail text and short feed teasers.
7. Finalize the selection and privacy-safe visual plan, then load the image skill
   selected in the job prompt and attached skills. It may be codex-gpt-image or
   another image-generation skill meeting references/image-production.md.
   With codex-gpt-image, supply one complete unchanged collage prompt to
   `paths.image_executor` via `paths.python_executable`, invoking `generate`
   using `--prompt-file`, `--model gpt-image-2 --quality high`, explicit `--size`,
   `--output-format png --timeout 600`, and a unique run-local `--out`. Follow the
   image reference for grid and attempts. With another image skill, use its
   documented invocation and produce the same local PNG and decoded-dimensions
   contract. Verify the exact decoded collage visually,
   preserve it as feed_cover_image, and crop that file using actual_dimensions.
   A one-card edition shares the same 1×1 image. Approved opt-out/force-majeure text
   fallback records the concrete reason internally and uses the same native send.
8. Run `pulse_runtime.py validate --pretty --strict-count` with the explicit day and
   state file, then render and review complete details/feed for prose, privacy,
   links, count, and length. Resolve `scripts/pulse_runtime.py` relative to `skill_dir`
   and invoke it with `paths.python_executable`. Use the native-publication
   reference's configuration-derived command: `send <normalized.json>` with
   `--pulse-day` set to the configured local day, `--state-file` from `paths.state_file`,
   `--send-record` from `paths.journal_dir/pulse_<YYYYMMDD>.send.json`,
   `--cards-target` from `delivery.cards_target`, and `--feed-target` from
   `delivery.feed_target`. Authentication uses the variable named by `token_env`,
   optionally supplied through the file named by `paths.env_file`.
   Runtime checks safe media and exact targets, locks the journal, records each
   operation before sending, and persists returned IDs. Each card is one complete
   detail: photo caption through 1000 UTF-16 units, otherwise text through 3900.
   Longer text requires editing. After all details, send one linked-title feed with
   the uncut collage; approved all-text fallback uses one text feed.
9. Read back the exact completed journal: matching day/digest/targets/counts, every
   selected card ID/ref, positive feed ID, cleared in_flight. Preserve historical
   journals and retained messages. Scheduled verified success or an already-complete
   matching edition returns exactly `[SILENT]`. Otherwise return concise
   `PULSE_SEND_FAILED: <day> <stage>: <reader-safe concrete reason>`; keep detailed
   diagnostics local. Cron deliver and failure_deliver both target
   `delivery.feed_target`.
