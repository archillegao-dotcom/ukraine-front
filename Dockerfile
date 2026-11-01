FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV UF_PORT=8765
EXPOSE 8765
# 数据缓存保存在 /app/data，可挂载卷持久化：docker run -p 8765:8765 -v uf-data:/app/data ukraine-front
CMD ["python", "-m", "ukraine_front.app", "--host", "0.0.0.0", "--no-browser"]
