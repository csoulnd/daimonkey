"""数据源注册表：新增源 = 写一个模块 + 在此注册 + registry.yaml 登记。"""
from .appstore_lookup import AppstoreLookupSource
from .appstore_rss import AppstoreRssSource
from .github import GithubSource
from .news_hn import NewsHnSource
from .news_rss import NewsRssSource
from .npm import NpmSource
from .pypi import PypiSource
from .vscode_marketplace import VscodeMarketplaceSource

SOURCE_REGISTRY = [
    GithubSource,
    NpmSource,
    PypiSource,
    AppstoreLookupSource,   # 先于 rss：产出 bundleId -> trackId 映射供其匹配
    AppstoreRssSource,
    VscodeMarketplaceSource,
    NewsRssSource,
    NewsHnSource,
]

__all__ = ["SOURCE_REGISTRY"]
