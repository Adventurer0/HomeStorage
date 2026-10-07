# HomeStorage · 家中有序

手机、电脑都能打开的家庭收纳网页。物品放在哪里、何时存入、还能放多久，以及每次取出和移动，都记录在自己的 NAS 上。

**飞牛 NAS 用户：** 下载完整项目 ZIP 后，使用 [飞牛部署说明](飞牛部署说明.md) 中的 `docker-compose.fnos.yml`，填写访问密码即可在飞牛 Compose 界面构建启动，无需命令行。

功能包含：

- **存储地图**：家 → 房间 → 货架 → 层板 / 抽屉 → 箱子，可自定义层级。
- **位置二维码**：打印贴在箱子上，用手机相机扫描后查看该位置与下级位置的物品，也可直接录入。
- **物品档案**：名称、分类、照片、数量、单位、品牌型号、标签、购买和存入日期、价格、备注。
- **搜索与筛选**：按名称、品牌、型号、标签、备注查找，按位置、分类和状态筛选。
- **存取和移库**：取出、归还、借出、移库、补充、消耗、丢弃、丢失；变动与日志一起保存。
- **保质期**：每件物品设置到期日和提前提醒天数，安全绿色、临期黄色、过期红色；首页和临期页自动汇总。
- **操作记录**：查看全屋操作、单件物品历史和搜索记录。
- **数据导出**：导出 JSON 档案；完整备份包含数据库与照片。
- **自定义分类**：新增、重命名、排序和停用分类，已有物品使用稳定分类 ID。
- **归档与恢复**：保留物品数量、照片、位置和日志，将归档物品从日常库存及提醒中隐藏。
- **AI 收纳助手**：在首页通过文字或录音查找物品、生成录入、补充、移库和归档草稿；用户明确确认后才执行数据写入。

数据库为空时只有一个「家」位置，不会把示例物品混进你的库存。先添加房间和箱子，再开始录入。

## AI 与语音设置

AI 功能可选。飞牛用户编辑 `docker-compose.fnos.yml` 的 environment，普通 Compose 用户编辑 `.env`：

| 配置 | 用途 |
| --- | --- |
| `AI_API_BASE_URL` | 供应商的 OpenAI 兼容 API 根地址，通常以 `/v1` 结尾 |
| `AI_API_KEY` | 供应商提供的 API 密钥，只在 NAS 后端使用 |
| `AI_MODEL` | 供应商支持工具调用的聊天模型名称 |
| `AI_TRANSCRIPTION_MODEL` | 可选语音识别模型；支持 OpenAI 语音接口时可使用 `whisper-1` |
| `AI_TRANSCRIPTION_BASE_URL` / `AI_TRANSCRIPTION_API_KEY` | 可选独立语音服务，留空则使用聊天服务配置 |

填好后重新构建、启动项目。没有填写 API 密钥时，分类、搜索、归档等普通功能照常工作。不同服务商不一定提供语音识别接口，聊天和语音服务可以分别配置。不要将已经填了密码或 API 密钥的配置文件上传回 GitHub。

首页可问“扭力扳手在哪里”，或说“新增两条 USB-C 线，放在书房配件箱”。AI 查询真实库存；位置不明确时需要补充信息。新增、补充、移动和归档都显示待确认卡片，用户点击确认后才能写入。草稿15分钟过期；库存或目标位置发生变化时需要重新生成。取消、重复确认不会产生重复库存。归档保留原始状态和数量，恢复后可继续使用。

AI 只获得列举分类、位置、查询物品与提出数据操作草稿的工具。没有 SQL、文件、终端、代码、Docker 或 GitHub 操作工具。用户确认由后端校验，模型不能自行确认。API 调用会将当前聊天和查询所需的少量物品信息发送给配置的服务商；不自动发送全库和照片。聊天模型返回的文字只作为建议展示，保存结果以待确认卡片和系统日志为准。

手机网页录音通常需要 **HTTPS**。通过飞牛 HTTPS 反向代理访问后允许麦克风，录音转成文字后可以先修改再发送，录音文件不持久保存。在普通 `http://NAS-IP:8080` 页面，可以用手机键盘自带的语音输入转成文字聊天。第一版未接入手机后台推送和离线语音识别。

## 在 NAS 上部署

需要 NAS 支持 Docker / Container Manager 和 Docker Compose v2。本项目使用单容器 FastAPI + SQLite，没有独立数据库服务和 Node.js 构建步骤。基础镜像支持常见的 x86_64 / ARM64 NAS。

将仓库放在 NAS 的本地磁盘，例如 `/volume1/docker/HomeStorage`。SQLite 数据目录应位于 NAS **本地文件系统**，不要通过 SMB/NFS 共享挂载另一个设备的目录。

### 一键部署

整个项目复制到 NAS 后，在项目目录运行：

```bash
sh deploy.sh
```

脚本使用 `docker-compose.yml`，首次自动创建 `.env` 并生成随机访问密码、创建 data 目录、构建镜像并等待服务健康。访问密码保存在 `.env` 的 `HOME_STORAGE_PASSWORD` 一行。重复运行会保留已有配置和数据。若 NAS 要求管理员权限访问 Docker，使用 `sudo sh deploy.sh`；应用仍以非 root 用户运行。需要 Docker Compose v2.18 或更新版本。

