# 循环时间规则展开 API（Recurring Time Rule Expansion API）

基于 **Python + FastAPI + Pydantic + python-dateutil** 的 REST API：将 RFC 5545 RRULE
循环规则展开为查询区间内的具体实例。实例按 **UTC 排序**，同时保留**本地时间**与
**UTC 偏移**；夏令时切换时不存在的本地时刻会被跳过，重复出现的本地时刻会分别返回
两个 fold；月末规则、EXDATE（排除日期）与 RDATE（补充日期）均可正确处理。

## 一键启动（Docker）

唯一需要的一条命令：

```bash
docker compose up --build
```

启动后访问：

| 地址 | 说明 |
| --- | --- |
| <http://localhost:8000> | 服务信息 |
| <http://localhost:8000/docs> | Swagger 交互式文档 |
| <http://localhost:8000/health> | 健康检查 |

> 旧版 Compose 请使用 `docker-compose up --build`。
> 停止并清理：`docker compose down`。

依赖（FastAPI、uvicorn、python-dateutil、Pydantic 及 IANA 时区数据库 tzdata）全部
在构建时纳入镜像，无需在宿主机安装任何东西。

## 配置（环境变量，均可选）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OCCURRENCES_DEFAULT_LIMIT` | `1000` | 单次请求默认返回的实例上限 |
| `OCCURRENCES_MAX_LIMIT` | `100000` | 请求字段 `limit` 允许的最大值 |
| `OCCURRENCES_SCAN_CAP` | `500000` | 展开器扫描候选时刻的硬上限（防止病态规则拖垮服务） |

在 `docker-compose.yml` 的 `environment` 段中调整即可。

## API

### `POST /occurrences`

展开循环规则，返回区间内的实例。

**请求字段**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `timezone` | string | 是 | IANA 时区名，如 `America/New_York`、`Asia/Shanghai` |
| `start` | datetime | 是 | 循环的起始**本地**时间（naive，不带偏移），按 `timezone` 解释 |
| `rrule` | string | 是 | RFC 5545 RRULE，如 `FREQ=DAILY;INTERVAL=1`；可带 `RRULE:` 前缀；`UNTIL=...Z`（UTC）会自动换算成本地时间 |
| `window_start` | datetime | 是 | 查询区间起点（**带时区偏移**，含边界），如 `2026-01-01T00:00:00Z` |
| `window_end` | datetime | 是 | 查询区间终点（**带时区偏移**，不含边界） |
| `exdates` | datetime[] | 否 | 要排除的本地时刻（naive） |
| `rdates` | datetime[] | 否 | 要补充的本地时刻（naive） |
| `limit` | int | 否 | 返回实例上限，默认 `OCCURRENCES_DEFAULT_LIMIT` |

> 注意：`rrule` 中不要内嵌 `DTSTART` / `EXDATE` / `RDATE` / `EXRULE` 行，
> 请改用 `start` / `exdates` / `rdates` 字段（内嵌会返回 `invalid_rrule` 错误）。

**示例：夏令时回拨（重复时刻返回两个 fold）**

```bash
curl -s -X POST http://localhost:8000/occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "America/New_York",
    "start": "2026-10-31T01:30:00",
    "rrule": "FREQ=DAILY;COUNT=3",
    "window_start": "2026-10-31T00:00:00Z",
    "window_end": "2026-11-03T12:00:00Z"
  }'
```

响应（`2026-11-01 01:30` 在纽约发生两次，两个 fold 分别返回，按 UTC 排序）：

