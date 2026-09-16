# XiaoAi-LLM-Router
# 构建: docker build -t xiaoai-llm-router .
# 运行: 见 docker-compose.yml（或 docker run + env 手动跑）
FROM python:3.11-slim

# 非 root 用户跑（密钥/对话数据不需要写系统目录）
RUN useradd -m -u 1000 router && chown -R router /app
WORKDIR /app
USER router

# 先装依赖（利用 Docker 层缓存：改代码不重装依赖）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码（v1.1 修正：v1.0 只挂 main.py 不挂模块文件 → 容器里 ModuleNotFoundError）
COPY main.py miservice_glue.py llm_router.py memory.py mi_tts.py ./

# 配置从卷或 env 注入，不在镜像里
CMD ["python", "main.py"]
