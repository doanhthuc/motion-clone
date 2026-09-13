"""VACE workflow builder for accessory-only correction."""


def build_vace_workflow(video_name, mask_name, reference_name, *, frames, fps,
                        size, prefix, seed):
    return {
        '10': {'class_type': 'LoadImage', 'inputs': {'image': reference_name}},
        '12': {'class_type': 'VHS_LoadVideo', 'inputs': {
            'video': video_name, 'force_rate': fps, 'frame_load_cap': frames,
            'select_every_nth': 1, 'format': 'AnimateDiff'}},
        '14': {'class_type': 'VHS_LoadVideo', 'inputs': {
            'video': mask_name, 'force_rate': fps, 'frame_load_cap': frames,
            'select_every_nth': 1, 'format': 'AnimateDiff'}},
        '40': {'class_type': 'WanVideoVACEModelSelect', 'inputs': {
            'vace_model': 'Wan2_1-VACE_module_14B_fp8_e4m3fn.safetensors'}},
        '42': {'class_type': 'WanVideoModelLoader', 'inputs': {
            'model': 'Wan2_1-T2V-14B_fp8_e4m3fn_scaled_KJ.safetensors',
            'base_precision': 'bf16', 'quantization': 'fp8_e4m3fn_scaled',
            'load_device': 'offload_device', 'extra_model': ['40', 0]}},
        '81': {'class_type': 'WanVideoVACEEncode', 'inputs': {
            'vae': ['50', 0], 'input_frames': ['12', 0],
            'ref_images': ['10', 0], 'input_masks': ['14', 0],
            'width': size, 'height': size, 'num_frames': frames}},
        '90': {'class_type': 'WanVideoSampler', 'inputs': {
            'model': ['42', 0], 'image_embeds': ['81', 0], 'seed': seed,
            'steps': 20, 'cfg': 4.0, 'shift': 8.0}},
        '100': {'class_type': 'WanVideoDecode', 'inputs': {'vae': ['50', 0], 'samples': ['90', 0]}},
        '110': {'class_type': 'VHS_VideoCombine', 'inputs': {
            'images': ['100', 0], 'frame_rate': fps, 'filename_prefix': prefix,
            'format': 'video/h264-mp4', 'pingpong': False, 'loop_count': 0}},
        '50': {'class_type': 'WanVideoVAELoader', 'inputs': {'model_name': 'Wan2_1_VAE_bf16.safetensors', 'precision': 'bf16'}},
    }


def validate_capabilities(info, workflow):
    required = {'WanVideoVACEEncode', 'WanVideoVACEModelSelect', 'WanVideoModelLoader'}
    missing = sorted(required - set(info.get('nodes', [])))
    if missing:
        raise RuntimeError('Missing ComfyUI VACE capabilities: ' + ', '.join(missing))
