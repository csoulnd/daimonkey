# 域名申请与 HTTPS 上线操作手册（百度智能云 BCC）

> 适用站点：`/home/daimonkey`（InferTech 观察站 / daimonkey）
> 服务器：百度智能云 BCC，Ubuntu 24.04，公网地址示例 `106.12.30.116`
> 目标：给现有站点绑定正式域名，完成 ICP 备案、DNS 解析、HTTPS 反向代理和备案号展示
> 预计耗时：域名注册 0.5 天；实名认证 1–3 个工作日；ICP 备案 1–4 周；服务器改造约 1 小时

本文中的 `example.com`、`admin@example.com`、备案号都是占位符，执行时替换成真实值。

---

## 0. 先看总览

### 0.1 当前站点事实（操作前核对）

| 项目 | 当前值 | 说明 |
|---|---|---|
| 云服务器 | 百度智能云 BCC | 中国大陆线路 |
| 公网 IP | `106.12.30.116` | 以控制台 EIP 列表为准；该 IP 的 80/8080 实测可访问 |
| 应用服务 | `systemd` 单元 `daimonkey` | `scripts/serve.py --host 0.0.0.0 --port 8080` |
| HTTP 80 | `heat-index-port80.service` 用 iptables 转发到 8080 | 这是 systemd 服务，重启会恢复 |
| HTTPS 443 | 未配置 | 没有 Nginx/Caddy，没有证书 |
| 站点目录 | `/home/daimonkey`，静态产物在 `dist/` | `dist/` 是构建产物，不要手改 |
| 构建命令 | `.venv/bin/python -m collector --build` | 改模板后用它重建 |
| 备份脚本 | `deploy/backup.sh` | 改造前先跑一次 |

### 0.2 先做三个决定

1. **备案主体**：个人还是企业。
   - 个人：身份证备案，适合个人维护的非经营性站点。
   - 企业：营业执照备案，适合以公司/品牌名义运营、后续可能商业化或开票的场景。
   - 站点含资讯聚合、用户登录、AI 助手；部分省份对个人备案的网站名称和内容审核更严，企业主体通常更稳妥。以主体所在省管局规则为准。
2. **域名**：选一个长期使用的品牌域名，后缀优先 `.com` / `.cn` / `.net`。
3. **托管路线**：
   - **推荐**：保留当前大陆 BCC，注册国内域名，完成 ICP 备案。
   - 备选：迁到香港/海外服务器，通常不需要 ICP 备案，但大陆访问延迟和稳定性会变差；当前机器在大陆，迁站成本更高。

### 0.3 全流程

```text
准备主体/域名
  → 注册域名并实名认证
  → 确认 BCC 备案资格，生成备案操作码
  → 提交 ICP 备案（管局审核）
  → 网站页脚展示 ICP 备案号
  → 30 日内完成公安联网备案
  → 域名 A 记录解析到 BCC 公网 IP
  → Nginx 反向代理 + HTTPS 证书
  → 收紧 8080、验收、维护
```

**顺序很重要**：先备案，后解析上线。备案通过前不要把域名解析到 80/443 对外提供站点；可以继续用 IP 访问。

---

## 1. 注册域名（百度智能云域名服务）

### 1.1 命名与后缀

- 优先 `.com`，其次 `.cn`；两者都是工信部批复、管局普遍接受的常规后缀。
- 短、好记、和品牌相关；避免连字符、数字混排、容易拼错的词。
- 注册前查一下商标和现有产品，避免侵权。
- 不要选来源不明的小众后缀；部分后缀不能备案，或续费价格很高。
- 可备案后缀查询：<https://domain.miit.gov.cn/>

### 1.2 先创建信息模板（推荐）

百度智能云域名注册需要绑定“信息模板”，提前创建并实名，可以少走一步。

1. 登录百度智能云控制台：<https://console.bce.baidu.com/>
2. 进入 **域名服务**（域名注册 / BCD）控制台。
3. 左侧导航选择 **信息模板** → **添加信息模板**。
4. 所有者类型选 **个人** 或 **企业**，填写与证件完全一致的信息：
   - 个人：姓名、身份证号、地址、手机、邮箱。
   - 企业：企业名称、统一社会信用代码、地址、联系人。
5. 提交实名认证，等待 **1–3 个工作日**。`.cn` 由 CNNIC 审核，可能稍慢。
6. 状态变为 **已实名** 后再注册；用已实名模板注册，域名通常自动完成实名。

