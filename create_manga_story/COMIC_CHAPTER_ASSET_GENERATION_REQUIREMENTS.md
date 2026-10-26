# Comic Chapter Asset Generation Requirements

This file records the product requirements for chapter-level character and scene assets.

## Workflow responsibility split

- `Comic Chapter Asset Generation` is the only workflow that creates chapter character and scene images with Gemini/Qwen/SeedVR2.
- It classifies all analyzed chapter occurrences first, generates one approved master per ordinary character/location variant, and persists the result in SQLite.
- `ComicAssetAutoTargetBatchBuilder` creates current-page requests only; it does not create images.
- `ComicAssetImageBatchGenerationLoop` in `asset_workflow_nodes.py` is the MiniMax H3 page-resource reader only. It loads approved bound assets and never generates replacements.
- The page-resource workflow must run after chapter asset generation has written approved assets and page bindings. No direct connection from the chapter generator's IMAGE output is required; SQLite is the handoff.
- `chapter_asset_manifest` is chapter-level data and is not an input to the page-resource reader. Current-page `requests_json` is the only request source.

## Character identity rule

- The same character must reuse one persistent reference asset across ordinary comic occurrences.
- Facial expression, emotion, crying, screaming, pose, action, camera angle, framing, lighting, props, newspaper/message context, battle reaction, and ordinary clothing presentation must **not** create a new character asset.
- A new character asset may be created only when the story explicitly shows a huge, persistent transformation, such as a changed species, body form, age, scale, or supernatural transformed form.
- A transformation asset is bound only to the pages/occurrences where that transformation exists.
- A different outfit, injury, equipment, or temporary scene state alone is not sufficient to create a new character asset.
- When uncertain, reuse the existing character asset instead of generating a new one.
- Chapter grouping must use `same_entity_key`/stable character name before temporary page/entity IDs; a normal character appearing on multiple pages must enter one character group.
- The chapter generator must finish classification across all chapter occurrences before generation; it must never run one generation per page occurrence.
- The same generated `asset_id` is reused and page-bound for every ordinary occurrence of that classified character or scene.
- Generic recurring roles such as `Soldier`, `Guard`, `Villager`, or `Servant` are grouped by their stable role/name even when Gemini emits occurrence keys such as `soldier_1`, `soldier_2`, or `soldiers_saluting`.

## Per-page resource reader

- `asset_workflow_nodes.py` is the page-resource reader for MiniMax H3 video generation.
- It must read `chapter_asset_page_bindings` from SQLite using the current page ID and entity evidence, then load the approved shared `asset_id`.
- `asset_workflow_nodes.py` must never call Gemini/Qwen/image generation or use a legacy asset cache to create a missing resource. All character and scene images must be created by `Comic Chapter Asset Generation` first.
- If the current page occurrence has no approved SQLite binding, the page-resource reader must fail with a missing-binding error instead of generating a replacement image.
- It must return the bound page resources and must not generate chapter assets, use ComfyUI cache state, or fall back to a different page's asset.
- A page containing multiple characters/scenes must produce one correctly bound reference per page entity.
- In `asset_workflow_nodes.py`, one `asset_request` always contributes exactly one `batch_images` output; additional references are context-only and must never be emitted.
- A missing current-page binding is a hard error (`MISSING_CHAPTER_ASSET`); the reader must not use a chapter-wide group fallback, legacy asset cache, or transient reference image.

## Scene identity rule

- The same location must reuse one scene reference across ordinary shots and panel changes.
- A new scene asset is allowed only for an explicit persistent state change that materially changes the location itself.

## Reuse and refresh

- Existing approved assets must be reused when the entity identity and generation inputs match.
- `force_refresh=true` is the explicit exception that permits regeneration.
- Legacy duplicate ordinary character groups must not all be emitted together; reuse one ordinary asset and keep only explicit transformation assets as additional references.
- Re-analysis may change an entity candidate ID, variant key, or fingerprint. If an approved chapter asset has an overlapping deterministic source crop, it must be reused instead of regenerated.
- Only deleting the approved asset or setting `force_refresh=true` may intentionally cause regeneration.
- SQLite entity bindings and chapter asset groups are the authoritative reuse record; ComfyUI node execution cache must never decide whether an asset is regenerated.
- Re-running analysis on the same page must reuse the existing SQLite entity ID for the same named character/location; it must not create a new candidate ID solely because the existing evidence belongs to that page.

