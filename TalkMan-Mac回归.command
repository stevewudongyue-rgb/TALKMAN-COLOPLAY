#!/bin/bash
# 在原生 Apple 芯片桌面会话运行；与云端使用同一回归入口。
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [ ! -x TalkMan-venv-mac/bin/python ]; then
  echo "请先运行：python3 TalkMan-构建Mac.py --test-dmg"
  exit 1
fi
TalkMan-venv-mac/bin/python -u -c 'import faulthandler, sys, unittest; faulthandler.dump_traceback_later(90, exit=True); unittest.main(module=None, argv=sys.argv, verbosity=2)' test_talkman_coloplay_revision63
TalkMan-venv-mac/bin/python -m unittest test_talkman_coloplay_recovery test_talkman_input_locations test_talkman_input_picker test_talkman_coloplay_preview test_talkman_coloplay_revision59 test_talkman_coloplay_revision60.CoordinateTests test_talkman_coloplay_revision61 test_talkman_cartridge_update test_talkman_usb_audio -v
TalkMan-venv-mac/bin/python -u -c 'import faulthandler, sys, unittest; faulthandler.dump_traceback_later(90, exit=True); unittest.main(module=None, argv=sys.argv, verbosity=2)' test_talkman_ai_edit
TalkMan-venv-mac/bin/python -u -c 'import faulthandler, sys, unittest; faulthandler.dump_traceback_later(90, exit=True); unittest.main(module=None, argv=sys.argv, verbosity=2)' test_talkman_app.EditorTests.test_pinned_main_window_opens_and_closes_settings
for module in test_talkman_delivery_confirm test_talkman_sync test_talkman_voice test_talkman_updates test_talkman_target test_talkman_coloplay test_talkman_editor test_talkman_coloplay_revision48 test_talkman_coloplay_revision47 test_talkman_coloplay_revision46 test_talkman_coloplay_revision44 test_talkman_coloplay_revision43 test_talkman_coloplay_revision45 test_talkman_target_macos test_talkman_macos_setup test_talkman_app; do
  TalkMan-venv-mac/bin/python -u -c 'import faulthandler, sys, unittest; faulthandler.dump_traceback_later(180, exit=True); unittest.main(module=sys.argv.pop(), verbosity=2)' "$module"
done
