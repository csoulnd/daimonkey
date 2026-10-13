"""Render the original speaker article from a versioned snapshot.

Run in the report directory using the website's .venv/bin/python.
The presentation is an editorial condensation; source-snapshot.md is unchanged.
"""
from pathlib import Path
import hashlib
import html
import json
import re
import markdown

ROOT = Path(__file__).resolve().parent
COMMIT = 'c61e29bd7f79e2f6097687a3adcee65c1ba0789a'
REPO = 'https://github.com/csoulnd/InferTechNote'
SOURCE = 'agent/reports/ai-coding-workflow-practice.md'
raw = (ROOT / 'source-snapshot.md').read_text(encoding='utf-8')
body = re.sub(r'\A---\n.*?\n---\n', '', raw, count=1, flags=re.S)
body = body.split('\n## Knowledge Extraction')[0]
body = re.sub(r'\]\(../../(\.agents/[^)]+)\)', lambda m: f']({REPO}/blob/{COMMIT}/{m[1]})', body)
md = markdown.Markdown(extensions=['fenced_code', 'tables', 'toc', 'sane_lists'], extension_configs={'toc': {'permalink': False}})
content = md.convert(body)
source_url = f'{REPO}/blob/{COMMIT}/{SOURCE}'
page = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Coding 提效实践 · 原文讲稿</title><link rel="stylesheet" href="presentation.css"><style>
body{{line-height:1.85}}.script-header{{max-width:1200px;margin:0 auto;padding:25px 25px 0;display:flex;gap:20px;flex-wrap:wrap;color:var(--muted);font-size:14px}}.script-grid{{max-width:1200px;display:grid;grid-template-columns:230px 1fr;gap:42px;margin:30px auto;padding:0 25px 80px}}.script-toc{{position:sticky;top:20px;align-self:start;max-height:90vh;overflow-y:auto;font-size:13px;line-height:1.7}}.script-toc ul{{list-style:none;padding-left:12px}}.script-toc>div>ul{{padding:0}}.script-toc li{{margin:7px 0}}.script-toc a{{text-decoration:none;color:var(--muted)}}.script-body{{min-width:0;font-size:19px}}.script-body h1{{font-size:36px;line-height:1.4}}.script-body h2{{font-size:29px;margin:55px 0 25px;color:var(--teal)}}.script-body h3{{font-size:24px;margin:35px 0 20px}}.script-body h4{{font-size:21px}}.script-body p{{margin:18px 0}}.script-body img{{max-width:100%;height:auto;display:block;margin:25px auto}}.script-body blockquote{{margin:25px 0;padding:5px 22px;border-left:3px solid var(--teal);color:var(--muted)}}.script-body table{{width:100%;border-collapse:collapse;font-size:16px}}.script-body th,.script-body td{{border-bottom:1px solid var(--line);padding:12px;text-align:left}}.script-body hr{{border:0;border-top:1px solid var(--line);margin:35px 0}}.script-body code{{font-size:.9em;overflow-wrap:anywhere}}.script-body pre{{white-space:pre-wrap}}.script-body a{{overflow-wrap:anywhere}}@media(max-width:850px){{.script-grid{{grid-template-columns:1fr;padding:0 20px 60px}}.script-toc{{display:none}}.script-body{{font-size:17px}}.script-body h1{{font-size:29px}}.script-body h2{{font-size:25px}}}}@media print{{.script-header,.script-toc{{display:none}}.script-grid{{display:block;max-width:none}}.script-body{{font-size:12pt}}}}
</style></head><body><header class="script-header"><a href="index.html">← 返回演示</a><a href="{source_url}" target="_blank" rel="noopener">GitHub 原文 ↗</a><span>讲稿快照 · {COMMIT[:7]} · 正文保持原文，省略仓库维护检查项</span></header><div class="script-grid"><nav class="script-toc" aria-label="讲稿目录">{md.toc}</nav><article class="script-body">{content}</article></div></body></html>'''
(ROOT / 'script.html').write_text(page, encoding='utf-8')
manifest = {'title': 'AI Coding 提效实践：工作流与人的成长', 'source_repository': REPO, 'source_path': SOURCE, 'source_commit': COMMIT, 'source_sha256': hashlib.sha256((ROOT / 'source-snapshot.md').read_bytes()).hexdigest(), 'slides': 10, 'target_duration_minutes': '10–15', 'audience': '有 AI Coding 经验及初次接触的开发者', 'paper_table': 'arXiv:2603.08274v1, Table 9; all 11 models with all three lengths; best-case fabrication percentages'}
previous_manifest = ROOT / 'source-manifest.json'
if previous_manifest.exists():
    prior = json.loads(previous_manifest.read_text(encoding='utf-8'))
    if 'presentation_revision' in prior:
        manifest['presentation_revision'] = prior['presentation_revision']
(ROOT / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Generated script.html and source-manifest.json')
