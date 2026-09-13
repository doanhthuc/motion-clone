"""VACE workflow builder for accessory-only correction."""


def build_vace_workflow(video_name, mask_name, reference_name, *, frames, fps,
                        size, prefix, seed, prompt='wristwatch'):
    if frames < 5 or frames > 81 or (frames - 1) % 4:
        raise ValueError('VACE requires 4k+1 frames, from 5 to 81')
    wf = {
        '10': {'class_type': 'LoadImage', 'inputs': {'image': reference_name}},
        '12': {'class_type': 'VHS_LoadVideo', 'inputs': {
            'video': video_name, 'force_rate': fps, 'frame_load_cap': frames,
            'select_every_nth': 1, 'format': 'AnimateDiff'}},
        '13': {'class_type': 'VHS_LoadVideo', 'inputs': {
            'video': mask_name, 'force_rate': fps, 'frame_load_cap': frames,
            'select_every_nth': 1, 'format': 'AnimateDiff'}},
        '14': {'class_type': 'ImageToMask', 'inputs': {'image': ['13', 0], 'channel': 'red'}},
        '40': {'class_type': 'WanVideoVACEModelSelect', 'inputs': {
            'vace_model': 'Wan2_1-VACE_module_14B_fp8_e4m3fn.safetensors'}},
        '42': {'class_type': 'WanVideoModelLoader', 'inputs': {
            'model': 'Wan2_1-T2V-14B_fp8_e4m3fn.safetensors',
            'base_precision': 'bf16', 'quantization': 'disabled',
            'attention_mode': 'sdpa', 'block_swap_args': ['41', 0],
            'load_device': 'offload_device', 'extra_model': ['40', 0]}},
        '41': {'class_type': 'WanVideoBlockSwap', 'inputs': {
            'blocks_to_swap': 30, 'offload_img_emb': False, 'offload_txt_emb': False,
            'use_non_blocking': True, 'vace_blocks_to_swap': 8,
            'prefetch_blocks': 1, 'block_swap_debug': False}},
        '60': {'class_type': 'WanVideoTextEncodeCached', 'inputs': {
            'model_name': 'umt5-xxl-enc-bf16.safetensors', 'precision': 'bf16',
            'positive_prompt': f'The same {prompt} from the reference image. Preserve pose and lighting.',
            'negative_prompt': 'different accessory, changing design, flickering, deformed hands',
            'quantization': 'disabled', 'use_disk_cache': True, 'device': 'gpu'}},
        '81': {'class_type': 'WanVideoVACEEncode', 'inputs': {
            'vae': ['50', 0], 'input_frames': ['12', 0],
            'ref_images': ['10', 0], 'input_masks': ['14', 0],
            'width': size, 'height': size, 'num_frames': frames, 'strength': 1.0,
            'vace_start_percent': 0.0, 'vace_end_percent': 1.0, 'tiled_vae': True}},
        '90': {'class_type': 'WanVideoSampler', 'inputs': {
            'model': ['42', 0], 'image_embeds': ['81', 0], 'seed': seed,
            'steps': 20, 'cfg': 4.0, 'shift': 8.0, 'text_embeds': ['60', 0],
            'force_offload': True, 'scheduler': 'unipc', 'riflex_freq_index': 0,
            'rope_function': 'comfy'}},
        '100': {'class_type': 'WanVideoDecode', 'inputs': {'vae': ['50', 0], 'samples': ['90', 0],
            'enable_vae_tiling': True, 'tile_x': 256, 'tile_y': 256,
            'tile_stride_x': 128, 'tile_stride_y': 128, 'normalization': 'default'}},
        '110': {'class_type': 'VHS_VideoCombine', 'inputs': {
            'images': ['100', 0], 'frame_rate': fps, 'filename_prefix': prefix,
            'format': 'video/h264-mp4', 'pingpong': False, 'loop_count': 0, 'save_output': True}},
        '50': {'class_type': 'WanVideoVAELoader', 'inputs': {'model_name': 'Wan2_1_VAE_bf16.safetensors', 'precision': 'bf16'}},
    }
    for key in ('12', '13'):
        wf[key]['inputs'].update(custom_width=0, custom_height=0, skip_first_frames=0)
    return wf


def build_mask_workflow(video, *, frames, fps, prompt, prefix):
    return {
        '12': {'class_type': 'VHS_LoadVideo', 'inputs': {
            'video': video, 'force_rate': fps, 'custom_width': 0, 'custom_height': 0,
            'frame_load_cap': frames, 'skip_first_frames': 0, 'select_every_nth': 1, 'format': 'AnimateDiff'}},
        '200': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'sam3.1_multiplex_fp16.safetensors'}},
        '201': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['200', 1], 'text': prompt}},
        '202': {'class_type': 'SAM3_VideoTrack', 'inputs': {'images': ['12', 0], 'model': ['200', 0],
            'conditioning': ['201', 0], 'detection_threshold': 0.5, 'max_objects': 1, 'detect_interval': 1}},
        '203': {'class_type': 'SAM3_TrackToMask', 'inputs': {'track_data': ['202', 0], 'object_indices': ''}},
        '204': {'class_type': 'MaskToImage', 'inputs': {'mask': ['203', 0]}},
        '110': {'class_type': 'VHS_VideoCombine', 'inputs': {'images': ['204', 0], 'frame_rate': fps,
            'filename_prefix': prefix, 'format': 'video/h264-mp4', 'pingpong': False,
            'loop_count': 0, 'save_output': True}},
    }


def validate_capabilities(info, workflow):
    for graph_node in workflow.values():
        kind, values = graph_node['class_type'], graph_node['inputs']
        if kind not in info:
            raise RuntimeError(f'Missing ComfyUI node: {kind}')
        schema = info[kind].get('input', {})
        required = schema.get('required', {})
        fields = {**required, **schema.get('optional', {})}
        missing = set(required) - set(values)
        if missing:
            raise RuntimeError(f'Incompatible {kind}: missing inputs {sorted(missing)}')
        for field, value in values.items():
            if field not in fields:
                raise RuntimeError(f'Incompatible {kind}: unsupported input {field}')
            choices = fields[field][0]
            if isinstance(choices, list) and not isinstance(value, list) and value not in choices:
                raise RuntimeError(f'Missing model or unsupported {kind}.{field}: {value}')
