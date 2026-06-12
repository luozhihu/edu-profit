# 培训学员财务管理

内部学员、缴费、退款和凭证管理系统。正式账务记录不可修改，只能作废重录。

## 本地启动

前置条件：`uv`。项目使用 Python 3.11，`uv` 会管理独立虚拟环境。

```bash
make doctor
make bootstrap
make dev
```

打开 <http://127.0.0.1:8000>。

开发登录：

- 用户名：`finance`
- 密码：`finance-dev`

开发账号仅用于本地环境，生产环境不得使用。

## 测试

```bash
make test-unit
make test-integration
make test-all
make lint
```

## 核心规则

- 实际收款 = 收款金额 - 返点金额 - 转培训成本金额。
- 手机号允许重复，身份证号标准化后唯一。
- 退款必须关联有效缴费，累计退款不能超过实际收款。
- 有有效退款的缴费不可作废。
- 正式记录不可编辑或删除。

完整实施范围与验收标准见 [MVP_IMPLEMENTATION_PLAN.md](./MVP_IMPLEMENTATION_PLAN.md)。

## 服务器一键部署

支持 Ubuntu/Debian 服务器。脚本会自动安装 Docker、生成生产密钥、启动应用，
并持久化 SQLite 数据库与附件。

```bash
chmod +x deploy.sh
./deploy.sh
```

首次执行时输入至少 12 位管理员密码。完成后访问：

```text
http://服务器IP:8000
```

可通过环境变量覆盖默认值：

```bash
ADMIN_USERNAME=finance APP_PORT=8080 MAX_UPLOAD_BYTES=20971520 ./deploy.sh
```

非交互部署必须提供管理员密码：

```bash
ADMIN_PASSWORD='replace-with-a-strong-password' ./deploy.sh
```

再次执行 `./deploy.sh` 会重新构建并更新应用，不会删除 SQLite 数据卷和附件卷。
生产配置保存在权限受限的 `.env.production`。公网使用时，应在应用前配置 HTTPS
反向代理，并仅开放所需端口。