### 1.3 查询并购买

1. 域名服务控制台 → **域名管理** → **注册新域名**。
2. 输入想要的名称，选择后缀，点 **立即查询**。
3. 可注册的域名加入购物车，选择购买年限（建议至少 1 年；备案期间域名必须有效）。
4. 资料配置页：
   - **绑定模板**：选择刚实名通过的信息模板。
   - **DNS 设置**：选百度智能云云解析（后续解析最方便）；也可以先用注册商默认 DNS。
5. 去结算并支付。
6. 回到域名管理列表，确认：
   - 域名状态正常；
   - 实名认证状态为 **已实名**；
   - 到期时间足够覆盖备案周期。

### 1.4 保存域名证书

备案系统可能要求上传域名证书，或个人/企业实名认证截图。域名管理 → 目标域名 → **更多操作** → 下载域名证书 / 查看实名信息。截图要包含：

- 域名；
- 域名所有者；
- 证件类型和证件号码；
- 域名到期时间。

### 1.5 注意事项

- 域名所有者必须和备案主体一致。个人备案就登记在个人名下，企业备案就登记在公司名下；不一致要先过户。
- 未实名或实名失败的域名会被 **Serverhold**，无法解析。
- 海外注册商（Cloudflare、Namecheap 等）注册的域名通常无法通过国内 ICP 备案的域名核验。要在大陆 BCC 上合规使用，优先国内注册商。
- 域名注册后尽快实名；`.cn` 尤其严格。
- 开启自动续费或设置到期提醒。域名过期会连带影响备案和证书。

---

## 2. 确认 BCC 备案资格

### 2.1 硬性条件

百度智能云当前对 BCC 备案的要求（以控制台实际校验为准）：

| 条件 | 要求 |
|---|---|
| 付费方式 | **预付费 / 包年包月**；按量付费暂不支持备案 |
| 公网 IP | 已关联 **弹性公网 IP（EIP）** |
| 线路 | 中国大陆线路 |
| 购买时长 | 通常要求 **大于 89 天**；部分省份规则不同 |
| 配额 | 每个符合条件的 BCC 实例通常可提交 **5 个网站/APP 备案**，配额用完后不可复用 |
| 账号 | 备案账号需完成实名认证，且主办单位证件号与账号实名一致 |

### 2.2 在控制台核对

1. **BCC 控制台** → 实例列表 → 打开当前实例详情：
   - 确认付费方式是包年包月；
   - 确认剩余时长 > 89 天；
   - 确认已绑定 EIP。
2. **弹性公网 IP 控制台** → 确认 EIP 列表和公网 IP。本文示例 `106.12.30.116`，以控制台为准。
3. **备案系统** → 云资源检查页面，确认该实例有可用备案配额。

> 如果实例是按量付费或剩余时长不足，先续费/转包年包月。不要等到提交备案时才发现资源不合格。

### 2.3 生成备案操作码

1. 登录百度智能云备案系统：<https://beian.bce.baidu.com/user/dashboard> 或 <https://cloud.baidu.com/beian/index.html>
2. 顶部导航进入 **备案操作码**。
3. 点 **生成备案操作码**，选择产品（BCC）和实例，填写数量。
4. 生成后状态为 **未使用**。
5. 提交备案时：
   - 同一账号：通常可直接选择实例；
   - 跨账号：在网站信息页选择 **备案操作码**，填入后点 **验证**。

规则：

- 一个操作码对应一个网站/APP；
- 备案终审通过后变为 **已使用**，不能再用；
- 备案撤销且未终审时，操作码恢复可用；
- 实例过期/失效，操作码随之失效；
- 账号实名信息必须与备案主体证件号一致。

### 2.4 不满足条件怎么办

- 续费到 90 天以上，或转换成包年包月；
- 换一台符合条件的 BCC；
- 用其他百度云账号下符合条件实例生成的操作码（主体实名必须一致）；
- 或者改走香港/海外托管，不办 ICP。

---

## 3. 准备备案材料

### 3.1 主体材料

| 材料 | 个人备案 | 企业备案 |
|---|---|---|
| 主体证件 | 身份证正反面 | 营业执照 |
| 负责人证件 | 本人身份证 | 法人身份证；网站负责人身份证 |
| 授权书 | 不需要 | 网站负责人不是法人时，部分省份要求授权书 |
| 联系方式 | 手机、邮箱、应急联系电话 | 同左，另加企业联系电话 |
| 域名材料 | 域名证书 / 实名截图 | 同左 |
| 其他 | 部分省份要求居住证/暂住证 | 涉及新闻、出版、教育、医疗等需前置审批文件 |

