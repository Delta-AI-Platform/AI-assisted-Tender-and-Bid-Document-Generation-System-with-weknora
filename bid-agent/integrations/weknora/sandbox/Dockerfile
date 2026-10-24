FROM mirror.ccs.tencentyun.com/wechatopenai/weknora-sandbox@sha256:5e1cca768f35724a5bdee18e2191d37c0d4a3c78e703a5beaccc3fb4a204a5d9
RUN sed -i 's|http://deb.debian.org|https://mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::ForceIPv4=true -o Acquire::https::Timeout=20 update && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-calc poppler-utils fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
RUN NO_PROXY='*' no_proxy='*' pip install --no-cache-dir --index-url http://mirrors.tencentyun.com/pypi/simple --trusted-host mirrors.tencentyun.com python-docx pypdf lxml openpyxl pillow pymupdf defusedxml reportlab
ENV LANG=C.UTF-8
