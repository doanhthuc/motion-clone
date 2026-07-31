#!/usr/bin/env python3
"""Linux/CUDA sub-worker for video generation and composition jobs."""

from subworkers.launcher import set_default_job_types

# ALD 30/06/2026 - thêm wan-i2v, voiceover, subtitle, enhance: trước đây KHÔNG sub-worker nào khai báo → job queue mãi.
set_default_job_types("teaser,video,bds,concat,teen-flycam,trend-tiktok,wan-i2v,voiceover,subtitle,enhance,reveal")

from worker_runtime.linux import main


if __name__ == "__main__":
    main()