所有证件用彩色扫描件或清晰照片，边角完整，不要遮挡、反光、模糊。

### 3.2 网站信息

- **网站名称**：建议用品牌名，例如 `InferTech 观察站` 或 `大马猴`。
  - 不要使用“中国”“国家”“中华”“银行”“保险”等需要专项审批的词；
  - 个人备案不要使用公司名称或暗示企业经营的名称；
  - 名称要和实际内容一致。
- **服务内容**：如实填写技术信息展示、技术社区/个人站点等。
  - 站点含 AI 资讯聚合。部分省份对“新闻/资讯”类内容审核严格，可能要求相关资质；提交前先看主体所在省管局规则。
  - 不要把内容写成与实际不符的类别。
- **域名**：填写主域名（如 `example.com`），`www` 一般随主域名一起覆盖，不需要单独再报一个网站；以备案页面提示为准。
- **网站负责人**：可以就是主体负责人；必须手机可接通、能配合人脸核验和短信核验。
- **备案操作码**：来自当前 BCC 实例。

### 3.3 提交前检查表

- [ ] 域名已实名，所有者与备案主体一致
- [ ] 域名后缀在工信部批复范围内
- [ ] 域名有效期足够，注册商是国内持牌注册商
- [ ] BCC 是包年包月、绑定 EIP、剩余时长 > 89 天
- [ ] 备案账号已完成实名，证件号与主体一致
- [ ] 营业执照/身份证在有效期内
- [ ] 网站名称、服务内容没有敏感或需前置审批的表述
- [ ] 负责人手机能接短信，能完成人脸核验
- [ ] 已保存域名证书/实名截图
- [ ] 网站当前能正常打开（可用 IP 访问），内容完整

---

## 4. 提交 ICP 备案

### 4.1 入口与账号

- 入口：<https://cloud.baidu.com/beian/index.html>
- 备案管理：<https://beian.bce.baidu.com/user/dashboard>
- 使用购买 BCC/EIP 的百度智能云账号登录；跨账号备案时用操作码，并确保主体实名一致。

### 4.2 七步流程

1. **验证备案类型**
   - 第一次备案选首次备案/新增备案；
   - 域名已在其他接入商备案过，选新增接入；
   - 已有备案主体、加新域名，选新增网站。
2. **填写主办者信息**
   - 个人/企业主体信息、证件、联系方式、应急联系人。
3. **填写网站信息**
   - 网站名称、服务内容、域名、网站负责人；
   - 选择 BCC 实例或填入备案操作码。
4. **资料上传及人脸核验**
   - 上传身份证/营业执照/授权书/域名材料；
   - 用微信、百度智能云 APP 或百度 APP 完成人脸识别。
5. **信息确认**
   - 逐项核对主体、网站、域名、证件号；提交后修改很麻烦。
6. **短信核验**
   - 按短信提示在 **24 小时内**完成核验；主体负责人和网站负责人都可能收到。
7. **备案进度及结果查询**
   - 在备案系统查看状态；审核期间保持手机畅通。

### 4.3 审核时间

| 阶段 | 时间 |
|---|---|
| 百度智能云初审 | 约 1–2 个工作日 |
| 管局审核 | 约 3–20 个工作日，各省不同 |
| 整体 | 通常 1–4 周 |

审核期间：

- 不要更换域名所有者、BCC 实例、EIP；
- 不要注销/迁移服务器；
- 保持网站可访问，内容与备案信息一致；
- 接到审核电话时如实回答网站用途、内容、负责人信息。

### 4.4 通过后必须做的两件事

1. **展示 ICP 备案号**
   - 在网站页脚展示备案号，并链接到 <https://beian.miit.gov.cn/>；
   - 格式示例：`京ICP备2026XXXXXX号-1`；
   - 具体实现见 6.10。

2. **公安联网备案**
   - 网站上线后 **30 日内**，到 <https://beian.mps.gov.cn/> 办理；
   - 用主体信息注册，提交网站信息、ICP 备案号、域名、服务器 IP、负责人信息；
   - 审核通过后获得公安备案号，同样在页脚展示并链接到公安备案查询页；
   - 各地公安要求可能不同，按页面提示操作。

### 4.5 常见驳回原因

