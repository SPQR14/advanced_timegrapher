"""Entry script of the packaged application (PyInstaller, see adv-tg.spec).

``freeze_support()`` must run before anything else: the capture and analysis child processes
are started with the "spawn" method, and in a frozen build each child re-runs this script.
"""

import multiprocessing
import os
import sys
import traceback


def _is_headless_run(argv: list[str]) -> bool:
    return (
        "--smoke-test" in argv
        or os.environ.get("ADV_TG_SMOKE_TEST") == "1"
        or os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen"
        or bool(os.environ.get("ADV_TG_BENCH", "").strip())
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # In windowed PyInstaller builds (console=False / runw.exe), an uncaught exception
    # triggers a blocking Win32 MessageBox that hangs headless CI until the job times out.
    if _is_headless_run(sys.argv):
        try:
            from adv_tg.main import main

            sys.exit(main())
        except SystemExit:
            raise
        except BaseException:
            tb = traceback.format_exc()
            log_dir = os.environ.get("ADV_TG_LOG_DIR", "").strip()
            if log_dir:
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    with open(
                        os.path.join(log_dir, "launcher_error.log"), "a", encoding="utf-8"
                    ) as fh:
                        fh.write(tb + "\n")
                except OSError:
                    pass
            try:
                sys.stderr.write(tb)
                sys.stderr.flush()
            except Exception:
                pass
            sys.exit(1)
    else:
        from adv_tg.main import main

        sys.exit(main())
