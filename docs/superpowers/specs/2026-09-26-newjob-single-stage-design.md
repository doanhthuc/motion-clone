# New Job as one no-scroll stage — design

Date: 2026-09-26 · Status: approved in conversation, awaiting spec review

## Why

New Job has been refactored several times and still reads as a form. It is a `List`: in Batch
mode four or five sections stack up (shared slots, an outfit strip, a driver strip, Settings, the
basket), so seeing the whole job and its Add button means scrolling, and a growing basket pushes
Settings and the queued jobs under the pinned action bar. The user's stated direction: as little
scrolling as possible, a screen built from taps and swipes, with slides, sheets and menus that
offer the step that matters next.

## Decisions (each chosen by the user from alternatives)

1. **One stage, no scroll** (chosen over a paged step-by-step wizard and over compressing the
   existing `List`). Everything else lives in sheets, menus or a drawer.
2. **No Single | Batch switch.** The mode is inferred from what is picked: on a try-on pipeline the
   Outfit and Driver cards accept many; every other card accepts one. One outfit and one driver is
   one job.
3. **One Continue button.** It validates on tap and navigates on success. The separate Validate
   step is gone (chosen over background auto-validation, which calls the server on every change and
   races "The draft changed during validation").
4. **Chained picking only on a fresh draft.** When every slot is empty, picking a single-select
   slot advances the sheet to the next empty required slot. Editing one slot of a filled draft
   closes the sheet as it does today.

## 1. Layout

```
┌──────────────────────────────────┐
│ 4 jobs  [Try-on Motion·Gemini ▾] ⋯│  toolbar
├──────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐      │
│  │Character │  │ Outfit ×3│      │  2-column grid; card height is
│  │          │  │ ▣▣▣ ●○○  │      │  computed to fill the space left
│  └──────────┘  └──────────┘      │
│  ┌──────────┐  ┌──────────┐      │
│  │Driver ×2 │  │BG  opt.  │      │
│  └──────────┘  └──────────┘      │
├──────────────────────────────────┤
│ ▴ Batch · 4   ▣▣▣▣               │  basket drawer (collapsed)
│ 3 outfits × 2 drivers = 6 videos │
│ [ Add 6 jobs ]    [ Continue ]   │  glass action bar
└──────────────────────────────────┘
```

- **Slot cards fit the screen.** Pipelines have one to four slots. The cards go in a two-column
  grid, and their size comes from the space left (`GeometryReader`) rather than a fixed 3:4, so
  the stage does not scroll on any iPhone from SE to Pro Max.
- **Toolbar.** The principal slot, where the mode switch was, becomes a **Pipeline · Provider
  chip**. Tapping it opens one medium-detent sheet with two sections, Pipeline and Provider, which
  replaces today's Settings section. The job count stays leading. The stale-refresh button and the
  `⋯` menu (Clear draft) stay trailing.
- **Errors and messages** become a floating banner at the top that dismisses itself or on an
  upward swipe. They no longer take a list section and push content down.
- **The basket is a custom drawer attached above the action bar, not a system sheet.** A system
  sheet covers the tab bar, and iOS shows one sheet at a time, so opening any picker would dismiss
  the basket. Collapsed, the drawer is one line ("Batch · 4" plus small thumbnails). Tapping it or
  dragging it up expands it over the card grid as the job list, and dragging it down collapses it.

## 2. Gestures

| Where | Tap | Long press | Swipe |
|---|---|---|---|
| Empty card | open the picker | — | — |
| Single-select card | open the picker to change it | peek + menu (Clear) | — |
| Multi-select card (Outfit/Driver) | open the multi-picker | menu for the item shown: saved try-on / Remove | horizontal: page through the items, with page dots |
| Basket job | open the detail (the existing `BatchEntryDetail`) | peek | trailing (swipe left): Drop, with confirmation |
| Basket drawer | expand / collapse | — | up / down |

The ✕ glyph on a filled tile is removed: it was below the 44 pt minimum. Clearing moves into the
long-press menu, where Batch mode's outfit tiles already keep Remove.

## 3. Behavior

### One source for the crossed roles

On a pipeline where `BatchComposer.supports(pipeline)`, the Outfit and Driver cards read and write
only `BatchComposer` (`outfits`, `drivers`), whether one item is picked or many. Add always goes
through `composer.run()`. With one outfit this adds exactly one job: `pending(in:)` builds one
`CrossStep`, and `run()` PATCHes it and calls add-to-batch once. Pipelines without a character +
outfit pair keep `store.addToBatch()` for the edited job.

