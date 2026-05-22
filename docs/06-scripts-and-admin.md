# 06 运维、脚本与管理界面

## 1. Django Admin

- 入口：`/admin/`（`tablet_backend/urls.py`）。  
- 注册内容：`apps/core/admin.py`（及项目内其它 `admin.py` 若有）。  
- 用户「文件库容量配额（字节）」在用户编辑页的内联 **用户资料** 中维护（默认 5GiB）；超级用户及角色为超级管理员/普通管理员不校验配额。

## 2. 自定义管理命令

路径：`apps/core/management/commands/`

| 命令 | 文件 | 用途（以代码内文档为准） |
|------|------|---------------------------|
| `init_data` | `init_data.py` | 初始化基础数据 |
| `ensure_party_a_demo` | `ensure_party_a_demo.py` | 甲方演示/引导环境相关数据 |
| `ensure_template_tester` | `ensure_template_tester.py` | 模板测试账号相关 |
| `purge_library_trash` | `purge_library_trash.py` | 永久删除回收站中超时（默认 31 天）的文件库记录；建议由 cron 每日执行 |
| `check_library_media` | `check_library_media.py` | 校验 DB 文件库记录与 `media/file_library` 磁盘是否一致；缺失则移入回收站。启动时默认自动执行，可加 `--dry-run` |

使用方式：`python3 manage.py <command> [options]`

## 3. 日志与监控

- 项目未强制统一日志配置；生产建议配置 `LOGGING` 将错误落盘或接入采集。  
- 长时间任务（OCR、管线）注意进程超时与磁盘占用（`temp` 目录）。

## 4. 静态与媒体

- `collectstatic`：部署生产时收集到 `STATIC_ROOT`。  
- `MEDIA_ROOT`：用户上传与文件库内容，**需备份**；迁移服务器时与数据库一并规划。

## 5. 安全发布清单（摘要）

- [ ] `DEBUG=False`、`SECRET_KEY` 环境变量  
- [ ] `ALLOWED_HOSTS`、HTTPS、`SECURE_*` 头（按部署环境）  
- [ ] 数据库连接与迁移  
- [ ] `CORS_ALLOWED_ORIGINS` 仅允许可信前端源  
- [ ] JWT 过期策略与刷新接口保护  
- [ ] 文件上传大小限制、反病毒（若单位要求）
