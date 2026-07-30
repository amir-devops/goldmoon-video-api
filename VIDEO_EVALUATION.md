# Goldmoon Video API — Deep Evaluation

**Goal of this review:** what to enhance so the output is a *perfect, attractive short video that an audience actually likes and watches to the end.*

**Scope reviewed:** `render_pipeline.py`, `main.py`, `tts.py`, `assets/presets.json`, the `Dockerfile` deployment path, fonts/music/LUT assets, and the n8n automation flow.

**Overall verdict:** The engine is genuinely solid — modular filtergraph, per-render variety, voiceover ducking, LUT grade, and (after this session) full‑screen reveal‑pan framing that no longer crops away detail. The biggest wins now are **not** in the core render math; they're in a few production gaps that silently degrade the result, plus a set of "make it feel designed" polish items around the first second, text legibility, pacing, and the call to action.

---

## 1. Critical production gaps (fix first — they silently break the intended look)

### 1.1 Custom fonts are missing on the server (highest impact)
`FONT_PRESET_MAP` maps presets to Oswald, Montserrat, and Playfair, but:

- `assets/Montserrat-Bold.ttf` and `assets/Oswald-Bold.ttf` **do not exist** anywhere in the project.
- `PlayfairDisplay-Regular.ttf` exists in the repo root but the `Dockerfile` never `COPY`s it into the image.

Result: on Hugging Face every caption falls back to **DejaVu Sans Bold**. All the typographic personality across your ten presets collapses into one generic system font. For premium travel reels, type is half the "expensive" feeling — this is the single highest‑leverage fix.

