# Accessory Reference Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a mask-guided accessory correction stage that keeps a fixed high-confidence reference across every visible interval of a wrist accessory, including after re-entry.

**Architecture:** Wan Animate remains responsible for the full motion. A follow-up VACE stage receives the generated video, an immutable accessory reference, and a per-frame mask video; it processes all visible intervals in overlapping chunks and composites only the corrected region. Low-confidence references fail before GPU submission.

**Tech Stack:** ComfyUI-WanVideoWrapper VACE, SAM3/video tracking, Python worker, batchlib manifest registry, unittest.

**Spec:** `docs/superpowers/specs/2026-09-10-camera-aware-tryon-motion-design.md` (conditioning and camera-motion contracts).

## Global Constraints

- Never use a generated frame as the accessory reference.
- Reject a reference that fails the measured sharpness/area threshold.
- Preserve the original video outside the accessory mask.
- Do not claim visual success without a GPU A/B clip.
- Install and pin the VACE model before enabling the pipeline in Telegram.

---

### Task 1: Provision and gate VACE

Add the VACE custom node/model to both worker image manifests and setup scripts; add a startup capability check that reports the exact missing model/node before a job is claimed.

### Task 2: Add reference and mask preparation

Create pure helpers for reference quality scoring, visible-interval extraction, overlap chunking, and mask normalization. Add unittest fixtures for clear, blurry, absent, and re-entry cases.

### Task 3: Build the VACE correction workflow

Implement a worker workflow using `WanVideoVACEEncode(input_frames, ref_images, input_masks)` and the verified VACE model. Process each chunk with the same reference, then composite the corrected mask region over the Wan output.

### Task 4: Register the batch pipeline

Add `accessory-correction` and `tryon-camera-motion-accessory-fix-enhance` stages to `scripts/batchlib/pipelines.py`, manifest validation, Telegram slot handling, and parameter metadata. Required inputs are generated motion video, immutable accessory reference, and mask/track source.

### Task 5: Verify locally and on one GPU clip

Run focused unit tests, `make batch-test`, registry gates, and secret scan. Run one 5-second GPU A/B with a wrist re-entry, inspect every visible interval, then enable the pipeline for Telegram only if the model and mask gates pass.
