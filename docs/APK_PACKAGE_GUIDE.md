# Android APK 更新包说明（给后端）

## 压缩包里有什么

每次一键编译 APK 成功，会在项目根目录生成一个 zip 包：

```
output/apk/放射安全检测报告-v{版本号}-build{构建号}.zip
```

例如：

```
output/apk/放射安全检测报告-v0.1.16-build16.zip
```

解压后包含两个文件：

```
放射安全检测报告-v0.1.16-build16.apk       ← 安装包本体
放射安全检测报告-v0.1.16-build16.json      ← 接口录入信息
```

## JSON 文件内容说明

`xxx.json` 里的字段直接对应更新接口需要返回的内容：

```json
{
  "version": "0.1.16",
  "build_number": 16,
  "file_size": 42835921,
  "download_url": "http://172.16.0.66:11223/api/v2/app/apk/放射安全检测报告-v0.1.16-build16.apk",
  "release_notes": "修复检测报告提交问题",
  "force_update": false
}
```

| 字段 | 含义 | 后端如何使用 |
|------|------|------------|
| `version` | 给人看的版本号 | 可原样返回给前端 |
| `build_number` | 构建号，整数值 | **必须大于旧版本** 才会触发更新提示 |
| `file_size` | APK 文件字节数 | 可原样返回给前端展示 |
| `download_url` | APK 下载地址 | 把 APK 放到这个 URL 对应的路径下 |
| `release_notes` | 更新说明 | 可原样返回给前端弹窗展示 |
| `force_update` | 是否强制更新 | 当前前端按可选更新处理，可固定返回 false |

## 后端需要做什么

1. **解压 zip 包**，拿到 APK 文件。
2. **把 APK 文件放到服务器上**，确保能按照 `download_url` 中的路径访问到。  
   例如 `download_url` 是：
   ```
   http://172.16.0.66:11223/api/v2/app/apk/放射安全检测报告-v0.1.16-build16.apk
   ```
   后端需要实现 `GET /api/v2/app/apk/xxx.apk`，返回 APK 二进制流，并带有 `Content-Length` 响应头。
3. **把 JSON 里的信息录入数据库或配置表**：
   - 更新 `GET /api/v2/app/version` 接口返回的内容。
   - 关键：`build_number` 一定要比上一个版本大。

## 完整流程示例

假设当前 App 版本是 `0.1.15+15`，用户收到 `build16` 的 zip 包。

后端操作：

1. 解压得到 `放射安全检测报告-v0.1.16-build16.apk` 和 `.json`。
2. 把 APK 上传到服务器，映射到 `/api/v2/app/apk/放射安全检测报告-v0.1.16-build16.apk`。
3. 在版本配置表里写入：
   - version: `0.1.16`
   - build_number: `16`
   - download_url: `http://.../api/v2/app/apk/放射安全检测报告-v0.1.16-build16.apk`
   - file_size: `42835921`
   - release_notes: `修复检测报告提交问题`
4. 前端调用 `GET /api/v2/app/version` 时，返回这条记录。

## 注意事项

- `build_number` 是整数比较，不是字符串，也不是 `version` 字段里的数字。
- 每次发版 `build_number` 只能递增，不能回退或重复。
- 如果 `download_url` 是公开访问的，前端下载时不需要鉴权；如果需要鉴权，前端会自动带上业务接口的 Bearer Token。
- APK 下载接口必须返回 `Content-Length`，否则前端下载进度条不会动。