**Fix:** add real Oswald‑Bold, Montserrat‑Bold, and Playfair font files and either commit them (they're small) or download them in the Dockerfile the same way the logo is. Then verify `/health` reports the fonts present.

### 1.2 Logo / subscribe icon depend on a fragile build‑time download
`assets/logo.png` and `assets/subscribe_icon.png` are git‑ignored and instead pulled from pinned GitHub commits at build time (`LOGO_COMMIT=13ee8a3`, etc.). This works today but is brittle: if those commits are ever GC'd, the repo is renamed, or GitHub LFS quota is hit, the **outro logo silently disappears** and the video falls back to a plain text card. Treat these as release assets with a stable, versioned URL, or bundle them via LFS on the HF side.

### 1.3 English‑only text is a hard audience limit
`require_english_text()` rejects anything that isn't `[A-Za-z0-9 .,!?-]`. For an Egypt tourism brand, a large slice of the audience is Arabic‑speaking. Right now Arabic captions and Arabic voiceover script are impossible. If Arabic (or bilingual) output is on the roadmap, this validation plus the font stack (needs an Arabic‑capable font and RTL shaping) is a dedicated project — but potentially a large reach unlock.

### 1.4 `frame_mode` isn't exposed through the API/CLI
The new `reveal` / `fit` / `fill` modes exist in `render_pipeline.py` but `VideoRequest` and the n8n CLI have no field for them, so callers can't choose. Add an optional `frame_mode` field (default `reveal`) to `main.py` and `--frame-mode` to the CLI so per‑video framing is controllable.

---

## 2. Attractiveness & retention (what makes people keep watching)

### 2.1 The first second has no hook
Short‑form lives or dies in the first ~1 second. Today scene 1 is just the first photo with a caption fading in at 0.3s. Consider a deliberate **hook**: a punchy opening title ("7 Wonders of Egypt", "You need to see this"), a fast push‑in on the strongest image, or a 3–4 frame flash montage before settling. Even a 0.5s branded title beat measurably lifts retention.

### 2.2 Caption legibility isn't guaranteed
With `reveal` mode the caption can land over bright sky, pale stone, or busy city — and several presets (`golden_hour`, `modern_vibe`) draw text with **no background box**. In testing, cream text over light limestone was hard to read. Fix universally with one of: a semi‑transparent rounded caption plate, a stronger/large blur shadow, or an auto‑contrast that samples the region behind the text. Legible text = higher completion and shares.

### 2.3 Pacing is uniform and static
Every scene is the same 4.0s with the same eased move. Reels that "feel alive" vary rhythm: a longer establishing shot, then quicker beats. Two cheap wins: (a) allow per‑scene durations, and (b) optionally **cut on the beat** of the chosen music track (detect beats once, snap scene boundaries). Even alternating 4.5s / 3.5s reads as more intentional.

### 2.4 Motion could be more varied and purposeful
The reveal pan now alternates direction — good. Next level: pick the pan *toward* the subject (use the existing `detect_focus_point` to bias where the pan ends), and mix in an occasional slow push‑in on portrait/detail shots so it isn't always a lateral sweep.

### 2.5 Transitions are random per render
`TRANSITION_POOL` is broad (21 options incl. wipes, slides, circles, radial). Some of these read as "PowerPoint" rather than cinematic. Curate a smaller, classier default set (fade, dissolve, smooth L/R, fadeblack) and reserve the flashy ones for opt‑in. Consistency looks more premium than novelty.

### 2.6 Color grade is one‑size
A single shared LUT is applied on top of every preset. It gives consistency, but Egypt content benefits from a warm golden‑hour push on desert/stone and slightly cooler contrast on Nile/water shots. Consider 2–3 LUTs selectable per style, or a subtle auto white‑balance so mixed source photos match better.

---

## 3. Audio & voiceover

- **Loudness normalization:** there's no `loudnorm` (target LUFS) pass. Platforms normalize anyway, but a consistent `-14 LUFS` master makes every video sit at the same, professional level and avoids the "one video is quiet, the next is loud" problem across a channel. Easy, high‑value add to the final encode.
- **Music ducking is good** (sidechain compress under voice) — keep it. Consider a short music‑only "tail" under the outro so the end doesn't feel abrupt.
- **Voiceover is a strong differentiator.** Lean into it: a 2–3 sentence script with a hook line and a CTA line ("Save this for your trip") turns a slideshow into content. Worth templating scripts per tour in Sanity.
- **TTS soft‑fails silently.** If narration fails, the video still ships music‑only — good for uptime, but there's no signal back to the operator. Log/emit a flag so you know when a batch went out without the intended voice.

## 4. Call to action & branding

- The outro now has a polished logo + divider + URL (good). But there's **no verbal/゛action CTA** — add a line like "Book your journey — link in bio" or "goldmoontours.com/en" spoken and/or shown larger. The single most‑skipped opportunity in travel shorts is not telling the viewer what to do.
- The **subscribe icon is YouTube‑styled**. If these post to Instagram/TikTok, it's off‑platform and can look spammy. Make it platform‑aware or off by default for non‑YouTube destinations.
- Consider a consistent **brand intro sting** (0.5s logo reveal) so a viewer recognizes the channel instantly across videos.

## 5. Technical / quality (mostly addressed, a few remaining)

- **Encode quality:** now `medium` / CRF 19 / high profile / 256k audio — good for social. `medium` is slow; if throughput matters, `fast` at CRF 20 is a fine trade.
- **Source resolution ceiling:** the web images are 1366×768, below 1080p. Full‑screen framing upscales ~2.5×; the added `unsharp` helps, but the real fix is sourcing higher‑res originals (or an AI upscaler pass) for the hero shots. This is the main remaining limiter on raw sharpness.
- **Legacy `fill` path inefficiency:** the old `loop + zoompan` pattern over‑produces frames (zoompan `d` multiplies the looped frames, then the final `-t` trims). The new `reveal` path avoids it. If you keep `fill`, port it to the single‑frame approach to cut render time.
- **Concurrency:** `render_semaphore = Semaphore(1)` means one render at a time. Fine for a cron, but a burst of requests will queue/timeout. Size it to the box, or move to a job queue with status polling for scale.
- **Determinism:** lots is random per render (style, transition, animation, music). Great for variety, bad for reproducibility/AB testing. Consider accepting an optional `seed` so a good result can be reproduced.

---

## 6. Suggested priority roadmap

**P0 — do before the next production push**
1. Bundle the real fonts (Oswald / Montserrat / Playfair) into the image and verify via `/health`.
2. Guarantee caption legibility (contrast plate or adaptive shadow) across all presets.
3. Harden logo/icon delivery so the outro never silently loses the logo.

**P1 — biggest audience/retention gains**
4. Add a 0.5–1s opening hook / title beat.
5. Expose `frame_mode` + optional per‑scene durations; add a spoken/shown CTA on the outro.
6. Add `loudnorm` to the audio master.
7. Curate the default transition set.

**P2 — scale & reach**
8. Bilingual / Arabic support (font + RTL + validation + TTS voice).
9. Beat‑synced cutting and focus‑biased pan direction.
10. Optional `seed` for reproducibility; job queue if concurrency grows.

---

*Net: the rendering core is in good shape. The gap between "good" and "audience loves it" is now mostly (1) making sure the intended design assets actually ship to the server, and (2) three design moves — a hook, always‑legible text, and a clear CTA.*
