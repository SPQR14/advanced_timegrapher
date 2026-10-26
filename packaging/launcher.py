"""Entry script of the packaged application (PyInstaller, see adv-tg.spec).

``freeze_support()`` must run before anything else: the capture and analysis child processes
are started with the "spawn" method, and in a frozen build each child re-runs this script.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from adv_tg.main import main

    sys.exit(main())
