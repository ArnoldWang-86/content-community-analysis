# -*- coding: utf-8 -*-
r"""run_all_collect.py —— 一键串行补齐三批数据（A1 → A2 → A3）

为什么必须串行：同时跑多个采集进程会让请求频率翻倍，触发 B站限流，
接口会开始返回大量重复数据（实测重复率高达 96%，数据直接作废）。

用法:
    python src\run_all_collect.py # 全流程，约 6 小时
    python src\run_all_collect.py --only A1 # 只跑某一步
    python src\run_all_collect.py --sleep 0.8 # 调慢 A1（默认 0.5）

每步都支持断点续跑，中途中断后重跑会跳过已完成的部分。
"""
import argparse, os, subprocess, sys, time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
LOG = os.path.join(ROOT, "output", "collect_log.txt")
os.makedirs(os.path.dirname(LOG), exist_ok=True)

SEP = "=" * 70


def log(msg, echo=True):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if echo:
        print(line, flush=True)


def other_collector_running():
    """检测是否有别的采集进程在跑"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'collect|danmaku' } | "
             "Where-Object { $_.ProcessId -ne " + str(os.getpid()) + " } | "
             "Measure-Object).Count"],
            capture_output=True, text=True, timeout=30)
        return int((r.stdout or "0").strip() or 0) > 0
    except Exception:
        return False


def count_lines(path):
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return n


STEPS = {
    "A1": {
        "name": "UP主粉丝数",
        "out": r"data\raw\ups.jsonl",
        "expect": 11841,
        "cmd": lambda sleep: [PY, "-u", r"src\02_collect_ups.py", "--sleep", str(sleep)],
        "eta": lambda sleep: f"约 {11841*(sleep+0.4)/3600:.1f} 小时",
    },
    "A2": {
        "name": "视频详情（投币/分享/cid）",
        "out": r"data\raw\detail.jsonl",
        "expect": 19456,
        "cmd": lambda sleep: [PY, "-u", r"src\03_collect_detail.py"],
        "eta": lambda sleep: "约 2.5 小时",
    },
    "A3": {
        "name": "弹幕文本（项目2语料）",
        "out": r"data\raw\danmaku.jsonl",
        "expect": 4000,
        "cmd": lambda sleep: [PY, "-u", r"src\04_collect_danmaku.py", "--limit", "4000"],
        "eta": lambda sleep: "约 30 分钟",
    },
}


def run_step(key, sleep):
    st = STEPS[key]
    out = os.path.join(ROOT, st["out"])
    before = count_lines(out)

    print()
    print(SEP)
    print(f" 【{key}】{st['name']} 预计 {st['eta'](sleep)}")
    print(f" 输出文件: {st['out']} (当前已有 {before:,} 行)")
    print(SEP)
    log(f"开始 {key} {st['name']}")

    t0 = time.time()
    rc = subprocess.call(st["cmd"](sleep), cwd=ROOT)
    el = time.time() - t0

    after = count_lines(out)
    print()
    log(f"{key} 结束 返回码={rc} 耗时 {el/60:.1f} 分钟 "
        f"新增 {after-before:,} 行 文件总计 {after:,} 行")
    if rc != 0:
        log(f" [警告] {key} 返回非零，但可断点续跑")
    return rc, after


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, default="", help="只跑 A1/A2/A3 之一")
    ap.add_argument("--sleep", type=float, default=0.5, help="A1 的请求间隔秒数")
    a = ap.parse_args()

    print()
    print("#" * 70)
    print("# 内容社区项目 —— 一键补齐数据")
    print(f"# 开始时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("#" * 70)
    log("=" * 60)
    log("run_all_collect 启动")

    if other_collector_running():
        print()
        print(" [中止] 检测到已有采集进程在运行！")
        print(" 同时跑多个会触发限流，导致数据大量重复作废。")
        print(" 请先等它跑完，或在任务管理器结束 python.exe。")
        print()
        return 1

    keys = [a.only] if a.only else ["A1", "A2", "A3"]
    for k in keys:
        if k not in STEPS:
            print(f"未知步骤: {k}"); return 1

    results = []
    total0 = time.time()
    for k in keys:
        rc, n = run_step(k, a.sleep)
        results.append((k, STEPS[k]["name"], rc, n))
        if rc != 0:
            log(f"{k} 失败，继续下一步（各步相互独立或可续跑）")

    print()
    print(SEP)
    print(" 全部完成")
    print(SEP)
    print(f" {'步骤':<6}{'内容':<26}{'返回码':>8}{'文件行数':>12}")
    for k, name, rc, n in results:
        print(f" {k:<6}{name:<26}{rc:>8}{n:>12,}")
    print()
    print(f" 总耗时: {(time.time()-total0)/60:.1f} 分钟")
    print(f" 日志: {LOG}")
    print()
    print(" 下一步: python src\\05_clean_to_db.py (清洗入库)")
    print(SEP)
    log(f"全部结束，总耗时 {(time.time()-total0)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
