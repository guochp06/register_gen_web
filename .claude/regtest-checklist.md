# Regtool Regression Test Checklist

> 每次代码改动后，Verifier Agent 必须逐项执行本清单，全部 PASS 才能交付。

## 0. 环境准备
- [ ] 停止旧后端进程（如有）
- [ ] 激活 venv：`source backend/venv/bin/activate`
- [ ] 启动后端：`cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 &`
- [ ] 验证启动：`curl http://localhost:8000/docs` 返回 Swagger UI HTML

## 1. 数据库与模型
- [ ] `regtool.db` 存在且能被正常读写
- [ ] `versions` 表包含字段：`id`, `name`, `description`, `html_path`, `warnings`, `top_addrmap_name`, `user_id`, `is_published`, `created_at`, `updated_at`
- [ ] 后端 import 测试：`python -c "from app.main import app; print('OK')"` 输出 OK

## 2. 匿名用户（未登录）
- [ ] `GET /api/v1/versions`（无 user 参数）→ 返回 `[]` 或仅已发布版本
- [ ] 看不到任何操作按钮（删除/发布/修改）

## 3. 普通用户（如 userA）
- [ ] `POST /api/v1/versions` 创建版本，body 含 `user_id: "userA"` → 成功，返回 version 对象
- [ ] `GET /api/v1/versions?user=userA` → 能看到自己创建的版本（未发布状态）
- [ ] 文件目录在首次上传后创建于 `backend/output/userA/{version_name}/`
- [ ] 上传 Excel/RALF 成功后，文件生成在用户隔离路径下
- [ ] `PUT /api/v1/versions/{id}` 修改版本元数据 → 成功（未发布状态）
- [ ] `POST /api/v1/versions/{id}/publish` 发布版本 → 成功，`is_published` 变为 true
- [ ] 发布后再次 `PUT` → 返回 403 "Cannot modify published version"
- [ ] `DELETE` 自己的版本，密码正确（等于用户名）→ 成功
- [ ] `DELETE` 自己的版本，密码错误 → 返回 403
- [ ] 发布后的版本可以 `DELETE`

## 4. 跨用户隔离
- [ ] 用 userB 创建版本
- [ ] `GET /api/v1/versions?user=userA` → **不应**看到 userB 的未发布版本
- [ ] `GET /api/v1/versions?user=userB` → 应看到 userB 的版本
- [ ] userA 尝试 `DELETE` userB 的版本 → 返回 403
- [ ] userA 尝试 `PUBLISH` userB 的版本 → 返回 403

## 5. Admin 权限
- [ ] `GET /api/v1/versions?user=admin` → 应看到所有用户的所有版本（已发布+未发布）
- [ ] Admin `DELETE` 任意版本，body 含 `user_id: "admin"` + `password: "askcp"` → 成功
- [ ] Admin `DELETE` 时密码错误或缺少 `user_id: "admin"` → 返回 403

## 6. 文件路径隔离
- [ ] 所有生成文件必须位于 `backend/output/{user_id}/{version_name}/` 下
- [ ] 不应存在旧路径 `backend/output/{version_name}/`（无 user_id 层级）
- [ ] **无重复路径段**：不应出现 `output/alice/alice/v1/` 这类嵌套
- [ ] HTML 目录存在：`output/{user_id}/{version_name}/html/` 必须包含完整 PeakRDL HTML
- [ ] HTML 静态文件 URL 包含 user_id 段：`/static/{user_id}/{version_name}/html/index.html`
- [ ] `peakrdl_html_service.py`、`incremental_update_service.py`、`module_code_generator.py` 等的路径均包含 user_id

## 7. 多用户目录隔离
- [ ] userA 上传生成的文件只存在于 `output/userA/`
- [ ] userB 的文件只存在于 `output/userB/`
- [ ] 用户之间无交叉污染（如 userA 目录下不应有 userB 的文件）

## 8. 上传事务性
- [ ] 上传成功后，文件存在于最终路径，DB 已更新
- [ ] 上传失败后（如格式错误），`backend/output/temp/` 下无残留目录
- [ ] 上传失败后，DB 中该版本的模块/寄存器数据未被污染
- [ ] 上传后端代码无 `UnboundLocalError` 等变量作用域错误

## 9. 前端
- [ ] `npm run build` 成功，无 TypeScript 错误
- [ ] 页面顶部有 User 输入框
- [ ] 输入用户名后显示对应 badge
- [ ] 输入 `admin` 后弹出密码框，密码 `askcp` 正确进入 admin 视图
- [ ] 版本列表显示 PUBLISHED/DRAFT badge
- [ ] 版本列表显示所有者（by userX）
- [ ] 版本列表可显示约 10 个版本（maxHeight 足够）
- [ ] 自己的未发布版本显示 **Publish** 和 **Delete** 按钮
- [ ] 已发布版本不显示修改/上传入口
- [ ] Delete 按钮点击后弹出确认对话框，显示版本名、所有者、创建时间、密码输入框
- [ ] 上传按钮在未选择版本、选择已发布版本、选择他人版本时均被禁用

## 10. 旧数据兼容性
- [ ] 如数据库为空，首次启动自动创建新表结构
- [ ] 不存在旧数据迁移问题（本系统选择直接重建）

---

## 测试结果记录格式

每项记录：
```
| 测试项 | PASS/FAIL | 备注 |
```

最终 verdict：
```
## Overall: PASS / FAIL
[未通过项列表]
```

FAIL 时，必须将问题反馈给 Modifier Agent 修复，修复后重新跑本清单。
