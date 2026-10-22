"""Icon-system regression tests (daimonkey v2, 2026-10-22).

Guards the single inline sprite in ``templates/base.html``:

* every literal ``#i-*`` reference in templates / JS resolves in the sprite;
* the documented dynamic references (avatar / pet-face concatenation and the
  navigation registry) stay whitelisted;
* no orphan symbol — 33 symbols only;
* 24x24 / 1.6 stroke / round caps / currentColor conformance for functional
  icons, with the four brand monkey faces and three avatars exempted;
* retired symbols and the old text glyphs never come back.

The standalone pages (``login.html`` / ``404.html``) do not extend base.html,
so they embed a *local* mini-sprite; this file also checks that every ``use``
inside them resolves inside the same document.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
ASSETS = ROOT / "static" / "assets"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


BASE = read(TEMPLATES / "base.html")
SPRITE_M = re.search(
    r'<svg xmlns="http://www\.w3\.org/2000/svg" class="sprite-defs"[^>]*>(.*?)</svg>',
    BASE,
    re.S,
)
assert SPRITE_M, "base.html: sprite-defs block not found"
SPRITE_BODY = SPRITE_M.group(1)
STYLE = read(ASSETS / "style.css")

SYMBOL_RE = re.compile(r"<symbol\b([^>]*)>(.*?)</symbol>", re.S)
ID_ATTR_RE = re.compile(r'id="(i-[a-z0-9-]+)"')
VIEWBOX_RE = re.compile(r'viewBox="([^"]+)"')

SYMBOLS: dict[str, tuple[str, str]] = {}
for _m in SYMBOL_RE.finditer(SPRITE_BODY):
    _attrs, _body = _m.group(1), _m.group(2)
    _id = ID_ATTR_RE.search(_attrs)
    assert _id, "symbol without id in base sprite"
    SYMBOLS[_id.group(1)] = (_attrs, _body)
SYMBOL_IDS = set(SYMBOLS)

NAV_ICONS = {
    "i-index", "i-news", "i-report", "i-book", "i-method", "i-chip", "i-clip",
}
ACTION_ICONS = {
    "i-search", "i-theme", "i-refresh", "i-minimize", "i-maximize",
    "i-restore", "i-close", "i-menu", "i-external", "i-download", "i-copy",
    "i-check", "i-warning", "i-info", "i-edit", "i-trash", "i-plus",
    "i-arrow-left", "i-arrow-right",
}
FACE_ICONS = {
    "i-monkey", "i-monkey-thinking", "i-monkey-happy", "i-monkey-angry",
}
AVATAR_ICONS = {"i-ava-monkey", "i-ava-happy", "i-ava-angry"}
# 26 icons on the 24x24 functional grid (7 nav + 19 action)
FUNCTIONAL_ICONS = NAV_ICONS | ACTION_ICONS

# Icons that ship for the documented next-use surfaces (clipboard download /
# copy, success toast, empty-state help) but have no call site in this build.
RESERVED_ICONS = {"i-download", "i-copy", "i-check", "i-info"}

# ids the JS builds by concatenation (cannot be found by a literal scan).
DYNAMIC_FACE_ICONS = FACE_ICONS
DYNAMIC_AVATAR_ICONS = AVATAR_ICONS
DYNAMIC_REGISTRY_ICONS = NAV_ICONS

RETIRED_ICONS = {
    "i-monkey-calm", "i-monkey-wow", "i-monkey-error", "i-monkey-loading",
    "i-monkey-sleepy", "i-monkey-laugh", "i-monkey-sad", "i-monkey-cry",
    "i-monkey-shy", "i-monkey-cool", "i-monkey-wink", "i-monkey-love",
    "i-monkey-dizzy", "i-monkey-party", "i-monkey-hat", "i-ava-hat",
}

# Interactive glyphs that must no longer serve as icons anywhere we touched.
FORBIDDEN_GLYPHS = "⟳◐⌕⛶─⟲☰✎🗑⚠«»↗←⚙"
GLYPH_FILES = [
    TEMPLATES / "base.html",
    TEMPLATES / "_assistant.html",
    TEMPLATES / "login.html",
    TEMPLATES / "knowledge_index.html",
    TEMPLATES / "404.html",
    # v2 close-out: the private report / knowledge pages lost their last
    # text glyphs (⛶ / ＋ / ↗ / ‹ ›) in favour of sprite references.
    TEMPLATES / "report_showcase.html",
    TEMPLATES / "report_detail.html",
    TEMPLATES / "knowledge_note.html",
    ASSETS / "assistant.js",
    ASSETS / "search.js",
    ASSETS / "app.js",
]

REF_RE = re.compile(r"#(i-[a-z0-9-]+)")


def literal_refs() -> dict[str, set[str]]:
    """file -> literal #i-* ids (dynamic concatenation prefixes are skipped)."""
    out: dict[str, set[str]] = {}
    files = sorted(TEMPLATES.rglob("*.html")) + sorted(ASSETS.glob("*.js"))
    for path in files:
        refs = set()
        for rid in REF_RE.findall(read(path)):
            if rid.endswith("-"):
                continue  # '#i-ava-' + face / '#i-monkey-' + face
            refs.add(rid)
        if refs:
            out[str(path.relative_to(ROOT))] = refs
    return out


