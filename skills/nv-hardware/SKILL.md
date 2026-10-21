---
name: nv-hardware
description: NV 硬件方向专家。适用场景:MLPerf Inference 结果接入、加速卡型号归一、单卡吞吐口径、NV 推理榜成员与排序、硬件产品页链接、换季升级。
---

# NV 硬件方向专家(nv-hardware)

## 职责边界

- 主责：`collector/sources/mlperf.py`、`registry.yaml → sources.mlperf / boards.nv_infer`、加速卡产品自动入池与归档。
- 协作：频道页与方向配置归 `directions`；benchmark 方法与其他评测源归 `model-eval`；采集事务/缓存/排障归 `data-update`。

## 数据源与口径(2026-10-21 定案)

| 项 | 内容 |
|---|---|
| 源 | MLPerf Inference 官方汇总 `summary.json`（当前 v6.1，2026-09-29） |
| 地址 | `https://raw.githubusercontent.com/mlcommons/inference_results_v6.1/main/summary.json`（约 650KB，492 条结果） |
| 过滤 | `Division=closed` + `SystemType=datacenter` + `Scenario=Offline` + LLM 模型 |
| 模型 | `gpt-oss-120b`（主指标）、`deepseek-r1`、`llama3.1-8b`（展示列） |
| 折算 | 单卡吞吐 = `Result / total_accelerators`，同一张卡多次提交取最大值 |
| 跳过 | 组合卡（名称含 `+` / ` and `）、缺失 `accelerator_model_name`、非 LLM 基准 |
| 能效 | v6.1 的 `has_power` 全为 false，**首版不出能效指标**；待 MLPerf Power 可用再扩 `mlperf_energy` |

- 榜单 `nv_infer`：`select={origin: mlperf, status: active}`，heat 仅 `mlperf_gptoss_120b: 1.0`；
  无该指标的卡 coverage=0 自动进观察区（页面“数据不足”提示）。
- 产品同步：`origin='mlperf'`、`category='nv'`、`board_tags=["nv_infer"]`、slug `mlperf-<型号>`；
  掉出本次结果的旧卡置 `archived`（页面保留，不删数据）。

## 缓存与更新

- `data/cache/mlperf_summary.json` 七天有效，有效期内零请求；原始响应逐次 `save_raw` 归档（gzip）。
- MLPerf 约季度发布一版：**换季只改 `registry.yaml → sources.mlperf.base_url` 的版本号**，删缓存后跑一次全量更新即可。
- 单卡数值变化不会自动触发指标重算以外的东西；榜单/页面靠 `--build` 重新生成。

## runbook

```bash
cd /home/daimonkey
.venv/bin/python -m unittest tests.test_mlperf -v      # 源单测（解析/归一/同步/缓存）
.venv/bin/python -m collector --update                 # 全量：采集 → 评分 → 建站
.venv/bin/python scripts/verify.py                     # 2e2 节校验 NV 榜
curl -s http://127.0.0.1:8080/api/v1/rankings/nv_infer.json | head
```

换季检查清单：新版本 `summary.json` 存在 → 模型名仍在 `MODEL_METRICS` → 主指标仍有 ≥8 张卡 →
`verify` 的 2e2 全绿。

## 常见调整

| 需求 | 改动点 |
|---|---|
| 换 MLPerf 版本 | `registry.yaml → sources.mlperf.base_url` + 删 `data/cache/mlperf_summary.json` |
| 换/加排名模型 | `collector/sources/mlperf.py → MODEL_METRICS` 与 `registry.metric_definitions`、`sitegen.METRIC_NAMES`、`boards.nv_infer.display_metrics` 同步 |
| 加能效指标 | 等 MLPerf Power 数据；新增 `mlperf_energy` 指标并在 heat 里配权重（总权重保持 1.0） |
| 国产卡入榜 | 名称归一表 `_VENDOR_PREFIX` 已含寒武纪/壁仞/沐曦，确认 MLPerf 结果里出现对应 `accelerator_model_name` |

## 排障

| 症状 | 处置 |
|---|---|
| NV 榜为空 | 看更新 notes：`MLPerf summary HTTP xxx` / `没有可用的 ... 结果`；确认 GitHub raw 可达（本机实测通） |
| 卡的名称重复/拆成两条 | 大小写差异会自动合并；同芯片不同提交名（如 VR200 / Vera Rubin）需在 `sources.mlperf.aliases` 显式映射 |
| 观察区突然变多 | 新版本里主指标模型改名或结果缺失；按“换季检查清单”核对 `MODEL_METRICS` |
| 数字明显偏低 | 检查是否混入多节点提交（`number_of_nodes`）或 `total_accelerators` 记录异常；必要时在解析层跳过 `number_of_nodes>1` |
| 页面还是旧版 | 采集后需 `--build` 重建 `dist/`；服务不需要重启（serve.py 未改） |

## 决策记录

- 2026-10-21(M6)：NV 首版以 **gpt-oss-120b Offline 单卡 Tokens/s** 为唯一排名口径，原因是它同时具备
  “最多提交（12 张卡）/单位统一（Tokens/s）/最贴近推理吞吐”三点；DeepSeek-R1 与 Llama3.1-8B 只作展示列，
  避免不同模型的数值混进同一排名。官方 summary 无功耗字段，能效延后。加速卡自动入池 + 掉榜归档沿用 AA 的
  产品同步范式；组合卡折算不可比，直接跳过。
