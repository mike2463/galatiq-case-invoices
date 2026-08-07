"""Local web console over the existing CLI services.

The UI renders stored state only and mutates exclusively through the same service
functions the CLI calls. Importing :mod:`invoice_agents` never requires the ``ui``
extra; submodules here import FastAPI and friends lazily at ``ui`` command time.
"""
