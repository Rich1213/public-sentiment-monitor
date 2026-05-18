"""
main.py — 台灣便利超商輿情監控系統（CLI 入口）

本檔案為薄包裝層，將執行邏輯委託給 worker/runner.py。
業務邏輯請直接參閱 worker/runner.py。

用法：
  python main.py                          # 監控所有品牌
  python main.py -k 7-ELEVEN 全家         # 只監控指定品牌
  python main.py --fresh                  # 強制重新採集（忽略去重）

等價命令（模組方式）：
  python -m worker.runner
  python worker/runner.py
"""

from worker.runner import main

if __name__ == "__main__":
    main()
