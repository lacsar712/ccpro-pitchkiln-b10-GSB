# PitchKiln-01 · 灶台值守看板

Django 5 + PostgreSQL：灶台瓦片看板 + 右侧抽屉探针时间线，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX：局部刷新灶台网格与抽屉
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4710** |
| Postgres | **6110**（容器内 5432） |

数据库账号：`pitchkiln` / `pitchkiln` / 库名 `pitchkiln`

## 快速启动

```bash
cd PitchKiln/PitchKiln-01
docker compose up --build -d
```

浏览器打开：http://localhost:4710

演示账号：

- `admin` / `123456`（超级用户）
- `worker` / `123456`（普通用户）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 6110，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6110
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4710
```

## 业务模型

1. **ResinLot（来脂批）**：`lotCode`、`originPlace`、`arrivalKg`、`receivedAt`
2. **FireHearth（灶台）**：`lane`、`tag`（唯一）、`resinGrade`、相位 `cold|charging|ramping|holding|drawing`
3. **CookRun（熬制值守）**：归属灶台与来脂批、`openedAt`、`closedAt`（可空）、`targetSoftPointC`
4. **SoftPointProbe（软化点探针）**：归属值守、`sampledAt`、`softPointC`、`samplerName`

**业务规则**：出胶**资格**与灶台**相位**是两回事，务必区分——

- **出胶资格（只读提示）**：进行中的 CookRun 至少有一条 `softPointC ≤ 95` 的 SoftPointProbe 即「资格·是」；读数缺失或全部高于 95℃ 为「资格·否」。判定逻辑只有一份：`apps/kiln/services/floor_rules.py` 的 `drawing_eligibility`，瓦片与抽屉的提示、以及「改相位」入口的校验都读它。探针写入成功后，瓦片与抽屉经 HTMX 局部刷新**立即**更新资格提示，但**绝不静默改动相位**。
- **相位（手动状态）**：只能经抽屉里「改相位」入口切换；切到 `drawing` 时由同一套判定（`assert_can_enter_drawing` → `drawing_eligibility`）把关，资格不足会被拒绝。写入合法探针后相位仍停在原位（如保温），须人工确认再改相位。
- **图例计数**：看板图例的「出胶」计数只统计相位已是 `drawing` 的灶；仅资格达标、未改相位的不计入。

种子数据中的 `坳火-甲`（保温，探针 102.4℃ / 96.2℃）用于演示这一区分：初始资格·否；写入 ≤95℃ 探针后资格翻为「是」，相位仍是保温，图例出胶计数不变。

## 界面

- 首页：**灶台值守看板** — 左侧班次条 + 按过道排布的灶台瓦片；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶）
- 次页：**来脂批** — 卡片时间线，非宽表 CRUD

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号存在。样例地名仅用「松脂坳 / 桐油坑」系。

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、种子
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  static/css/         # 值守台 ops-console 样式
```
