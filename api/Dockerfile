FROM python:3.12-slim

WORKDIR /app

# curl is used by the Compose healthcheck; libpq-dev/gcc are build deps for asyncpg/psycopg wheels.
# libgl1/libglib2.0-0/libxcb1/libsm6/libxext6/libxrender1 are runtime shared
# libraries that RapidOCR's opencv-python (cv2) links at import - python:3.12-slim
# omits them, so without these the RapidOCR model-bake warm-up below fails with
# "libxcb.so.1: cannot open shared object file".
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl build-essential libpq-dev \
        libgl1 libglib2.0-0 libxcb1 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Tesseract escape hatch: RapidOCR is the default OCR backend and needs
# no system packages. If RapidOCR underperforms on real course-material scans,
# uncomment the RUN below AND set OCR_BACKEND=tesseract so app/ingestion/parsing.py
# selects the Tesseract backend. Present-but-inert: enabling Tesseract is an
# uncomment + rebuild, not a Dockerfile rewrite.
# RUN apt-get update \
#     && apt-get install -y --no-install-recommends tesseract-ocr libtesseract-dev \
#     && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./

# Install the CPU-only PyTorch build BEFORE the project so sentence-transformers
# and docling resolve against it instead of pulling the multi-gigabyte CUDA build
# (torch + nvidia-cu13 libs + triton). This project is CPU-only end to end
# (embeddings included; GPU is only ever the LLM),
# so the CUDA stack is pure bloat that also blows the build container's memory
# budget during the bge-m3 warm-up below.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir .

# Bake model weights at build time so ingestion has zero hidden runtime network
# dependency. HF_HOME points the
# Hugging Face cache at a baked-in image directory; the warm-up RUNs below
# construct each model so its weights download into that cache once and are
# exercised - a broken or renamed weight then fails THIS build, not the first
# ingestion job. HF_HUB_OFFLINE=1 is set only AFTER the warm-up succeeds so
# runtime loads are enforced-offline and fail fast + readable on a missing
# weight instead of silently hanging on a network fetch.
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('BAAI/bge-m3'); v = m.encode(['build-time warm-up'], normalize_embeddings=True); assert len(v[0]) == 1024, len(v[0])"
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"
# Docling's own layout-detection and table-structure models (distinct from
# the RapidOCR ONNX weights above, and from bge-m3) are HF-hosted and are
# NOT downloaded merely by importing rapidocr/sentence-transformers - they
# are lazily fetched on the FIRST DocumentConverter.convert() call. Without
# this explicit bake, that first call happens at ingestion runtime under
# HF_HUB_OFFLINE=1 and fails fast with LocalEntryNotFoundError instead of
# silently hanging (warming only the OCR/embedding models is not enough;
# Docling's layout/table models need it too).
#
# IMPORTANT: call each model class's own download_models() with the default
# local_dir=None (NOT docling.utils.model_downloader's top-level
# download_models(), which defaults local_dir to settings.cache_dir/"models"
# - a DIFFERENT path than what LayoutModel/TableStructureModel actually read
# at runtime when artifacts_path is None, i.e. every real pipeline run).
# local_dir=None resolves to the standard huggingface_hub cache under
# HF_HOME (set above), which is exactly where the runtime pipeline's own
# artifacts_path is None branch looks - baking anywhere else silently misses
# the cache and still hits the network at ingestion time.
RUN python -c "from docling.models.stages.layout.layout_model import LayoutModel; LayoutModel.download_models()"
RUN python -c "from docling.models.stages.table_structure.table_structure_model import TableStructureModel; TableStructureModel.download_models()"
ENV HF_HUB_OFFLINE=1
# RapidOCR ships its ONNX weights inside the installed package (no runtime
# download), so no separate offline flag is needed; the warm-up above still
# proves the models load at build time.

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