class IconSpriteTest(unittest.TestCase):
    def test_expected_symbol_set(self) -> None:
        self.assertEqual(len(SYMBOL_IDS), 33, sorted(SYMBOL_IDS))
        self.assertEqual(len(NAV_ICONS), 7)
        self.assertEqual(len(ACTION_ICONS), 19, sorted(ACTION_ICONS))
        self.assertEqual(len(NAV_ICONS - SYMBOL_IDS), 0, NAV_ICONS - SYMBOL_IDS)
        self.assertEqual(len(ACTION_ICONS - SYMBOL_IDS), 0, ACTION_ICONS - SYMBOL_IDS)
        self.assertEqual(len(FACE_ICONS), 4)
        self.assertEqual(len(AVATAR_ICONS), 3)
        self.assertEqual(len(FUNCTIONAL_ICONS), 26)
        self.assertEqual(
            NAV_ICONS | ACTION_ICONS | FACE_ICONS | AVATAR_ICONS, SYMBOL_IDS
        )

    def test_literal_references_resolve(self) -> None:
        missing: list[str] = []
        for path, refs in literal_refs().items():
            for rid in sorted(refs - SYMBOL_IDS):
                missing.append(f"{path}: #{rid}")
        self.assertEqual(missing, [], "literal icon refs missing from base sprite")

    def test_report_and_knowledge_pages_iconized(self) -> None:
        """v2 close-out: the three private pages take their controls from the
        base sprite, and every ``#i-*`` they add resolves in that sprite."""
        expected = {
            "report_showcase.html": {
                "i-maximize", "i-plus", "i-external",
                "i-arrow-left", "i-arrow-right",
            },
            "report_detail.html": {"i-external"},
            "knowledge_note.html": {"i-external"},
        }
        refs_by_file = literal_refs()
        problems: list[str] = []
        for name, ids in expected.items():
            key = f"templates/{name}"
            found = refs_by_file.get(key, set())
            for rid in sorted(ids - found):
                problems.append(f"{key}: missing #{rid}")
            for rid in sorted(found - SYMBOL_IDS):
                problems.append(f"{key}: unresolved #{rid}")
        self.assertEqual(problems, [], "report/knowledge icon refs")

    def test_dynamic_references_are_whitelisted(self) -> None:
        assistant_js = read(ASSETS / "assistant.js")
        # the concatenation rules still target live symbols
        self.assertIn("#i-ava-", assistant_js)
        self.assertIn("#i-monkey-", assistant_js)
        for group, ids in (
            ("pet face", DYNAMIC_FACE_ICONS),
            ("avatar", DYNAMIC_AVATAR_ICONS),
            ("nav registry", DYNAMIC_REGISTRY_ICONS),
        ):
            self.assertEqual(ids - SYMBOL_IDS, set(), f"{group} targets missing")
        self.assertIn('AVATARS = ["monkey", "happy", "angry"]', assistant_js)

    def test_no_orphan_symbols(self) -> None:
        referenced: set[str] = set()
        for refs in literal_refs().values():
            referenced |= refs
        referenced |= DYNAMIC_FACE_ICONS
        referenced |= DYNAMIC_AVATAR_ICONS
        referenced |= DYNAMIC_REGISTRY_ICONS
        referenced |= RESERVED_ICONS
        orphans = sorted(SYMBOL_IDS - referenced)
        self.assertEqual(orphans, [], "orphan symbols in base sprite")

    def test_viewbox_per_family(self) -> None:
        for rid in sorted(FUNCTIONAL_ICONS):
            attrs = SYMBOLS[rid][0]
            m = VIEWBOX_RE.search(attrs)
            self.assertIsNotNone(m, f"{rid}: missing viewBox")
            self.assertEqual(m.group(1), "0 0 24 24", f"{rid}: functional grid")
        for rid in FACE_ICONS:
            self.assertIn('viewBox="0 0 640 520"', SYMBOLS[rid][0])
        for rid in AVATAR_ICONS:
            self.assertIn('viewBox="0 0 100 100"', SYMBOLS[rid][0])

    def test_functional_icon_attributes(self) -> None:
        for rid in sorted(FUNCTIONAL_ICONS):
            attrs = SYMBOLS[rid][0]
            self.assertIn('fill="none"', attrs, rid)
            self.assertIn('stroke="currentColor"', attrs, rid)
            self.assertIn('stroke-width="1.6"', attrs, rid)
            self.assertIn('stroke-linecap="round"', attrs, rid)
            self.assertIn('stroke-linejoin="round"', attrs, rid)
        # `.ic` base class is the container fallback (16px instances etc.)
        m = re.search(r"\.ic\s*\{([^}]*)\}", STYLE)
        self.assertIsNotNone(m, "style.css: .ic rule missing")
        rule = m.group(1)
        for prop in ("width: 1em", "height: 1em", "stroke: currentColor",
                     "fill: none", "stroke-width: 1.6"):
            self.assertIn(prop, rule, f".ic missing {prop}")

    def test_only_status_dots_are_filled(self) -> None:
        filled = {
            rid for rid, (_attrs, body) in SYMBOLS.items()
            if rid in FUNCTIONAL_ICONS and 'fill="currentColor"' in body
        }
        self.assertEqual(filled, {"i-warning", "i-info"},
                         "only warning/info status dots may be filled")

    def test_retired_symbols_are_gone(self) -> None:
        self.assertEqual(RETIRED_ICONS & SYMBOL_IDS, set())
        corpus = "\n".join(
            read(p) for p in sorted(TEMPLATES.rglob("*.html"))
        ) + "\n" + "\n".join(
            read(p) for p in sorted(ASSETS.glob("*.js"))
        )
        for rid in sorted(RETIRED_ICONS):
            self.assertNotIn(f"#{rid}", corpus, f"retired #{rid} still referenced")

    def test_no_text_glyph_fallbacks(self) -> None:
        problems: list[str] = []
        for path in GLYPH_FILES:
            for lineno, line in enumerate(read(path).splitlines(), 1):
                for glyph in FORBIDDEN_GLYPHS:
                    if glyph in line:
                        problems.append(
                            f"{path.relative_to(ROOT)}:{lineno} {glyph!r}"
                        )
        self.assertEqual(problems, [], "text glyph still used as icon")

    def test_avatar_brand_assets_preserved(self) -> None:
        """A20 change must survive the sprite replacement: light facial
        features, per-face gradients, three avatars only."""
        for rid in AVATAR_ICONS:
            body = SYMBOLS[rid][1]
            self.assertIn("#f6f2ff", body, f"{rid}: facial features must be light")
        self.assertIn('url(#dm-ava-happy)', SYMBOLS["i-ava-happy"][1])
        self.assertIn('url(#dm-ava-angry)', SYMBOLS["i-ava-angry"][1])
        self.assertIn('url(#dm-ava)', SYMBOLS["i-ava-monkey"][1])
        for gid in ("dm-ava", "dm-ava-happy", "dm-ava-angry", "dm-fire"):
            self.assertIn(f'id="{gid}"', SPRITE_BODY, f"gradient {gid} missing")
        self.assertNotIn("i-ava-hat", SYMBOL_IDS)

    def test_standalone_pages_embed_their_own_symbols(self) -> None:
        """login.html / 404.html do not extend base.html: each `use` must be
        defined in the same document or the icon silently renders nothing."""
        for name in ("login.html", "404.html"):
            text = read(TEMPLATES / name)
            local = set(ID_ATTR_RE.findall(text))
            refs = {r for r in REF_RE.findall(text) if not r.endswith("-")}
            self.assertEqual(refs - local, set(), f"{name}: unresolved local refs")
            self.assertNotIn("i-monkey-calm", refs, name)

    def test_no_duplicate_symbol_ids_per_document(self) -> None:
        for path in sorted(TEMPLATES.rglob("*.html")):
            ids = re.findall(r'<symbol\b[^>]*\bid="([^"]+)"', read(path))
            if not ids:
                continue
            self.assertEqual(
                len(ids), len(set(ids)),
                f"{path.relative_to(ROOT)}: duplicate <symbol> ids "
                f"(old + new sprite coexisting?)",
            )


if __name__ == "__main__":
    unittest.main()
