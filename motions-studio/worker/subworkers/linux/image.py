#!/usr/bin/env python3
"""Linux/CUDA sub-worker for image edit and try-on jobs."""

from subworkers.launcher import set_default_job_types

set_default_job_types("tryon,create-image,product-overlay")

from worker_runtime.linux import main


if __name__ == "__main__":
    main()