- 域名所有者与备案主体不一致；
- 域名未实名，或实名信息还没同步；
- 网站名称含敏感词、企业名、需前置审批的词；
- 网站服务内容与实际不符；
- 证件照片模糊、缺边角、反光；
- 负责人手机打不通，或短信核验超时；
- BCC 不满足包年包月/时长/EIP 要求；
- 同一主体在多个账号下重复备案，信息冲突。

被驳回后按意见修改，重新提交即可；不要重复提交相同材料。

---

## 5. 域名解析（备案通过后）

### 5.1 添加 A 记录

1. 百度智能云控制台 → **云解析 DNS** → **域名解析**。
2. 找到域名（在百度云注册的通常自动出现；外部域名需先添加并修改 NS）。
3. **解析设置** → **添加记录**：

| 主机记录 | 类型 | 记录值 | TTL |
|---|---|---|---|
| `@` | A | `106.12.30.116` | 600 |
| `www` | A | `106.12.30.116` | 600 |

4. 保存后等待生效。A 记录不要填内网 IP。

### 5.2 验证

在服务器或本地执行：

```bash
dig +short example.com
dig +short www.example.com
curl -I http://example.com
```

预期：

- `dig` 返回 `106.12.30.116`；
- `curl -I http://example.com` 返回 `200` 或 `301`（完成 HTTPS 后会 301 到 https）；
- 如果返回其他 IP，检查是否有 CDN、Cloudflare 代理或旧解析记录。

### 5.3 注意

- **不要用 Cloudflare 橙云代理**指向大陆源站：会增加链路复杂度，也可能影响备案接入和大陆访问。要用 Cloudflare 就选 DNS only。
- 备案通过前不要正式解析上线；可以先用 IP 访问。
- 改 DNS 后 TTL 生效需要时间，验证时以权威 DNS 为准。
- 如果以后加 CDN、WAF 或换 IP，备案接入信息可能需要变更。

---

## 6. 服务器改造：Nginx + HTTPS

> 以下命令默认在 `/home/daimonkey`、Ubuntu 24.04、root 或有 sudo 权限的用户下执行。
> 改造会短暂影响访问，建议在低峰期做，并先看 6.12 回滚方案。

### 6.1 当前状态

- `daimonkey.service` 监听 `0.0.0.0:8080`；
- `heat-index-port80.service` 已启用，负责把 80 转发到 8080；
- 没有 Nginx/Caddy，没有 443；
- 系统防火墙 `ufw` inactive，但百度云安全组仍需放行端口。

### 6.2 备份

```bash
cd /home/daimonkey
sudo bash deploy/backup.sh
sudo cp /etc/systemd/system/daimonkey.service /root/daimonkey.service.bak
sudo cp /etc/nginx/nginx.conf /root/nginx.conf.bak 2>/dev/null || true
```

记下备份包的输出路径。数据库和 `data/` 是关键内容，`dist/` 可以重建。

### 6.3 停掉 80→8080 转发

这一步不能只删 iptables 规则：`heat-index-port80.service` 已启用，重启会自动恢复，而且会让 Nginx 收不到 80 流量。

```bash
# 停用并取消开机自启
sudo systemctl disable --now heat-index-port80.service

# 如果确认 heat-index-web 是旧站、不再使用，一并停用，避免开机抢占 8080
sudo systemctl disable --now heat-index-web.service

# 清理当前残留规则（服务 ExecStop 通常会删，双保险）
sudo iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-ports 8080 2>/dev/null || true

# 确认
sudo iptables -t nat -S PREROUTING | grep 8080 || echo "port 80 redirect removed"
```

> 不要停 `daimonkey.service`。它才是当前站点。

### 6.4 安装 Nginx 与 certbot

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
sudo systemctl enable --now nginx
nginx -v
```

如果 80 端口被占用，先查：

```bash
sudo ss -ltnp | grep ':80'
```

### 6.5 配置反向代理（先 HTTP）

创建 `/etc/nginx/sites-available/daimonkey.conf`：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;

    # 剪贴板单文件上限 100MB，这里留出请求头余量
    client_max_body_size 128m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # AI 助手/长任务可能跑几分钟
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

启用：

```bash
sudo ln -sf /etc/nginx/sites-available/daimonkey.conf /etc/nginx/sites-enabled/daimonkey.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

验证（此时 DNS 应已解析）：

```bash
curl -I http://example.com
```

