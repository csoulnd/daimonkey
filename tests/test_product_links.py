"""产品页榜单链接回归测试：只为「本次真的有榜单页」的 board 生成 /rankings/ 链接。

背景：office / research / data 三个 board 在库中没有任何 rankings 快照，sitegen
不会生成 /rankings/{board} 页；产品页若无条件链接过去就是 404 死链。hidden 源榜
（coding/general/claw）虽由 serve 301 到 /dir/harness，也不是 canonical 目标。

全部为纯 Jinja 渲染（FileSystemLoader），不写 dist/、data/，不 build、不联网。
"""
from __future__ import annotations

import re
import unittest

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from collector.config import TEMPLATES_DIR

# 生产 base.html 当前没有 {% block crumb %}（面包屑块未被渲染）；这里用最小 base
# 单独验证 product.html 的面包屑条件逻辑，避免模板接线回归后产出无效链接。
CRUMB_BASE = (
    "<!doctype html><html><body>"
    "<div class=\"crumb\">{% block crumb %}{% endblock %}</div>"
    "<main>{% block content %}{% endblock %}</main>"
    "</body></html>"
)

BOARD_NAMES = {
    "office": "办公智能体",
    "research": "研究智能体",
    "data": "数据智能体",
    "coding": "编码智能体（源榜）",
    "harness_core": "核心产品榜",
}


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters.update({
        "num": lambda v: "—" if v is None else f"{v:,}",
        "pct": lambda v: "—" if v is None else f"{v:+.1f}%",
        "dt": lambda s: (str(s).replace("T", " ")[:16] + " UTC") if s else "—",
        "d10": lambda s: (s or "")[:10],
        "hw_num": lambda v: "—" if v is None else str(v),
    })
    return env


def _product(boards: list[str], latest_ranks: dict | None = None) -> dict:
    return {
        "name": "测试产品",
        "vendor": "测试厂商",
        "status": "active",
        "category": "office",
        "boards": list(boards),
        "repo": None,
        "repo_aliases_list": [],
        "license": None,
        "npm_package": None,
        "pypi_package": None,
        "appstore_id": None,
        "vscode_ext_id": None,
        "notes": None,
        "latest_ranks": latest_ranks or {},
        "latest_ai": {},
        "news": [],
        "integration": None,
        "metrics_series": {},
    }


def _ctx(boards: list[str], board_pages: list[str],
         latest_ranks: dict | None = None) -> dict:
    return {
        "site": {"name": "InferTech 观察站", "lang": "zh-CN"},
        "meta": {"last_collected_at": "2026-10-21T00:00:00Z",
                 "methodology_version": "v1.5",
                 "retention": {"raw_body_days": 90}},
        "nav_directions": [],
        "assets_v": "test123",
        "stale": False,
        "board_names": BOARD_NAMES,
        "board_pages": list(board_pages),
        "metric_names": {},
        "news_categories": {},
        "p": _product(boards, latest_ranks),
        "charts": {},
        "chart_units": {},
        "chart_sources": {},
    }


class ProductBoardLinksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _env()
        self.tpl = self.env.get_template("product.html")

    def render(self, boards: list[str], board_pages: list[str],
               latest_ranks: dict | None = None) -> str:
        return self.tpl.render(**_ctx(boards, board_pages, latest_ranks))

    def test_office_without_ranking_page_is_not_linked(self) -> None:
        """无快照 board：面包屑/徽章都退化为纯文本，且保留 badge 样式类。"""
        html = self.render(["office"], ["harness_core"])
        self.assertNotIn('href="/rankings/office"', html)
        self.assertNotRegex(html, r'href="/rankings/(?:office|research|data)"')
        # 徽章不可点，但名称与样式类保留
        self.assertIn('<span class="badge b-office">办公智能体</span>', html)
        self.assertIn("办公智能体", html)

    def test_hidden_source_board_is_not_linked(self) -> None:
        """hidden 源榜 coding 有快照但不渲染独立榜单页，不能生成 /rankings/coding。"""
        html = self.render(["coding"], ["harness_core"])
        self.assertNotIn('/rankings/coding', html)
        self.assertIn('<span class="badge b-coding">编码智能体（源榜）</span>', html)

    def test_rendered_board_keeps_link(self) -> None:
        """防回归：board_pages 含该 board 时仍然输出榜单链接。"""
        html = self.render(["harness_core"], ["harness_core"])
        self.assertIn(
            '<a class="badge b-harness_core" href="/rankings/harness_core">'
            "核心产品榜</a>", html)

    def test_no_dead_ranking_links_for_disabled_boards(self) -> None:
        """office/research/data 三者均不应产生 /rankings/ 死链。"""
        for board in ("office", "research", "data"):
            with self.subTest(board=board):
                html = self.render([board], ["harness_core"])
                self.assertIsNone(
                    re.search(r'href="/rankings/(?:office|research|data)"', html))
                self.assertIn(f'<span class="badge b-{board}">', html)

    def test_latest_rank_cards_skip_unrendered_boards(self) -> None:
        """最新排名卡片：hidden 源榜/历史 board_key 只展示名次，不做 /rankings/ 链接。"""
        ranks = {
            "coding": {"rank": 3, "heat_score": 61.2,
                       "snapshot_date": "2026-10-21"},
            "unified": {"rank": 9, "heat_score": 55.0,
                        "snapshot_date": "2026-10-20"},
            "harness_core": {"rank": 2, "heat_score": 80.5,
                             "snapshot_date": "2026-10-21"},
        }
        html = self.render(["harness_core"], ["harness_core"], ranks)
        self.assertIsNone(re.search(r'href="/rankings/(?:coding|unified)"', html))
        self.assertIn('href="/rankings/harness_core"', html)
        self.assertIn('<div class="rcard">', html)

    def test_breadcrumb_is_conditional_when_base_renders_crumb(self) -> None:
        """面包屑：无榜单页时纯文本，有榜单页时仍是 /rankings/ 链接。"""
        env = _env()
        env.loader = ChoiceLoader([DictLoader({"base.html": CRUMB_BASE}),
                                   env.loader])
        tpl = env.get_template("product.html")

        no_page = tpl.render(**_ctx(["office"], ["harness_core"]))
        self.assertIn("<span>办公智能体</span> / 测试产品", no_page)
        self.assertNotIn('href="/rankings/office"', no_page)

        has_page = tpl.render(**_ctx(["harness_core"], ["harness_core"]))
        self.assertIn('<a href="/rankings/harness_core">核心产品榜</a> / 测试产品',
                      has_page)


if __name__ == "__main__":
    unittest.main()
