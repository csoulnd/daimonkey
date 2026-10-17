"""命令行入口。

用法：
  python3 -m collector --init-db      仅建库
  python3 -m collector --collect      仅采集+评分+LRU
  python3 -m collector --build        仅导出 API + 建站
  python3 -m collector --collect-news 仅采集新闻+LRU+导出+建站（不触热度数据源）
  python3 -m collector --update       全流程（前端"更新数据"按钮即调用此命令）
  python3 -m collector --assistant news-digest
                                     内部通道执行助手工作流（走 internal 配额；
                                     news-digest=本周新闻 AI 摘要草稿）
"""
import argparse
import json as _json

from .config import ensure_dirs


def main() -> None:
    ap = argparse.ArgumentParser(prog="collector",
                                 description="InferTech 观察站采集与建站")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--init-db", action="store_true", help="仅初始化数据库")
    g.add_argument("--collect", action="store_true", help="仅采集+评分+LRU")
    g.add_argument("--build", action="store_true", help="仅导出 API 与构建站点")
    g.add_argument("--collect-news", action="store_true",
                   help="仅采集新闻并建站（不触热度数据源）")
    g.add_argument("--update", action="store_true", help="全流程更新（默认）")
    g.add_argument("--assistant", metavar="TASK",
                   help="内部通道执行助手工作流: news-digest|content-polish|site-notes")
    args = ap.parse_args()

    ensure_dirs()
    if args.init_db:
        from .db import init_db
        init_db()
        print("数据库已初始化")
        return
    if args.assistant:
        from . import assistant as _assistant
        task_map = {"news-digest": ("news_digest_summary", {}),
                    "content-polish": ("content_polish", None),
                    "site-notes": ("site_improvement_notes", {})}
        if args.assistant not in task_map:
            ap.error(f"未知助手任务: {args.assistant}")
        name, payload = task_map[args.assistant]
        if payload is None:  # content-polish 需要从 stdin 读文本
            payload = {"text": _read_stdin_text()}
        try:
            r = _assistant.run_internal(name, payload, channel="internal")
            print(r["reply"])
            print(f"[tokens ~{r['tokens_in']}+{r['tokens_out']}]")
        except _assistant.AssistantError as e:
            print(f"助手执行失败: {e}")
            raise SystemExit(1)
        return
    if args.collect:
        from .pipeline import run_collect
        r = run_collect()
    elif args.build:
        from .pipeline import run_build
        run_build()
        print("站点已构建到 dist/")
        return
    elif args.collect_news:
        from .pipeline import run_news_update
        r = run_news_update()
        print(f"新闻更新完成: {r['state']} | 源 ok={r['sources_ok']}"
              f" failed={r['sources_failed']} | HTTP 请求 {r['http_requests']} 次")
        for line in r.get("notes", []):
            print("  " + line)
        return
    else:
        from .pipeline import run_update
        r = run_update()
        print(f"更新完成: {r['state']} | 指标 {r['metrics']} 行 | "
              f"源 ok={r['sources_ok']} failed={r['sources_failed']} | "
              f"HTTP 请求 {r['http_requests']} 次 | 峰值内存 {r['peak_mem_mb']}MB")
        for line in r.get("notes", []):
            print("  " + line)


def _read_stdin_text() -> str:
    import sys
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


if __name__ == "__main__":
    main()
