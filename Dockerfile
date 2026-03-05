FROM python:3.13.1-slim-bookworm
WORKDIR /app
COPY ./requirements.txt /app/
RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt
COPY /verisend /app/verisend
COPY /alembic /app/alembic/
COPY alembic.ini /app/alembic.ini