```json
{
  "timezone": "America/New_York",
  "window": { "start": "2026-10-31T00:00:00Z", "end": "2026-11-03T12:00:00Z" },
  "count": 4,
  "skipped_nonexistent": 0,
  "occurrences": [
    { "local_time": "2026-10-31T01:30:00", "utc_time": "2026-10-31T05:30:00Z", "utc_offset": "-04:00", "fold": 0 },
    { "local_time": "2026-11-01T01:30:00", "utc_time": "2026-11-01T05:30:00Z", "utc_offset": "-04:00", "fold": 0 },
    { "local_time": "2026-11-01T01:30:00", "utc_time": "2026-11-01T06:30:00Z", "utc_offset": "-05:00", "fold": 1 },
    { "local_time": "2026-11-02T01:30:00", "utc_time": "2026-11-02T06:30:00Z", "utc_offset": "-05:00", "fold": 0 }
  ]
}
```

**响应字段**

| 字段 | 说明 |
| --- | --- |
| `occurrences[].local_time` | 本地 wall-clock 时间（naive ISO 8601） |
| `occurrences[].utc_time` | 同一时刻的 UTC（`Z` 结尾），列表按它升序 |
| `occurrences[].utc_offset` | 该本地时刻的 UTC 偏移，如 `-04:00`、`+05:45` |
| `occurrences[].fold` | `0` 或 `1`，区分夏令时回拨时的重复本地时刻 |
| `skipped_nonexistent` | 因不存在（夏令时拨快）而被跳过的本地时刻数量 |

### 语义说明

- **展开方式**：以 naive 本地时间做 wall-clock 展开（“每天 09:00”永远是本地 09:00），
  再逐个本地化到目标时区。
- **不存在的时刻**（春季拨快产生的空隙）：跳过，并计入 `skipped_nonexistent`。
- **重复的时刻**（秋季回拨产生的重叠）：两个 fold 各返回一次，`fold=0` 为偏移较大
  （夏令时）的一侧。
- **月末规则**：`BYMONTHDAY=31` 自动跳过没有 31 日的月份；`BYMONTHDAY=-1` 表示每月
  最后一天；`BYDAY=-1FR` 表示每月最后一个周五，等等（由 python-dateutil 完整支持）。
- **EXDATE / RDATE**：排除与补充的本地时刻精确匹配到秒；EXDATE 优先于 RDATE。
- **查询区间**：`[window_start, window_end)`，按实例的 UTC 时刻判定归属，起点含、终点不含。

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## 错误格式

所有客户端错误均返回 HTTP 422 与结构化错误体：

```json
{
  "error": {
    "code": "invalid_timezone",
    "message": "Unknown IANA time zone: 'Not/AZone'",
    "details": { "timezone": "Not/AZone" }
  }
}
```

| `error.code` | 触发条件 |
| --- | --- |
| `validation_error` | 请求体不符合 schema（缺字段、类型错误等） |
| `invalid_timezone` | 时区名非法或不是有效的 IANA 时区 |
| `invalid_start` | `start` 带了 UTC 偏移（必须为 naive 本地时间） |
| `invalid_window` | 区间边界未带偏移，或 `window_start >= window_end` |
| `invalid_rrule` | RRULE 无法解析，或内嵌了 `DTSTART`/`EXDATE`/`RDATE`/`EXRULE` |
| `invalid_exdates` / `invalid_rdates` | 排除/补充日期带了 UTC 偏移 |
| `invalid_limit` | `limit` 超过服务端上限 |
| `too_many_occurrences` | 区间内实例数超过 `limit`（请缩小区间或提高 `limit`） |
| `expansion_too_large` | 规则候选扫描量超过 `OCCURRENCES_SCAN_CAP`（请让 `start` 更靠近查询区间） |

## 本地开发（不使用 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                                   # 运行测试（34 个用例）
uvicorn app.main:app --reload --port 8000  # 启动开发服务
```

## 项目结构

```
├── app/
│   ├── main.py         # FastAPI 应用、路由、异常处理器
│   ├── models.py       # Pydantic 请求/响应模型
│   ├── recurrence.py   # RRULE 展开、DST gap/fold 处理、窗口过滤
│   ├── errors.py       # DomainError（结构化错误）
│   └── config.py       # 环境变量配置
├── tests/              # pytest 端到端测试
├── requirements.txt    # 运行时依赖（纳入镜像）
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```
