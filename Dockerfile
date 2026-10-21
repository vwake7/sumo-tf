FROM hashicorp/terraform:1.16.5 AS terraform

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY --from=terraform /bin/terraform /usr/local/bin/terraform

WORKDIR /workspace

COPY pyproject.toml ./pyproject.toml
RUN python -m pip install --no-cache-dir 'pystackql==3.8.1' 'fastapi==0.119.0' 'uvicorn==0.37.0'

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
