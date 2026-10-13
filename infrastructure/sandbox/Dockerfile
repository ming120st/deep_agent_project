FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN useradd \
    --create-home \
    --shell /bin/bash \
    sandbox

WORKDIR /workspace

COPY requirements.txt /tmp/requirements.txt

RUN pip install \
    --no-cache-dir \
    -r /tmp/requirements.txt

RUN chown -R sandbox:sandbox /workspace

USER sandbox

CMD ["sleep", "infinity"]