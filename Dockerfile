# One image, two runtimes: Python 3.15.2 (base image) for the API, Python 3.9 (uv) for the
# engine workers (GPU-CFR ships cpython-39 .so files only). The API spawns one
# worker per GPU (HOLDEM_WORKER_MODE=spawn), so they share /dev/shm and the
# socket directory inside one container. Run with the NVIDIA runtime.
FROM python:3.15.2-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy UV_NO_CACHE=1 \
    NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin holdem
WORKDIR /opt/holdem-solver
COPY requirements/ requirements/
RUN uv python install 3.9 \
    && uv venv .venv-api --python /usr/local/bin/python3.15 \
    && uv pip install --python .venv-api/bin/python -r requirements/api.txt \
    && uv venv .venv-engine --python 3.9 \
    && uv pip install --python .venv-engine/bin/python -r requirements/engine.txt
COPY holdem_solver/ holdem_solver/
COPY third_party/GPU-CFR/ third_party/GPU-CFR/
COPY tests/ tests/
COPY pytest.ini ./
RUN mkdir -p /data /run/holdem-solver && chown holdem:holdem /data /run/holdem-solver \
    && chmod 700 /data /run/holdem-solver
USER holdem
ENV PYTHONPATH=/opt/holdem-solver PYTHONDONTWRITEBYTECODE=1 \
    HOLDEM_API_DATA_DIR=/data HOLDEM_SOLVER_RUNTIME_DIR=/run/holdem-solver \
    HOLDEM_API_HOST=0.0.0.0 HOLDEM_API_PORT=8000 HOLDEM_WORKER_MODE=spawn
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD ["/opt/holdem-solver/.venv-api/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["/opt/holdem-solver/.venv-api/bin/python", "-m", "holdem_solver.api"]
