"""The Cloud Run Job side of Plumbline: `orchestrator.py` sequences the
eleven-agent fleet for one run, `worker.py` is the process entrypoint
Cloud Run actually invokes (reads `PLUMBLINE_RUN_ID`, calls the
orchestrator, reports the outcome). See each module's own docstring.
"""