应返回 `200`。如果 `502`，看 6.6 后面的排查。

### 6.6 申请证书（推荐 Let's Encrypt）

```bash
sudo certbot --nginx \
  -d example.com -d www.example.com \
  --redirect --agree-tos \
  -m admin@example.com --no-eff-email
```

certbot 会自动：

- 申请证书（HTTP-01 验证）；
- 写入 443 配置；
- 添加 HTTP→HTTPS 跳转；
- 安装 systemd 定时续期任务。

验证：

```bash
sudo certbot renew --dry-run
systemctl list-timers | grep certbot
curl -I https://example.com
```

预期 `HTTP/1.1 200` 或 `HTTP/2 200`。证书自动续期前，确保 80 端口长期可达。

> 如果 certbot 失败，常见原因是 80 未放行、DNS 未生效、备案未通过、或者 ACME 请求被网络策略拦截。先排查，再考虑 6.7 的百度云证书。

### 6.7 备选：百度智能云免费证书

如果不想用 Let's Encrypt：

1. 百度智能云控制台 → **SSL 证书** / 证书管理服务。
2. **申请证书** → 选择免费型 DV → 绑定 `example.com`（需要覆盖 `www` 时选对应型号或分别申请）。
3. 按提示做 DNS 验证（CNAME）或文件验证。
4. 签发后下载 **Nginx 格式**，得到 `.pem` 证书和 `.key` 私钥。
5. 上传到服务器：

```bash
sudo mkdir -p /etc/nginx/ssl
sudo chmod 700 /etc/nginx/ssl
# 将证书和私钥上传/粘贴到：
# /etc/nginx/ssl/example.com.pem
# /etc/nginx/ssl/example.com.key
sudo chmod 600 /etc/nginx/ssl/example.com.key
```

6. 用完整配置替换 6.5 的 HTTP-only 配置：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name example.com www.example.com;

    ssl_certificate     /etc/nginx/ssl/example.com.pem;
    ssl_certificate_key /etc/nginx/ssl/example.com.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    client_max_body_size 128m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

```bash
sudo nginx -t
sudo systemctl reload nginx
```

> 免费证书有效期以控制台为准（可能只有 3 个月），到期要手动续期。长期维护更推荐 Let's Encrypt 自动续期。

### 6.8 应用适配：真实 IP 与 Secure Cookie

反向代理后，应用看到的客户端地址会变成 `127.0.0.1`。当前代码有两处需要同步改：

#### 6.8.1 限流按真实 IP

`scripts/serve.py` 当前在 `_admit()` 里用：

```python
key = f"{kind}:{self.client_address[0]}"
```

反代后所有请求都会共享 `127.0.0.1` 一个桶，等于全站共用一个限流额度。改成信任 Nginx 传入的 `X-Real-IP`：

在请求处理类中增加方法（放在 `_admit` 附近即可）：

```python
    def _client_ip(self) -> str:
        # 仅当请求来自本机反代时信任该头；服务会绑定 127.0.0.1。
        return self.headers.get("X-Real-IP") or self.client_address[0]
```

然后把上面那行改成：

```python
key = f"{kind}:{self._client_ip()}"
```

日志如果想显示真实 IP，可同时把 `log_message` 里的 `self.address_string()` 替换为 `self._client_ip()`。

> 安全前提：应用必须只监听 `127.0.0.1`（见 6.9），否则公网用户可以直接伪造 `X-Real-IP`。

#### 6.8.2 登录 Cookie 加 Secure

当前登录 Cookie 在 `scripts/serve.py` 中设置：

```python
cookie = (f"{ASSISTANT_COOKIE}={make_cookie_value(username)}; Path=/;"
          f" Max-Age={ASSISTANT_COOKIE_MAX_AGE}; HttpOnly; SameSite=Strict")
```

增加一个辅助方法：

```python
    def _cookie_secure(self) -> str:
        return "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
```

登录和退出两处的 `SameSite=Strict` 后面追加：

```python
f" Max-Age={ASSISTANT_COOKIE_MAX_AGE}; HttpOnly; SameSite=Strict{self._cookie_secure()}"
```

退出登录的 Cookie 目前是普通字符串，要改成 f-string 再追加，避免在 HTTPS 下写回一个不带 Secure 的同名 Cookie：

```python
cookie = (f"{ASSISTANT_COOKIE}=; Path=/; Max-Age=0;"
          f" HttpOnly; SameSite=Strict{self._cookie_secure()}")
```

