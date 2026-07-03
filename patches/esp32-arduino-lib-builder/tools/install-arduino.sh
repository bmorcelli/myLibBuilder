#/bin/bash

source ./tools/config.sh

#
# The arduino-esp32 checkout at $AR_COMPS/arduino is cloned, pinned to the
# commit from versions.txt, and patched by myLibBuilder's run.py before
# build.sh ever runs. Skip the upstream clone/fetch/checkout/pull logic here
# so it doesn't get moved away from that pinned, patched state.
#
if [ ! -d "$AR_COMPS/arduino/.git" ]; then
	echo "ERROR: arduino-esp32 checkout not found at $AR_COMPS/arduino. It must be prepared by run.py (myLibBuilder) before build.sh runs." 1>&2
	exit 1
fi

echo "Using arduino-esp32 checkout pinned by myLibBuilder at $AR_COMPS/arduino ($(git -C "$AR_COMPS/arduino" rev-parse --short HEAD))"
