# Android APK 在线更新：后端接口说明

> 已实现。发版包格式见 [APK_PACKAGE_GUIDE.md](./APK_PACKAGE_GUIDE.md)。未发版 / `enabled=false` 时版本检查返回 `has_update: false`，**不影响**现有业务接口。

## 1. 版本检查接口（必须）

**接口**：`GET /api/v2/app/version`  
**认证**：`Authorization: Bearer <token>`（与现有业务接口一致）  
**调用时机**：
- 用户登录后进入任务看板首页时自动调用
- 用户点击左侧导航栏版本号时手动调用

### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `platform` | string | 否 | 建议 `android`；若传其它值返回 400 |
| `current_version` | string | 否 | 前端当前版本号，例如 `0.1.13`（仅展示/对账） |
| `current_build` | int / string | 否 | 前端当前构建号，例如 `13`（**比较用**） |

### 正常返回示例（200）

始终返回 **200**（不用 204），用 `has_update` 区分：

```json
{
  "has_update": true,
  "version": "0.1.16",
  "build_number": 16,
  "download_url": "http://<当前请求Host>:11223/api/v2/app/apk/放射安全检测报告-v0.1.16-build16.apk",
  "download_path": "/api/v2/app/apk/放射安全检测报告-v0.1.16-build16.apk",
  "file_size": 42835921,
  "sha256": "…",
  "release_notes": "修复检测报告提交问题",
  "force_update": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `has_update` | bool | `build_number` 大于客户端 `current_build` 时为 true；未配置发版时为 false |
| `version` | string | 新版本号，展示用 |
| `build_number` | int | **核心判断字段**（整数比较） |
| `download_url` | string | 按**当前请求 Host** 拼出的绝对地址（忽略 zip JSON 里写死的内网 IP） |
| `download_path` | string | 相对路径；也可 `kBaseUrl + download_path` |
| `file_size` | int | 字节数 |
| `sha256` | string | 包校验（建议下载后校验） |
| `release_notes` | string | 更新说明 |
| `force_update` | bool | 预留；无更新时恒为 false |

### 无更新时

返回 200，`"has_update": false`（可能仍带上当前仓库最新包元数据便于对账）。

---

## 2. APK 下载（必须）

```
GET /api/v2/app/apk/<filename>
```

- 返回 APK 二进制流，`Content-Type: application/vnd.android.package-archive`
- 带 **`Content-Length`**（进度条）
- **允许匿名**直链（便于系统下载器）；亦可用业务 Token（忽略无效 Token 以免 401）
- 文件名支持中文（与编译产物一致）
- 生产环境也可由 Nginx 反代同一路径或 `media/apk/`

---

## 3. 后端发版流程（对齐 APK_PACKAGE_GUIDE）

1. 超管打开侧栏 **「App 更新」** → `/settings/app-ota/`
2. 上传一键编译产物 zip（内含 `.apk` + `.json`）
3. 后端解压：APK 落到 `media/apk/`（保留原文件名）；JSON 写入 `app_ota_runtime.json`
4. `GET /api/v2/app/version` 返回该记录；`download_url` 按请求 Host 重算
5. 换包时校验 **`build_number` 必须大于** 当前已登记值

仍兼容单独上传 `.apk`（需手填 version / build_number）。

---

## 4. 配置与目录

| 项 | 说明 |
|----|------|
| `app_ota_runtime.json` | 热更新配置，无需重启 |
| `media/apk/` | APK 落盘（可用 `APP_OTA_APK_DIR` 覆盖） |

---

## 5. 联调测试步骤

1. 超管上传 `…-v0.1.16-build16.zip`，勾选启用推送并保存。
2. App `pubspec.yaml` 中 `version: x.x.x+build` 的 build 小于 16。
3. 登录后进首页，应提示有新版本；点版本号可再检查。
4. 「立即更新」下载 APK 并调起安装；安装后不应再提示。
5. 关闭推送或未配置时，接口 `has_update=false`，首页静默。

---

## 6. 注意事项

- 仅 Android；iOS 不在范围。
- `build_number` 整数比较，不能回退或重复换包。
- `download_url` **不写死内网 IP**，随 App 访问的 Host 变化。
- 明文 HTTP 需 App `usesCleartextTraffic=true`（内网演示）。
- 接口异常时前端应静默跳过，不影响首页。
- `force_update` 以后端字段为准，前端未实现强制时仅作预留。
