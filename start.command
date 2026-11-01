#!/bin/bash
# macOS: double-click this file in Finder to start the app.
# 首次打开如被系统拦截：右键点击本文件 -> 打开。
cd "$(dirname "$0")" || exit 1
/bin/bash ./start.sh ${1+"$@"}
status=$?
if [ "${status}" -ne 0 ]; then
  echo ""
  echo "Launcher exited with code ${status}. Press Enter to close this window. / 启动失败，按回车关闭窗口。"
  read -r _
fi
exit "${status}"
