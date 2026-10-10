"""命令行入口。

用法：
  python3 -m collector --init-db      仅建库
  python3 -m collector --collect      仅采集+评分+LRU
  python3 -m collector --build        仅导出 API + 建站
  python3 -m collector --collect-news 仅采集新闻+LRU+导出+建站（不触热度数据源）
  python3 -m collector --update       全流程（前端"更新数据"按钮即调用此命令）
"""
import argparse

from .config import ensure_dirs


def main() -> None:
    ap = argparse.ArgumentParser(prog="collector",
                                 description="智能体热度指数采集与建站")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--init-db", action="store_true", help="仅初始化数据库")
    g.add_argument("--collect", action="store_true", help="仅采集+评分+LRU")
    g.add_argument("--build", action="store_true", help="仅导出 API 与构建站点")
    g.add_argument("--collect-news", action="store_true",
                   help="仅采集新闻并建站（不触热度数据源）")
    g.add_argument("--update", action="store_true", help="全流程更新（默认）")
    args = ap.parse_args()

    ensure_dirs()
    if args.init_db:
        from .db import init_db
        init_db()
        print("数据库已初始化")
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


if __name__ == "__main__":
    main()
