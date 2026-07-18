FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY .streamlit ./.streamlit
COPY src ./src

EXPOSE 8080 8501

CMD ["uvicorn", "or_aws_fleet.api:app", "--host", "0.0.0.0", "--port", "8080"]
