import os
import sys

import xbmcaddon

sys.path.insert(0, os.path.join(xbmcaddon.Addon().getAddonInfo('path'), 'resources', 'lib'))

from tms.plugin import run  # noqa: E402

run(sys.argv)
