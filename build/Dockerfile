FROM vllm/vllm-openai@sha256:a551e05307cd2e0092139d84db32af9c97e67d2eeeff072d21e429131d8c23f0
COPY inputs/ /input/
COPY recipe/build/stage.py recipe/build/run_stage.sh /tempo/build/
RUN mkdir -p /receipts && python3 -m pip install --no-index --no-deps /input/cmake-3.31.6-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
RUN bash /tempo/build/run_stage.sh 1
RUN bash /tempo/build/run_stage.sh 2
RUN bash /tempo/build/run_stage.sh 3
RUN bash /tempo/build/run_stage.sh 4
RUN bash /tempo/build/run_stage.sh 5
COPY recipe/ /tempo/
ARG SOURCES_SHA256
LABEL jspark3.sources=$SOURCES_SHA256
RUN python3 /tempo/tools/apply_patches.py
ENTRYPOINT ["vllm", "serve"]
CMD []