本地用 `http://127.0.0.1:8080` 测试时没有 `X-Forwarded-Proto: https`，不会加 Secure，不影响开发；线上经 Nginx 访问会自动带上。

#### 6.8.3 重启应用

```bash
sudo systemctl restart daimonkey
curl -I http://127.0.0.1:8080/
```

### 6.9 收紧端口与监听

先确认 Nginx 已能通过域名正常访问，再收紧；否则会把自己锁在门外。

1. 让应用只监听本机：

```bash
sudo sed -i 's#--host 0.0.0.0 --port 8080#--host 127.0.0.1 --port 8080#' /etc/systemd/system/daimonkey.service
sudo systemctl daemon-reload
sudo systemctl restart daimonkey
sudo ss -ltnp | grep 8080
```

预期看到 `127.0.0.1:8080`，而不是 `0.0.0.0:8080`。

2. 百度云 **安全组**（BCC 控制台 → 实例 → 安全组 → 入站规则）：
   - 放行 TCP **80**、**443**，来源 `0.0.0.0/0`；
   - **删除/关闭 8080** 的公网入站（或至少限制来源）；
   - 保留 **22**，最好限制为固定办公 IP；
   - 不要关闭 22，除非确认有 VNC/远程控制台可用。

3. 验证：

```bash
# 本机应正常
curl -I http://127.0.0.1:8080/

# 从外部网络执行，应连接失败/超时
curl -I --max-time 3 http://106.12.30.116:8080/
```

> 如果有外部脚本/客户端直连 `http://106.12.30.116:8080/api/...`（例如剪贴板同步），先把地址改成 `https://example.com/api/...`，再关闭 8080；登录 Cookie 加上 Secure 后，这些客户端也必须走 HTTPS。站内页面使用相对路径，不需要改。

### 6.10 页脚展示备案号

修改 `templates/base.html` 的 `<footer>`，在现有 `{% if ... %}{% endif %}` 之后加一段公共内容，保证所有页面都显示：

```html
<p class="dim">
  <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener">京ICP备2026XXXXXX号-1</a>
  · <a href="https://beian.mps.gov.cn/" target="_blank" rel="noopener">京公网安备 110105020XXXXXX号</a>
</p>
```

把占位备案号换成真实号码；ICP 备案号必须链接到工信部备案系统，公安备案号按当地公安要求展示/链接。

重建站点：

```bash
cd /home/daimonkey
.venv/bin/python -m collector --build
```

`dist/` 是构建产物，不要直接改 `dist/` 里的 HTML。构建后刷新页面确认页脚。

### 6.11 可选：HSTS

确认 HTTPS 稳定、且所有子域都支持 HTTPS 后，再在 443 的 `server` 块加：

```nginx
add_header Strict-Transport-Security "max-age=31536000" always;
```

不要一开始就加 `includeSubDomains`，避免其他 HTTP 子域被浏览器强制跳 HTTPS 后不可用。

### 6.12 回滚

如果 Nginx/HTTPS 改造后站点异常，按下面顺序回到改造前：

```bash
# 1. 停 Nginx，避免继续占用 80/443
sudo systemctl stop nginx
sudo systemctl disable nginx

# 2. 恢复应用监听和端口转发
sudo sed -i 's#--host 127.0.0.1 --port 8080#--host 0.0.0.0 --port 8080#' /etc/systemd/system/daimonkey.service
sudo systemctl daemon-reload
sudo systemctl restart daimonkey

# 3. 重新启用 80→8080 转发
sudo systemctl enable --now heat-index-port80.service

# 4. 安全组重新放行 8080
```

如果应用代码改动导致问题，用 `git diff` 查看并回退对应改动；服务文件可用 `/root/daimonkey.service.bak` 恢复。备案和 DNS 是外部状态，回滚服务器改造时不需要动。

---

## 7. 上线验收清单

### 7.1 命令行

```bash
# DNS
dig +short example.com
dig +short www.example.com

# HTTP → HTTPS
curl -I http://example.com

# HTTPS 状态与证书
curl -I https://example.com
openssl s_client -connect example.com:443 -servername example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates

# 应用本机健康
curl -I http://127.0.0.1:8080/

# 8080 不应从公网访问（在外部网络执行）
curl -I --max-time 3 http://106.12.30.116:8080/
```

### 7.2 浏览器

