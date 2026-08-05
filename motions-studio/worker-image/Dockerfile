# syntax=docker/dockerfile:1.7
# Image dựng sẵn cho pod GPU (MTC_PREBUILT=1). Xoá ~20-35 phút cài phần mềm mỗi lần dựng pod:
# apt/Node/torch/ComfyUI/custom-node làm ở CI một lần, pod chỉ symlink vào.
#
# Base PHẢI ship sshd và không được thoát: RunPod không tiêm sshd, image tự thoát là container
# restart vô hạn (docs/gpu-pod.md#runpod-gotchas §1). runpod/pytorch có /start.sh làm đúng việc đó.
#
# Bản trước base vastai/comfy — bỏ vì là image bên thứ ba và tên gây hiểu nhầm là dùng vast.ai
# (nhà cung cấp do GPU_PROVIDER quyết, không phải image). Nó KHÔNG va bẫy §1.
FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404

USER root
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MTC_PREBUILT_DIR=/opt/mtc-prebuilt

# Marker cuối cùng: setup/lib-feature.sh:504 `die` nếu thiếu file này. Đặt ở cuối image để một
# build hỏng giữa chừng KHÔNG bao giờ cho ra image có marker nhưng thiếu ruột.
RUN mkdir -p /opt/mtc-prebuilt && touch /opt/mtc-prebuilt/.ready