## Resource observation

- The `Comic2Anime 资源` panel must show the entity name when available, with the generated asset name and asset ID retained for tracing.
- Deleting a generated resource clears its chapter group's `generated_asset_id`, returns the group to `planned`, and removes source-crop references for that asset. Re-running chapter generation may create a new approved asset and re-establish page bindings.

## Page batch output

- `ComicAssetBatchGenerationLoop.batch_images` must return all final images for the current page.
- Each current-page entity/request contributes exactly one final image to `batch_images`; approved chapter masters and ordinary approved references are reused, not emitted as extra reference/crop images.
- Transient page crops and additional reference images are inputs only and must not appear as extra page outputs.
- The batch report must expose `page_image_count` and `page_image_asset_ids` so the current-page output can be verified.
- The page batch report must distinguish `REUSED` page resources from generation decisions; this reader has no `PASS` generation path.

## Page-level asset bindings

- A character or scene reference is generated once and then bound to every page occurrence that uses it.
- The binding target is the source occurrence location: `source_page_id` + `panel_index` + `crop_box`. The same generated asset may be copied to many bindings, but each binding must point back to the exact page occurrence that supplied it.
- Page bindings are persisted in SQLite (`chapter_asset_page_bindings`); they are not inferred from ComfyUI cache, node execution state, fingerprints, or current workflow memory.
- Reusing one asset on multiple pages creates multiple logical binding rows pointing to the same `asset_id`; it does not generate or duplicate image files.
- Each page binding stores that page's entity ID/key/name lookup handle while pointing to the shared generated `asset_id`; the chapter group's first entity ID must not be reused for every page.
- Current-page asset lookup must read the SQLite page binding first.
- `asset_workflow_nodes.py` must read the current page's SQLite bindings directly by source page position before considering any compatibility fallback; it must not select an asset by character-name substring matching.
- Names such as `King` and `Demon King` must never be resolved through `name in evidence`; if page position is ambiguous, return no reference rather than guessing the wrong character.
- `chapter_asset_group_sources` is the occurrence-location evidence table. It must retain the source crop asset, source page, panel index, quality, selected-reference flag, and Gemini evidence JSON used to bind the shared master back to each occurrence.
- `chapter_asset_page_bindings` is the page-to-master link. Multiple pages may point to one `asset_id`; this is intentional sharing, not duplicate generation.

## Verification contract

- Before running MiniMax H3 page generation, verify that the chapter generator report has approved groups and that the current page has approved rows in `chapter_asset_page_bindings`.
- A normal page containing two characters and one macro scene must return exactly three final `batch_images`, one per bound page occurrence.
- Reference crops and extra historical references may be sent as comparison context upstream, but they must never increase the final page image count.
- The SQLite database is the audit record for generation, reuse, deletion, source evidence, and page binding; ComfyUI execution/cache state is not an asset registry.

## Upscale test switch

- `Comic Chapter Asset Generation` exposes `enable_upscale`.
- It defaults to `true` for the normal 4096px SeedVR2 output.
- Set it to `false` during testing to skip SeedVR2 and persist the QC-passed 1024px image, reducing test time and resource usage.
- The switch is included in the SQLite generation fingerprint/parameters.

## Full-page crop analysis

- Gemini must inspect the complete comic page and establish panel boundaries and reading order before returning any character or scene crop.
- Crop coordinates must be normalized relative to the entire page, not a panel or a resized preview.
- Character crops should include the complete visible body whenever possible; scene crops should cover the usable macro environment and remain inside the owning panel.
- The analysis prompt version must change when crop semantics change so stale incorrect crop results are not reused from SQLite analysis cache.

## Request asset type

- Mixed pages must normalize request types by entity: characters use `character_portrait` or another `character_*` type, while locations use `scene_reference`.
- A character must never be emitted with `asset_type=scene_reference`; doing so bypasses character reuse/change detection and can cause duplicate generation.
