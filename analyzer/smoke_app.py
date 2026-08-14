"""UI 无头冒烟：构造窗口、落子/导航/重绘、关闭。不启动 KataGo，捕获 UI 代码 bug。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import GoAnalyzer

app = GoAnalyzer()
app.update_idletasks()
# 落两子 + 导航 + 清空，全程触发 redraw（棋子/坐标/候选标记绘制路径）
app.play(3, 3)
app.play(15, 15)
app.do_undo()
app.do_redo()
app.play(3, 15)
app._clear_analysis()
app._draw_winrate_bar(0.4)
app._render_analysis({"rootInfo": {"winrate": 0.42, "scoreLead": -1.2, "currentPlayer": "W"},
                      "moveInfos": [
                          {"order": 0, "move": "D4", "winrate": 0.43, "scoreLead": -1.1, "pv": ["D4", "Q16", "R3"]},
                          {"order": 1, "move": "Q16", "winrate": 0.41, "scoreLead": -1.5, "pv": ["Q16"]},
                      ]})
app.do_reset()
app.update()
app.update_idletasks()
print("app smoke OK — UI constructs, plays, navigates, renders analysis, resets, closes.")
app.destroy()
