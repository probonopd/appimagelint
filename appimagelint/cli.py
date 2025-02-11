import argparse
import logging
import os
import sys

from appimagelint.checks import IconsCheck
from .cache.runtime_cache import AppImageRuntimeCache
from .reports import JSONReport
from .services.result_formatter import ResultFormatter
from .models import AppImage
from . import _logging
from .checks import GlibcABICheck, GlibcxxABICheck


def get_version():
    try:
        import pkg_resources
        version = pkg_resources.require("appimagelint")[0].version
    except ImportError:
        version = "unknown"

    APPDIR = os.environ.get("APPDIR", None)

    git_commit = "unknown"

    if APPDIR is not None:
        try:
            with open(os.path.join(APPDIR, "commit")) as f:
                git_commit = f.read().strip(" \n\r")
        except FileNotFoundError:
            pass

    version += "-git" + git_commit

    return version


def parse_args():
    parser = argparse.ArgumentParser(
        prog="appimagelint",
        description="Run compatibility and other checks on AppImages automatically, "
                    "and provide human-understandable feedback"
    )

    parser.add_argument("--version",
                        dest="display_version",
                        action="version", version=get_version(),
                        help="Display version and exit"
    )

    parser.add_argument("--debug",
                        dest="loglevel",
                        action="store_const", const=logging.DEBUG, default=logging.INFO,
                        help="Display debug messages")

    parser.add_argument("--log-source-location",
                        dest="log_message_locations",
                        action="store_const", const=True, default=False,
                        help="Print message locations (might be picked up by IDEs to allow for jumping to the source)")

    parser.add_argument("--log-timestamps",
                        dest="log_timestamps",
                        action="store_const", const=True, default=False,
                        help="Log timestamps (useful for debugging build times etc.)")

    parser.add_argument("--force-colors",
                        dest="force_colors",
                        action="store_const", const=True, default=False,
                        help="Force colored output")

    parser.add_argument("--json-report",
                        dest="json_report", nargs="?", default=None,
                        help="Write results to file in machine-readable form (JSON)")

    parser.add_argument("path",
                        nargs="+",
                        help="AppImage to review")

    args = parser.parse_args()

    return args


def run():
    args = parse_args()

    if getattr(args, "display_version", False):
        print(get_version())
        return

    # setup
    _logging.setup(
        args.loglevel,
        with_timestamps=args.log_timestamps,
        force_colors=args.force_colors,
        log_locations=args.log_message_locations,
    )

    # get logger for CLI
    logger = _logging.make_logger("cli")

    # need up to date runtime to be able to read the mountpoint from stdout (was fixed only recently)
    # also, it's safer not to rely on the embedded runtime
    custom_runtime = AppImageRuntimeCache.get_data()

    # results logs are written immediately, but maybe we want to generate additional reports
    # for this purpose, we collect all results
    results = {}

    try:
        for path in args.path:
            results[path] = {}

            logger.info("Checking AppImage {}".format(path))

            appimage = AppImage(path, custom_runtime=custom_runtime)

            kwargs = dict()
            if args.force_colors:
                kwargs["use_colors"] = True

            formatter = ResultFormatter(**kwargs)

            for check_cls in [GlibcABICheck, GlibcxxABICheck, IconsCheck]:
                logger.info("Running check \"{}\"".format(check_cls.name()))
                check = check_cls(appimage)

                results[path][check] = []

                for testres in check.run():
                    results[path][check].append(testres)
                    check.get_logger().info(formatter.format(testres))

        if args.json_report:
            report = JSONReport(results)
            report.write(args.json_report)

    except KeyboardInterrupt:
        logger.critical("process interrupted by user")
        sys.exit(2)
