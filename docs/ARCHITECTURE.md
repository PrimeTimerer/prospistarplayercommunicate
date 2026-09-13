# Architecture and verification boundaries

The app is a Python Windows desktop shell with a loopback HTTP service and
HTML/CSS/JavaScript UI. It does not attach to a game, CT, or another product.

| Boundary | Main modules | Contract |
|---|---|---|
| Save verification | save_reader_v2, aes_pure | Stable complete container check before parsing; no save writes |
| World/history | world_store, ledger_v2, universe_registry | Player/world isolation, preserved archives, legacy readers |
| Deterministic expression | editorial_engine, community, template_store | Authored candidate packs, factual source separation |
| Provider expression | gemini_provider, provider_feed, provider_expression, narrate | Explicit consent, bounded requests, existing-result preservation |
| Narrative review | narrative_context, narrative_review | Bounded contextual verdict; not record authority |
| Conversation/memory | story_desk, story_desk_service, personal_context | Confirmed scope, selected memory, stable source/revision |
| Daily/monthly/yearly recap | chronicle, chronicle_service, story_links | Stored material and incremental changes, not invented statistics |
| Service/UI | service, ui_server, app_shell, ui/ | Loopback/API security, common jobs and visible outcomes |

`config.py` preserves the old import surface; validated configuration lives in
`config_v2.py`. API secrets use the Windows account's protected local store.
Models and keys are not bundled. The UI calls local routes, never exposes a key.

`run_tests.py` uses temporary profile roots. `scripts/build_test_fixture.py`
constructs all committed binary/OCR fixtures from declared constants, never from
private files. `--check` proves byte identity. One optional real-save smoke is
retained but disabled unless deliberately invoked outside the standard runner
with `STARMODEFEED_REAL_SAVE` pointing to a user's own local file.

`python app_native.py --verify-backend artifacts/backend.json` runs a disposable,
self-closing backend fixture without GUI or real provider calls. Create the
`artifacts` parent first and use a new report path. A pass is not evidence of
real-game parsing, full narrative quality, native UI appearance or live API uptime.