- [ ] 访问 `http://example.com` 自动跳到 `https://example.com`
- [ ] 证书有效，无混合内容警告
- [ ] 首页、榜单、新闻、报告、知识库正常
- [ ] 登录、退出正常，刷新后登录态保持
- [ ] 剪贴板上传/下载正常（大文件测试 100MB 以内）
- [ ] AI 助手对话正常，长任务不被 504 中断
- [ ] 更新数据按钮正常
- [ ] 手机 4G/5G 网络访问正常
- [ ] 页脚能看到 ICP 备案号和公安备案号，链接正确

### 7.3 安全

- [ ] `127.0.0.1:8080` 正常，公网 `8080` 不可达
- [ ] 安全组只放行 22/80/443，22 尽量限制来源
- [ ] HTTPS 只启用 TLS 1.2/1.3
- [ ] 登录 Cookie 带 `Secure`、`HttpOnly`、`SameSite=Strict`
- [ ] Nginx 日志/应用日志能看到真实客户端 IP（配置 6.8 后）
- [ ] 证书自动续期任务存在
- [ ] 已做一次 `deploy/backup.sh`
- [ ] SSL Labs / MySSL 检测无高危问题

### 7.4 合规

- [ ] ICP 备案号已在页脚展示并链接工信部
- [ ] 公安联网备案已完成并展示
- [ ] 域名所有者 = 备案主体
- [ ] 网站名称/内容与备案信息一致
- [ ] BCC/EIP/域名都在有效期内

---

## 8. 上线后维护

### 8.1 证书

```bash
# Let's Encrypt
systemctl list-timers | grep certbot
sudo certbot renew --dry-run
```

- certbot 定时续期依赖 80 端口可达；安全组不要删除 80。
- 如果换 DNS/换服务器，续期可能失败，及时检查。
- 用百度云免费证书时，按控制台有效期提前续期，更新 `/etc/nginx/ssl/` 文件后 `nginx -s reload`。

### 8.2 备案变更

以下情况需要做 **变更备案** 或 **新增网站备案**：

- 新增域名；
- 更换服务器/EIP/接入商；
- 主体信息、网站名称、服务内容变化；
- 迁移到其他百度云账号或实例。

备案信息不实或未及时变更，可能被管局/接入商要求整改。公安备案信息变化也要同步更新。

### 8.3 域名与服务器到期

- 域名开启自动续费，至少提前 30 天确认；
- BCC/EIP 保持有效，实例过期会导致备案操作码失效，甚至影响备案；
- 变更前先备份：`deploy/backup.sh`；
- 换服务器后按新实例重新生成操作码并做新增接入/变更。

### 8.4 监控建议

- 用外部拨测监控 `https://example.com` 状态码和证书到期时间；
- 监控 `daimonkey.service` 和 Nginx；
- 定期看 `journalctl -u daimonkey` 与 Nginx error log；
- 保留至少一份异地备份。

---

## 9. FAQ

**Q1：备案要多久？**
百度云初审 1–2 个工作日，管局 3–20 个工作日，整体通常 1–4 周。材料一次通过会快很多。

**Q2：可以用海外注册的域名备案吗？**
通常不行。国内 ICP 备案要求域名在工信部批复的注册商/后缀体系内，海外注册商域名一般无法通过域名核验。要在大陆 BCC 上合规使用，优先国内注册商。

**Q3：备案通过前域名能解析吗？**
技术上可以解析，但不建议对外提供站点。先完成备案，再正式解析到 80/443；审核期间用 IP 访问即可。

**Q4：为什么域名注册后打不开？**
常见原因：未实名/Serverhold、DNS 未生效、安全组未放行、Nginx 未启动、备案未通过。按 7.1 的顺序排查。

**Q5：Nginx 报 502 Bad Gateway？**
应用没起来或端口不对：

```bash
systemctl status daimonkey
curl -I http://127.0.0.1:8080/
sudo ss -ltnp | grep 8080
journalctl -u daimonkey -n 50 --no-pager
```

**Q6：登录后马上掉线？**
检查是否 HTTPS 下 Cookie 没加 Secure、系统时间是否正确、是否混用 http/https 域名。按 6.8.2 修改后重启应用。

**Q7：大文件上传失败？**
Nginx 默认 1MB，需设置 `client_max_body_size 128m;`。当前剪贴板单文件上限 100MB。

**Q8：8080 还能从公网访问？**
检查三处：应用是否还监听 `0.0.0.0`、安全组是否还放行 8080、是否有其他防火墙规则。按 6.9 收紧。

