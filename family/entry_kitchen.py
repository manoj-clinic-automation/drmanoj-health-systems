#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entry_kitchen.py -- the Family Kitchen at https://family.dr-manoj.in/kitchen/.

FAMILY_EDITION_V1 (Phase C). Run by family-kitchen.service as the Linux user
fam_kitchen, which can write /srv/family/kitchen and nothing else. The pool
(kitchen.py) behind the same path-prefix wrapper the member apps use.
Python 3.9.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.environ.setdefault("KITCHEN_DIR", "/srv/family/kitchen")
import kitchen  # noqa: E402
import family_prefix  # noqa: E402

flask_app = kitchen.make_app()
application = family_prefix.PrefixApp(flask_app.wsgi_app, "/kitchen",
                                       family_prefix.route_segments(flask_app.url_map))
