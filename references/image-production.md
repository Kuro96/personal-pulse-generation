# Image production

Load the image-generation skill selected at installation (`codex-gpt-image` or
another installed image-generation skill) after selected content and privacy-safe
briefs are final. Pulse owns style, palette, composition, typography policy, grid,
panel order, and the complete prompt. The executor receives that prompt unchanged.

## Artifacts and layout

Resolve `PULSE_RUN_ROOT` from the runtime as described in the publication reference.
Keep prompts, outputs, and crops in its `YYYYMMDD/run-id/` directory. Use unique
attempt names such as `pulse_YYYYMMDD_collage_attempt_01.prompt.txt` and `.png`.

| Selected cards | Grid |
|---|---|
| 1 | 1×1; same image for feed and card |
| 2 | 2×1 |
| 3–4 | 2×2 |
| 5–6 | 3×2 |
| 7–8 | 4×2 |

Unused cells are decorative and discarded during cropping. Choose an explicit
size suitable for the composition; `1536x1024` suits 3×2, `2048x1024` suits 4×2.

## Image-skill contract

也可使用其他用于生图的 skill。The selected skill must accept the complete
privacy-safe collage prompt, produce a locally saved PNG under `PULSE_RUN_ROOT`,
and report dimensions verified by fully decoding the actual image. The agent
visually checks that file and crops the approved collage in card order. Use the
chosen skill's documented invocation and authentication. Set `paths.image_executor`
to `null` when it has no Codex-compatible script. The runtime consumes the resulting
files and metadata; it does not invoke the image provider. Record the chosen skill
in the cron job's attached skills and prompt.

## Codex executor

Use `PYTHON` and `IMAGE_EXECUTOR` resolved from configuration as shown in the
publication reference, with `RUN_DIR`, `DAY_KEY`, `ATTEMPT`, and `SIZE` set for the current run:

```bash
"$PYTHON" "$IMAGE_EXECUTOR" generate \
  --prompt-file "$RUN_DIR/pulse_${DAY_KEY}_collage_attempt_${ATTEMPT}.prompt.txt" \
  --model gpt-image-2 --quality high --size "$SIZE" \
  --output-format png --timeout 600 \
  --out "$RUN_DIR/pulse_${DAY_KEY}_collage_attempt_${ATTEMPT}.png"
```

The helper resolves active Hermes Codex authorization, enforces its endpoints and
redirect policy, fully decodes the response, reports `actual_dimensions`, and
publishes the verified file atomically. Use a new filename per attempt; preserve
existing outputs. The helper makes one network attempt per invocation.

Visually inspect the exact decoded output: card/panel order, distinct boundaries,
brief match, and privacy. Require scenes free of readable text, logos, faces, and
private details. Preserve the approved uncut collage as `feed_cover_image`. Crop
that exact file using the helper's decoded `actual_dimensions`, with row-major
panel order matching cards. One-card editions reuse the original directly.

## Retry and fallback

After a concrete network/backend failure or visually invalid collage, Pulse may
revise its privacy-safe prompt and make up to three explicit new attempts, each
with unique files. Authorization rejection, rate limits, or an unavailable service
can make further attempts unsafe or futile; record the specific blocker and stop
retrying that condition.

Text-only publication is available for explicit user opt-out or force majeure:
authorization/rate/account/service blockage, persistent concrete network/backend
failure, invalid collage after permitted attempts, or a hard delivery deadline
explicitly accepted by the user. Otherwise missing required covers stop publication.

Record `user_opt_out_text_fallback` or `force_majeure_text_fallback` and the concrete
reason internally. Complete text fallback through the same strict native send and
journal procedure. The feed renders one short safe image-status note. A reduced
edition retains the same image policy as a full edition.
