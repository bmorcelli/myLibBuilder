#/bin/bash

source ./tools/config.sh

#
# The TinyUSB checkout at $AR_COMPS/arduino_tinyusb/tinyusb is cloned and pinned
# to the commit from versions.txt by myLibBuilder's run.py before build.sh runs.
# The upstream script clones it and does "git pull --ff-only" on master, which
# moves it off that pinned commit (and can mismatch the arduino-esp32 sources,
# e.g. missing audio20_control_request_t). Skip the clone/pull so the pinned,
# matching commit is what actually compiles.
#
TINYUSB_REPO_DIR="$AR_COMPS/arduino_tinyusb/tinyusb"
if [ ! -d "$TINYUSB_REPO_DIR/.git" ]; then
	echo "ERROR: TinyUSB checkout not found at $TINYUSB_REPO_DIR. It must be prepared by run.py (myLibBuilder) before build.sh runs." 1>&2
	exit 1
fi

echo "Using TinyUSB checkout pinned by myLibBuilder at $TINYUSB_REPO_DIR ($(git -C "$TINYUSB_REPO_DIR" rev-parse --short HEAD))"
