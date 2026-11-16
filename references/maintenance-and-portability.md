# Maintenance and portability

## Scoped maintenance

Load `cooperative-answering` for all prose/code work and the relevant Hermes skill
before changing its configuration. Resolve live behavior from the installed
runtime, canonical prompt, and deployed job rather than dated incident notes.

Keep these aligned when behavior changes:

1. Main skill and focused references.
2. `templates/daily_pulse_cron_prompt.md`.
3. Runtime validation/rendering and meaningful local tests.
4. The deployed daily job's prompt, attached skills, tool availability, workdir,
   schedule, and result routes, when deployment is authorized.

The job reads the canonical template each run, loads `personal-pulse-generation`
and `cooperative-answering`, and loads `codex-gpt-image` for images. Its normal
collection needs session search, files, scoped terminal reads, web retrieval,
vision, and delegation where available. Derive the workdir, schedule, timezone,
job name, and native targets from the same private configuration used by the runtime.
Check the scheduler's effective timezone and next occurrence against `user.timezone`
and `schedule.cron`; host cron may require an explicit timezone or converted schedule.
Both result and failure routes use `delivery.feed_target`;
`[SILENT]` follows verified completion. After a live configuration write, read back
the exact job. Template editing and job execution are separate operations.
Keep one-time maintenance instructions in the maintenance conversation. Persist
only recurring execution instructions in the job prompt. State delivery ownership
explicitly: the Pulse runtime publishes cards/feed using the durable journal;
the scheduler delivers the final status response. When diagnosing a publication
refusal, inspect both the stored job prompt and the scheduler's assembled hint
for scope collisions before changing runtime code.

## Verification

Exercise the changed behavior on real local objects and archived inputs copied to
isolated task paths. Useful checks include strict validation of reduced editions,
reader-surface rendering, stable historical payload digests, and offline send
journals. Verify actual and desired counts separately, exact card identity, link
binding, safe media, and ambiguous-send handling. A simulated transport result is
an offline check; the production journal is the publication record.

Trace tone through generated prose, the documented preview command, and the actual
publisher renderer. Exercise the same layout and length budget before journal
creation; record pending network operations after deterministic rendering succeeds.
Remove unused assemblers that duplicate the schema or flatten evidence. Reject
malformed supplied numeric values at normalization, preserving valid historical
payload digests. Compare a real personal candidate's new information with prior
conversation delivery when checking selection changes.

Keep audit output compact: changed behavior, evidence exercised, failures, and
remaining deployment work. Diagnose the specific Pulse component rather than
turning a content review into an unrelated system-wide audit.

## Export

Keep source, documentation, tests and `config.example.json` shareable in the Git
index. Store deployment values in ignored local configuration and runtime data
outside the source tree. After reviewing and committing the public tree, use:

```bash
git -C "$SKILL_DIR" archive --format=zip --prefix=personal-pulse-generation/ \
  --output="$ARCHIVE" HEAD
```

`SKILL_DIR` comes from skill discovery; `ARCHIVE` is an absolute output path.
Inspect the indexed file list before sharing. Dependencies are installed
separately through their documented setup procedures.

Keep local reports and configuration output in private storage. Git ignore rules
protect untracked files; previously tracked private files require `git rm --cached`
and a reviewed commit. Historical exposure requires separate history cleanup and
credential rotation. A current-tree or archive review establishes its own scope;
remote history requires a separate audit.

Recipient setup establishes their user, context permissions, storage paths,
timezone/schedule, confirmed card/feed targets, image policy, and preferences.
Use the recipient's active Hermes Codex authorization through the executor. Back up
existing skill installations before replacing them; update an existing job's
prompt and tools along with its attached skills.

Before activation, compile helpers, exercise validation/rendering and offline send
with isolated state/journals, and run the image helper's `generate --dry-run` using
a caller-owned prompt and explicit model/quality/size/format/timeout. Read back the
configured job. Live delivery begins under the recipient's approved schedule or
separately authorized run.