该 Compose 使用 `build: .` 从当前项目构建，因此需要整个项目，不能只上传一个 YAML 文件。`compose.yaml` 保留用于兼容先前的部署方式，两份 Compose 当前内容一致。

### 手动部署 / Container Manager

在该目录执行：

```bash
cp .env.example .env
```

编辑 `.env`：设置自己的 `HOME_STORAGE_PASSWORD`；通过 `id -u`、`id -g` 查询运行用户，把编号填入 `PUID`、`PGID`。端口冲突时修改 `HOME_STORAGE_PORT`。

```bash
mkdir -p data
# 用 .env 中填写的用户/组编号替换下面的 1000:1000。
sudo chown -R 1000:1000 data
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 homestorage
```

手机连到家庭网络后，浏览器打开 `http://NAS的局域网IP:8080`，输入配置的密码。在群晖 Container Manager 中也可「项目 → 新增」选择仓库目录和 `compose.yaml`，先准备好 `.env` 与 data 权限。

宿主机 `./data` 挂载到容器 `/data`，保存 SQLite 数据库、照片和会话密钥。容器更新、删除或重建不会删除该目录。保持单个容器、单个服务进程访问同一个 SQLite 文件。

### 日常使用

1. 在「存储空间」创建房间、货架和箱子，给箱子设置 `A3-02` 等编号。
2. 打开位置二维码并打印；生成二维码时使用手机能访问的 NAS 地址，不要使用 localhost。
3. 在「全部物品」添加物品，或扫描位置二维码后在该位置录入；食品和药品填写到期日及提醒天数。
4. 拿走可归还的物品用「取出」，用完归还；食品、耗材使用「消耗」扣减数量。
5. 临期页面查看自动计算的提醒；过期物品处理后使用「丢弃」，操作会留在记录里。

保质期按 `APP_TIMEZONE`（默认上海时间）日期计算：到期日前大于提醒天数为绿色，进入提醒天数及到期当天为黄色，到期日之后为红色。首页和到期页在可见且没有打开表单时每分钟更新，回到网页也会更新。这里的到期日是你录入的期限，系统不会判断食品实际是否安全。提醒显示在网页内；第一版没有后台推送、邮件或手机系统通知，也不支持断网编辑。

手机浏览器可把网页添加到主屏幕。HTTPS 下的安装体验取决于浏览器；局域网 HTTP 下可使用普通网页和手机自带相机扫码。二维码使用当前访问地址，换 NAS 地址后需要重新打印。网页上传照片支持文件选择，手机支持时可调用拍照。

### 更新

先备份数据，再在仓库目录执行：

```bash
git pull --ff-only
docker compose up -d --build
```

### 备份和恢复

JSON 导出包含位置、物品和日志，**不包含照片，也不是完整恢复文件**。第一版没有 JSON 导入。完整备份使用 NAS 快照，或停服务后备份整个 data 目录：

```bash
docker compose stop homestorage
tar -czf "homestorage-backup-$(date +%Y%m%d-%H%M%S).tar.gz" data
docker compose start homestorage
```

如果备份命令失败，也应重新启动服务。恢复时停止服务，保留当前 data 副本，再解压可信备份替换 data，确认属主与 `.env` 中 `PUID/PGID` 一致，然后启动。`.env` 单独安全保存。不要同时让两个容器对同一份数据库写入。

### 常见问题

- **无法写入数据**：检查 data 目录权限与 `PUID/PGID` 是否一致，不要把容器改成 root 来规避权限。
- **端口已占用**：修改 `.env` 的 `HOME_STORAGE_PORT`，重新执行 `docker compose up -d`。
- **手机打不开**：检查手机与 NAS 的网络连通性、NAS 防火墙和映射端口。
- **换密码**：修改 `.env` 后执行 `docker compose up -d`；不要将密码放到二维码中。
- **外网访问**：通过自己的 HTTPS 反向代理或私有网络访问。密码登录页在普通 HTTP 上不加密传输；外网访问需要 HTTPS。

HTTPS 反向代理需要保留浏览器请求的 `Host`（含非标准端口），否则同源校验会拒绝登录或保存。Nginx 配置使用 `proxy_set_header Host $http_host;`；群晖反向代理也应保留原始 Host。

如果构建网络使用自己的可信 HTTPS 代理，基础镜像可能需要该代理的 CA。可通过 BuildKit 临时注入完整的可信 CA bundle，保持 TLS 验证；普通 NAS 网络不需要此项：

```bash
docker build --secret id=pip_ca,src=/path/to/trusted-ca-bundle.pem -t homestorage:local .
docker compose up -d --no-build
```

## 本地开发和验证

Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-proxy-headers
```

默认数据库位于 `./data`；可通过 `DATA_DIR` 覆盖。未设置 `HOME_STORAGE_PASSWORD` 时只适用于本机开发；Docker Compose 强制要求配置密码。API 契约见 [API_CONTRACT.md](API_CONTRACT.md)。`GET /health` 检查数据库连通性。

开发检查包括物品完整存取流程、层级校验、保质期边界、上传校验、身份校验、日志和持久化。NAS 实机需要按上述步骤自行部署后确认网络与目录权限。
