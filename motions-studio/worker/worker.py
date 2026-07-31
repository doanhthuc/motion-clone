#!/usr/bin/env python3
"""Compatibility entrypoint for the Linux/CUDA worker."""

from worker_runtime.linux import main


if __name__ == "__main__":
    main()
