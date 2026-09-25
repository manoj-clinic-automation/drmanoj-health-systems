#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_serve.py -- serve one family entry on a loopback port (test suites and the
upgrade self-test only; production runs gunicorn from the systemd units).

    python3 -B _serve.py entry_gut 18211
Python 3.9."""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
mod = importlib.import_module(sys.argv[1])
from werkzeug.serving import make_server  # noqa: E402

srv = make_server("127.0.0.1", int(sys.argv[2]), mod.application, threaded=True)
print("serving %s on %s" % (sys.argv[1], sys.argv[2]), flush=True)
srv.serve_forever()
