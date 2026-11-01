FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 TZ=Asia/Shanghai
WORKDIR /app
RUN useradd --system --uid 10001 monitor && mkdir /app/data && chown monitor:monitor /app/data
COPY monitor.py /app/monitor.py
USER monitor
CMD ["python", "monitor.py", "run"]