**Q9：个人备案还是企业备案？**
看运营主体。个人适合非经营性个人站点；以公司/品牌运营、可能商业化或开票，用企业主体。资讯类内容在部分省份审核更严，先查当地管局规则。

**Q10：ICP 备案和 ICP 许可证是一回事吗？**
不是。ICP 备案是网站信息登记；如果通过网站从事经营性互联网信息服务并收费，可能还需要增值电信业务经营许可证（ICP 许可证）。按实际业务判断。

**Q11：备案号必须展示吗？**
必须。ICP 备案号要在网站页脚展示并链接工信部备案系统；公安备案号按公安要求展示。

**Q12：换服务器后备案还有效吗？**
备案与接入商/服务器相关。换 BCC/EIP 或迁移接入商，需要办理变更备案/新增接入，不能只改 DNS。

---

## 附录 A：命令清单（按顺序执行）

```bash
# ── 0. 备份 ─────────────────────────────────────────────
cd /home/daimonkey
sudo bash deploy/backup.sh

# ── 1. 停掉旧的 80→8080 转发 ───────────────────────────
sudo systemctl disable --now heat-index-port80.service
# 确认旧站不再使用后再执行：
sudo systemctl disable --now heat-index-web.service
sudo iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-ports 8080 2>/dev/null || true
sudo iptables -t nat -S PREROUTING | grep 8080 || echo "port 80 redirect removed"

# ── 2. 安装 Nginx / certbot ────────────────────────────
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
sudo systemctl enable --now nginx

# ── 3. 写入 Nginx 配置（先改 example.com） ─────────────
# 文件：/etc/nginx/sites-available/daimonkey.conf
# 内容见 6.5
sudo ln -sf /etc/nginx/sites-available/daimonkey.conf /etc/nginx/sites-enabled/daimonkey.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
curl -I http://example.com

# ── 4. 申请证书 ────────────────────────────────────────
sudo certbot --nginx -d example.com -d www.example.com \
  --redirect --agree-tos -m admin@example.com --no-eff-email
sudo certbot renew --dry-run
curl -I https://example.com

# ── 5. 应用适配（改 scripts/serve.py，见 6.8） ─────────
sudo systemctl restart daimonkey

# ── 6. 收紧监听与端口 ─────────────────────────────────
sudo sed -i 's#--host 0.0.0.0 --port 8080#--host 127.0.0.1 --port 8080#' /etc/systemd/system/daimonkey.service
sudo systemctl daemon-reload
sudo systemctl restart daimonkey
sudo ss -ltnp | grep 8080
# 安全组：放行 80/443，关闭 8080，保留 22

# ── 7. 页脚备案号 + 重建 ───────────────────────────────
# 改 templates/base.html，见 6.10
.venv/bin/python -m collector --build

# ── 8. 验收 ───────────────────────────────────────────
dig +short example.com
dig +short www.example.com
curl -I http://example.com
curl -I https://example.com
openssl s_client -connect example.com:443 -servername example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
sudo systemctl status nginx daimonkey --no-pager
```

---

## 附录 B：Nginx 完整配置参考（certbot 执行后的形态）

certbot 会自动修改配置，下面是便于对照的完整形态；不要重复粘贴覆盖 certbot 生成的证书路径。

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;

    # certbot 会在这里加入 301 到 https；未签证书前保留 proxy 便于验证
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        client_max_body_size 128m;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name example.com www.example.com;

    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    client_max_body_size 128m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

---

## 附录 C：参考资料

- 百度智能云备案系统：<https://cloud.baidu.com/beian/index.html>
- 百度智能云备案管理：<https://beian.bce.baidu.com/user/dashboard>
- 备案前常见问题（BCC 条件、操作码）：<https://cloud.baidu.com/doc/BeiAn/s/vjx4rg2is>
- 云资源备案检查：<https://cloud.baidu.com/doc/BeiAn/s/Ykir3fkjg>
- 备案操作码流程：<https://cloud.baidu.com/doc/BeiAn/s/tkir3qfzh>
- 备案所需材料：<https://cloud.baidu.com/doc/BeiAn/s/ikir3xt00>
- 工信部域名后缀查询：<https://domain.miit.gov.cn/>
- 工信部 ICP 备案查询：<https://beian.miit.gov.cn/>
- 全国互联网安全管理服务平台（公安备案）：<https://beian.mps.gov.cn/>
