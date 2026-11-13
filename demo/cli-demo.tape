# decision-journal CLI 真实运行回放（showcase 可复现）
#
# 录制方式（仓库根目录执行）：
#   vhs demo/cli-demo.tape
# 产物：demo/cli-demo.gif（可直接用于 README / 社交分享）
#
# 说明：CLI 会自动拉起本机后端（~/.decision-journal/backend.log 有日志），
# Demo 模式输出会在界面上如实标注 source=mock；本录像会向本地决策日志写入一条真实记录。
# 开场动画影片的复现管线见 film-b 工程（npx remotion render src/index.ts BieJiuJie ...）。
Output demo/cli-demo.gif

Set FontSize 14
Set Width 920
Set Height 600
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set Padding 18

Type "python scripts/decision_journal.py --question '该不该换工作' --mode rational"
Sleep 500ms
Enter
Sleep 7s

Type "python scripts/decision_journal.py --action stats"
Sleep 300ms
Enter
Sleep 3s
