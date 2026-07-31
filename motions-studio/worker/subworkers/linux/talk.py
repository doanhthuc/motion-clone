#!/usr/bin/env python3
"""Linux/CUDA sub-worker for talking-head and story-film jobs."""

from subworkers.launcher import set_default_job_types

set_default_job_types("talk,face-motion,story-film")

from worker_runtime.linux import main


if __name__ == "__main__":
    main()