The seed follows from this. Single mode's draft-level `tryonSeed` badge goes away. The seed is
per outfit, chosen from the outfit card's long-press menu and shown as a badge on the card
(`BatchMediaTile`'s existing "Saved try-on" badge).

### Saved try-ons "Use in job"

`SavedTryonsView.use(_:)` writes the draft directly (character, outfit and `tryonSeed` through the
library store) and today forces `newJobMode = .single`. MotionKit gains
`BatchComposer.adoptDraftSelection()`: when the composer has no outfits and the draft carries an
outfit slot, that outfit becomes the composer's single outfit, with the draft's `tryonSeed` as its
seed. When the composer has no drivers and the pipeline has a driver role, the draft's driver slot
is adopted the same way. Each adopted slot, and the seed with the outfit, is then cleared on the
draft in one PATCH, so the composer is the only place the crossed roles live. A dimension that
already has a selection is left alone, so a hand-built selection is never overwritten. New Job
calls it whenever the draft's outfit or driver slot changes. `NewJobMode` and
`AppModel.newJobMode` are deleted.

`BatchComposer.reset()` empties the selection. Clear draft calls it next to `store.clear()`,
because the selection now lives outside the draft. Selecting a pipeline without a character +
outfit pair calls it too, since its cards cannot show the selection.

### The action bar

- **Add N** (secondary): queue what is being composed and keep composing.
- **Continue** (primary): run everything visible plus the basket. If a composition is pending, it
  is added first. Then the draft is validated, with a spinner on the button, and a valid draft
  navigates to `RunFlowView`. On a refusal it stays: the slot warning shows on its card, and the
  message shows in the banner. A stale validation shows the banner and does not navigate.
- With a required slot missing, both buttons are disabled, and a line names what is missing
  ("Pick Character, Outfit to continue").
- The summary line (`3 outfits × 2 drivers = 6 videos · 3 try-ons`), build progress, the failure
  line with its Continue-to-resume, and the 12-job cap reason stay, as `BatchRunBar` shows them now.

### Chained picking

It triggers only when the draft has no filled slot and the composer is empty. Picking a
single-select slot advances the same sheet to the next empty required slot, and the title shows
the progress ("Character ✓ → Outfit"). A multi-select slot shows a Next button instead of
advancing on each tick. The sheet closes when no required slot is empty. Dragging the sheet down
exits at any point.

## 4. Code shape (`ios/MotionApp/NewJob/`)

| File | Role |
|---|---|
| `NewJobView` | rewritten small: toolbar, banner, composes grid + drawer + action bar |
| `SlotCard` (new) | one card for single- and multi-select slots (stacked items, horizontal paging, long-press menu); replaces `SlotTile`, `SlotRow` and the batch `outfitTile`/`driverTile` |
| `SlotCardGrid` (new) | sizes the cards to the available space |
| `BasketDrawer` (new) | the basket drawer; reuses `BatchEntryRow` and `BatchEntryDetail` |
| `SettingsChip`, `SettingsSheet` | reuse `PipelineChoiceList` and `ProviderChoiceList`; the rest of `PipelinePicker` goes |
| `NewJobActionBar` | absorbs `BatchRunBar`; the Add / Continue logic above |
| `PickerChain` (new) | drives chained picking |
| deleted | `BatchComposerSection`, `BatchRunBar` |

MotionKit gains `BatchComposer.adoptDraftSelection()`, `BatchComposer.reset()` and a pure
`NewJobState` (what each card holds, what is missing, what Add and Continue would do). No server route, no field, nothing under
`scripts/**`, so the deploy-bot workflow does not run.

The seed observers (`composer.seedKey`, `library.loaded`) stay in `NewJobView` for the reason their
comment gives. The mode split they were placed above is gone, but the reason still applies.

## 5. Verification

- `make ios-test`: unit tests for `adoptDraftSelection` (adopts into an empty composer and clears
  the draft slots, ignores a non-empty dimension, carries the seed or nil), `reset`, and
  `NewJobState`.
- `make ios-build`.
- `make ios-ui-test`: Phase 4/5/6 smokes use `newjob.mode`, `"Validate"`, `"Add to batch"` and the
  Settings `"Pipeline"` row. They are rewritten for the new flow. The identifiers `batch.run`,
  `batch.summary`, `batch.pickOutfits`, `batch.pickDrivers`, `outfit.pick.*`, `driver.pick.*`,
  `newjob.actionBar`, `newjob.continueToRun` and `newjob.more` are kept, and each card keeps
  `SlotText`'s accessibility label and value ("Missing required").
- Simulator screenshots on iPhone SE (3rd gen) and a Pro Max, with a four-slot pipeline and a
  full basket, as the evidence that the stage does not scroll.
- Install on the phone for the user to try the gestures. This is the only real test of the swipe
  and drawer feel.

## Out of scope

- Any server-side change, including making validation cheaper or implicit.
- The run flow after Continue (`RunFlowView`) and the Materials tab.
- Pair (1:1) mode, which remains replaced by the N × M cross build.
