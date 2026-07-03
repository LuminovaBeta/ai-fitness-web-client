部署：
```
#同步项目
uv sync

#执行数据库迁移
uv run manage.py makemigrations
uv run manage.py migrate

#安装 Node 依赖
pnpm install

#启动 Django 服务器
uv run manage.py runserver 0.0.0.0:8000
```


1. 后端验证采用 Token 认证（如JWT）时，后端成功验证身份后会返回一段加密字符串。触屏前端或手机 App 只需要将其存入本地存储（LocalStorage 或 App 原生安全存储），并在后续每次向后端发送 HTTP 请求时，在请求头中附带 Authorization: Bearer <Token> 即可

