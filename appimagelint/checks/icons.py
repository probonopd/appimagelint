import glob
import os.path as op
import re
from typing import Tuple

from PIL import Image

from appimagelint._logging import make_logger
from appimagelint.models import TestResult
from ..models import AppImage
from . import CheckBase


class IconsCheck(CheckBase):
    _VALID_RESOLUTIONS = (8, 16, 32, 48, 56, 64, 128, 192, 256, 384, 512)

    def __init__(self, appimage: AppImage):
        super().__init__(appimage)

    @staticmethod
    def name():
        return "Icons validity and location check"

    def run(self):
        logger = self.get_logger()

        with self._appimage.mount() as mountpoint:
            # find desktop file, get name of icon and look for it in AppDir root
            desktop_files = glob.glob(op.join(mountpoint, "*.desktop"))

            # we can of course check the validity of all icon files we find, but there's always one main icon that is
            # referenced from the desktop file
            main_icon_name = None

            if not desktop_files:
                logger.error("Could not find desktop file in root directory")

            else:
                logger.debug("Found desktop files: %s", desktop_files)

                desktop_file = desktop_files[0]
                logger.info("Extracting icon name from desktop file: %s", desktop_file)

                with open(desktop_file) as f:
                    # find Icon= entry and get the name of the icon file to look for
                    # we don't need to check things like "is there just one Icon entry" etc., that's the job of another
                    # test
                    desktop_file_contents = f.read()

                    # note for self: Python's re doesn't do multiline unless explicitly asked for with re.MULTILINE
                    match = re.search(r"Icon=(.+)", desktop_file_contents)

                    if not match:
                        logger.error("Could not find Icon= entry in desktop file")
                    else:
                        main_icon_name = match.group(1)

            # to be able to filter out non-icon files with the same prefix in the AppDir root
            known_image_exts = ("png", "xpm", "svg", "jpg")

            # assuming test broke
            # now prove me wrong!
            root_icon_valid = False

            if main_icon_name is not None:
                if "/" in main_icon_name:
                    logger.error("main icon name is a path, not a filename (contains /)")
                else:
                    # properly escape some "magic" characters in the original filename so they won't be interpreted by glob
                    fixed_main_icon_name = glob.escape(main_icon_name)

                    # build glob pattern
                    pattern = "{}.*".format(fixed_main_icon_name)

                    logger.debug("Trying to find main icon in AppDir root, pattern: {}".format(repr(pattern)))

                    appdir_root_icons = glob.glob(op.join(mountpoint, pattern))

                    if not appdir_root_icons:
                        logger.error("Could not find suitable icon for desktop file's Icon= entry")

                    else:
                        # filter out all files with a not-well-known extension
                        appdir_root_icons = [i for i in appdir_root_icons if
                                             op.splitext(i)[-1].lstrip(".") in known_image_exts]

                        if len(appdir_root_icons) > 1:
                            logger.warning("Multiple matching icons found in AppDir root, checking all")

                        main_icon_check_results = []
                        for icon in appdir_root_icons:
                            valid = self._check_icon_for_valid_resolution(icon)

                        # if only one of the checks failed, we can't guarantee a working root icon
                        root_icon_valid = all(main_icon_check_results)

            yield TestResult(root_icon_valid, "icons.valid_appdir_root_icon", "Valid icon in AppDir root")

            # next, check that .DirIcon is available and valid
            dotdiricon_valid = self._check_icon_for_valid_resolution(op.join(mountpoint, ".DirIcon"))
            yield TestResult(dotdiricon_valid, "icons.valid_dotdiricon", "Valid icon file in .DirIcon")

            # now check all remaining icons in usr/share/icons/...
            other_icons_root_path = op.join(mountpoint, "usr/share/icons/**/*.*")
            other_icons = glob.glob(other_icons_root_path, recursive=True)

            # assume everything works
            # prove me wrong!
            other_icons_checks_success = True

            for abs_path in other_icons:
                # check if this icon even belongs to here
                filename = op.basename(abs_path)
                split_fname = op.splitext(filename)

                # not an error, but means we don't have to process that file any further
                if split_fname[0] != main_icon_name:
                    logger.warning("Icon found whose file name doesn't match the Icon= entry in desktop file: %s",
                        rel_path)

                else:
                    # also just a warning
                    if split_fname[1].lstrip(".") not in known_image_exts:
                       logger.warning("Icon has invalid extension: %s", split_fname[1])

                    logger.debug("checking whether icon has good resolution in general")
                    if not self._check_icon_for_valid_resolution(abs_path):
                        logger.warning("icon %s has invalid resolution", abs_path)
                        other_icons_checks_success = False

                    logger.debug("checking whether icon is in correct location")
                    rel_path = op.relpath(abs_path, op.join(mountpoint, "usr/share/icons"))

                    # split path into the interesting components: icon theme, resolution and actual filename
                    split_path = rel_path.split("/")

                    # find resolution component in split path
                    path_res = None

                    def extract_res_from_path_component(s):
                        return tuple([int(i) for i in s.split("x")])

                    if len(split_path) != 4 or split_path[2] != "apps":
                        logger.warning("Icon %s is in non-standard location", rel_path)
                    else:
                        try:
                            path_res = extract_res_from_path_component(split_path[1])
                        except:
                            pass

                    if not path_res:
                        # something's definitely broken
                        other_icons_checks_success = False

                        logger.warning("Could not find icon resolution at expected position in path, "
                                       "trying to guess from entire path")
                        for comp in split_path:
                            try:
                                path_res = extract_res_from_path_component(comp)
                            except:
                                pass
                            else:
                                break

                    if not path_res:
                        other_icons_checks_success = False
                        logger.error("Could not extract resolution from icon path,"
                                     "should be usr/share/icons/<theme>/<res>/apps/<name>.<ext>")

                    else:
                        # make sure extracted resolution corresponds to the file's resolution
                        actual_res = self._get_icon_res(abs_path)
                        if actual_res != path_res:
                            other_icons_checks_success = False
                            logger.error("Icon resolution doesn't match resolution in path: %s (file resolution is %s)",
                                         path_res, actual_res)

            yield TestResult(other_icons_checks_success, "icons.valid_other_icons", "Other integration icons valid")

    @staticmethod
    def get_logger():
        return make_logger("icon_check")

    def _get_icon_res(self, icon_path: str) -> Tuple[int]:
        logger = self.get_logger()

        try:
            logger.debug("Opening image: %s", icon_path)
            im = Image.open(icon_path)

            logger.debug("format: %s -- resolution: %s, mode: %s", im.format, im.size, im.mode)
            return im.size

        except:  # noqa
            logger.exception("Failed to identify icon %s", icon_path, )

    def _check_icon_for_valid_resolution(self, icon_path: str) -> bool:
        res = self._get_icon_res(icon_path)

        if not res:
            return False

        return res[0] in self._VALID_RESOLUTIONS and res[1] in self._VALID_RESOLUTIONS
