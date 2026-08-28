# -*- coding: utf-8 -*-
"""Windows 7 / Python 3.8 compatible application entry point."""
from __future__ import print_function
import os
import sys

# CP65001 is unreliable on Windows 7. Do not force UTF-8 console output.
if os.name == 'nt':
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(errors='replace')
            sys.stderr.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

try:
    from app import create_app
    app = create_app()
except Exception as exc:
    import traceback
    print('ZJAI-Agent startup error: %s' % exc)
    traceback.print_exc()
    raise


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True, use_reloader=False)
