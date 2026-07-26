#!/usr/bin/env python3
"""Local zip-slip proof for battle-004.

Executed by the judge as:
    python red_exploit_submission.py --expect-vulnerable

This script intentionally uses only the Python standard library and imports the
public target via the literal binding required by the arena.
"""

import argparse
import os
import sys
