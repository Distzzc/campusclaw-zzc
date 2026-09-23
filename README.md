# CampusClaw

最小 Flask + SQLite 课堂材料服务，提供教师/学生登录、角色权限、班级隔离和教师材料入库。

## 本地启动

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="use-a-long-random-server-secret"
export DATABASE_PATH="$PWD/data/app.db"
export STORAGE_PATH="$PWD/data/materials"
python app.py
```

开发/测试预置数据只在非生产环境显式加载：

```bash
FLASK_APP=app.py flask seed-data
```

预置账号：`a_teacher`、`a_student`、`b_teacher`、`b_student`。预置密码仅供开发测试，不能用于生产。

## Docker Compose

复制 `.env.example` 并设置服务端 `SECRET_KEY`，然后执行：

```bash
cp .env.example .env
docker compose up --build
```

健康检查：

```bash
curl http://localhost:5000/health
```

应用通过 `/api/materials` 提供材料列表和教师上传接口；服务端依据认证会话中的班级过滤数据，客户端传入的班级标识不参与授权。

生产部署必须通过服务端环境变量注入随机 `SECRET_KEY`，启用 HTTPS，并设置 `SESSION_COOKIE_SECURE=1`。生产环境应使用安全的 cookie/SameSite 策略和反 CSRF 防护；不能把密钥放入前端、请求参数、表单或请求头，也不能启用预置账号。
