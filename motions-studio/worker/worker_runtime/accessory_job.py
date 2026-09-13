"""Regional correction on the GPU pod, with bounded chunks and disk-backed frames."""
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageFilter

from .accessory_correction import (chunk_visible_intervals, composite_region,
                                   prepare_reference, visible_intervals)
from .accessory_workflow import (build_mask_workflow, build_vace_workflow,
                                validate_capabilities)


def command(*args):
    subprocess.run([str(x) for x in args], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE, timeout=600)


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                             '-show_streams', '-of', 'json', str(path)],
                            check=True, capture_output=True, text=True, timeout=60)
    stream = json.loads(result.stdout)['streams'][0]
    fps = Fraction(stream['r_frame_rate'])
    if fps <= 0 or fps > 60 or abs(float(fps) - float(Fraction(stream['avg_frame_rate']))) > 0.01:
        raise ValueError('Accessory correction requires constant frame rate video, at most 60 fps')
    return stream['width'], stream['height'], fps


def extract(path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    command('ffmpeg', '-nostdin', '-v', 'error', '-i', path, '-vsync', '0', directory / '%06d.png')
    return sorted(directory.glob('*.png'))


def encode(directory, destination, fps, audio=None):
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-framerate', str(fps),
            '-i', str(directory / '%06d.png')]
    if audio is not None:
        args += ['-i', str(audio), '-map', '0:v:0', '-map', '1:a?', '-c:a', 'copy']
    args += ['-c:v', 'libx264', '-crf', '0' if audio is None else '16',
             '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(destination)]
    command(*args)


def correction_options(params):
    defaults = {'accessoryPrompt': 'wristwatch', 'referenceBox': None,
                'accessoryMaskGrow': 6, 'accessoryChunkFrames': 49,
                'accessoryChunkOverlap': 8, 'seed': 42}
    unknown = set(params) - set(defaults)
    if unknown:
        raise ValueError(f'Unsupported accessory parameters: {sorted(unknown)}')
    p = {**defaults, **params}
    for key, low, high in [('accessoryMaskGrow', 0, 24), ('accessoryChunkFrames', 17, 81),
                           ('accessoryChunkOverlap', 1, 24), ('seed', 0, 2**32-1)]:
        if type(p[key]) is not int or not low <= p[key] <= high:
            raise ValueError(f'{key} must be an integer between {low} and {high}')
    if ((p['accessoryChunkFrames'] - 1) % 4
            or p['accessoryChunkOverlap'] >= p['accessoryChunkFrames'] // 2):
        raise ValueError('Chunk size must be 4k+1; overlap must be less than half a chunk')
    if not isinstance(p['accessoryPrompt'], str) or not p['accessoryPrompt'].strip():
        raise ValueError('accessoryPrompt must describe one accessory to track')
    return p


def correct_frames(frames, masks, reference_name, output_dir, render, *, options, fps,
                   check_cancel=lambda: None):
    """render(video, mask, ref, frame_count, prefix) -> corrected clip path.

    Pixels outside the binary mask remain identical until final H.264 encoding.
    Overlap blending limits seams but does not guarantee model identity fidelity.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    boxes, visible = [], []
    with Image.open(frames[0]) as first:
        size = first.size
    if len(masks) != len(frames):
        raise ValueError('Mask frame count must match the generated video exactly')
    mask_dir = output_dir.parent / 'normalized-masks'
    mask_dir.mkdir()
    normalized = []
    for index, path in enumerate(masks):
        check_cancel()
        with Image.open(path) as image:
            if image.size != size:
                raise ValueError('Mask resolution must match the generated video')
            mask = image.convert('L').point(lambda v: 255 if v >= 128 else 0)
        if options['accessoryMaskGrow']:
            mask = mask.filter(ImageFilter.MaxFilter(options['accessoryMaskGrow'] * 2 + 1))
        area = mask.histogram()[255]
        if area > size[0] * size[1] * 0.25:
            raise ValueError('Accessory mask covers over 25% of frame; refine accessoryPrompt or supply a mask')
        if area < 4:
            mask = Image.new('L', size)
        boxes.append(mask.getbbox())
        visible.append(mask.getbbox() is not None)
        dest = mask_dir / f'{index+1:06d}.png'
        mask.save(dest)
        normalized.append(dest)
    intervals = visible_intervals(visible)
    if not intervals:
        raise ValueError('No accessory detected; provide a clearer description or mask')
    chunks = chunk_visible_intervals(intervals, chunk_size=options['accessoryChunkFrames'],
                                     overlap=options['accessoryChunkOverlap'])
    for index, path in enumerate(frames):
        shutil.copyfile(path, output_dir / f'{index+1:06d}.png')
    previous_end = -1
    for number, (start, end) in enumerate(chunks):
        check_cancel()
        relevant = boxes[start:end]
        box = (max(0, min(b[0] for b in relevant)-24), max(0, min(b[1] for b in relevant)-24),
               min(size[0], max(b[2] for b in relevant)+24), min(size[1], max(b[3] for b in relevant)+24))
        directory = output_dir.parent / f'chunk-{number:03d}'
        video_dir, chunk_masks = directory / 'input', directory / 'masks'
        video_dir.mkdir(parents=True)
        chunk_masks.mkdir()
        count = end - start
        padded = max(5, ((count - 1 + 3) // 4) * 4 + 1)
        for local in range(padded):
            source_index = start + min(local, count-1)
            for source, target, method in [(frames[source_index], video_dir, Image.Resampling.LANCZOS),
                                           (normalized[source_index], chunk_masks, Image.Resampling.NEAREST)]:
                with Image.open(source) as image:
                    image.crop(box).resize((512, 512), method).convert('RGB').save(target / f'{local+1:06d}.png')
        video, mask_video = directory / 'input.mp4', directory / 'mask.mp4'
        encode(video_dir, video, fps)
        encode(chunk_masks, mask_video, fps)
        rendered = render(video, mask_video, reference_name, padded, f'accessory-{number:03d}')
        generated = extract(rendered, directory / 'rendered')
        if len(generated) != padded or probe(rendered) != (512, 512, fps):
            raise ValueError('VACE returned unexpected frame count, dimensions or frame rate')
        overlap = max(0, previous_end - start)
        for local in range(count):
            index = start + local
            with Image.open(frames[index]) as original, Image.open(generated[local]) as fixed, \
                    Image.open(normalized[index]) as mask:
                result = composite_region(original.convert('RGB'), fixed.convert('RGB'), mask, box)
            dest = output_dir / f'{index+1:06d}.png'
            if local < overlap:
                with Image.open(dest) as previous:
                    result = Image.blend(previous.convert('RGB'), result, (local+1)/(overlap+1))
            result.save(dest)
        previous_end = end
    return {'visible_intervals': intervals, 'chunks': chunks,
            'mask_coverage_frames': sum(visible), 'total_frames': len(frames)}


def run(job, runtime):
    p = correction_options(job.get('params') or {})
    inputs, job_id = job.get('inputs') or {}, job['id']
    if not inputs.get('video') or not inputs.get('reference'):
        raise ValueError('Accessory correction requires video and a source accessory reference image')
    work = Path(tempfile.mkdtemp(prefix=f'accessory-{job_id[:8]}-'))

    def cancel():
        if runtime.api_job_cancelled(job_id):
            raise RuntimeError('cancelled')

    runtime.api_progress(job_id, 0.02, 'Checking accessory reference')
    source = Path(runtime.api_download(inputs['reference'], str(work / 'source.png')))
    reference = work / 'reference.png'
    report = {'reference': prepare_reference(source, reference, p['referenceBox']),
              'options': p, 'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest()}
    response = runtime.requests.get(f'{runtime.COMFY_URL}/object_info', timeout=60)
    response.raise_for_status()
    info = response.json()
    graph = build_vace_workflow('probe.mp4', 'mask.mp4', 'ref.png', frames=49,
                                fps=30, size=512, prefix='probe', seed=p['seed'])
    validate_capabilities(info, graph)
    if not inputs.get('mask'):
        validate_capabilities(info, build_mask_workflow('probe.mp4', frames=81, fps=30,
                                                       prompt=p['accessoryPrompt'], prefix='mask'))
    video = Path(runtime.api_download(inputs['video'], str(work / 'video.mp4')))
    width, height, fps = probe(video)
    if max(width, height) > 1920 or width % 2 or height % 2:
        raise ValueError('Correct the native motion output before enhance (even dimensions, maximum edge 1920)')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'csv=p=0', str(video)], capture_output=True, text=True, check=True, timeout=60)
    duration = float(result.stdout)
    if not math.isfinite(duration) or not 0 < duration <= 31:
        raise ValueError('Experimental accessory correction accepts clips up to 30 seconds')
    frames = extract(video, work / 'source-frames')
    reference_name = runtime.comfy_upload(str(reference))
    runtime.api_preview(job_id, str(reference), 'Fixed accessory reference')

    def execute(graph, label):
        cancel()
        validate_capabilities(info, graph)
        outputs = runtime.comfy_poll(runtime.comfy_submit(graph), job_id, deadline_sec=3600, prog_step=label)
        result = runtime.comfy_fetch_output(outputs)
        if not result:
            raise RuntimeError('ComfyUI completed without a correction video')
        return result

    masks = []
    if inputs.get('mask'):
        mask_video = runtime.api_download(inputs['mask'], str(work / 'supplied-mask.mp4'))
        if probe(mask_video) != (width, height, fps):
            raise ValueError('Supplied mask must match native video resolution and frame rate')
        masks = extract(mask_video, work / 'mask-frames')
    else:
        for start in range(0, len(frames), 81):
            end = min(start + 81, len(frames))
            mask_input = work / f'track-input-{start}'
            mask_input.mkdir()
            for local, frame in enumerate(frames[start:end]):
                shutil.copyfile(frame, mask_input / f'{local+1:06d}.png')
            clip = work / f'track-{start}.mp4'
            encode(mask_input, clip, fps)
            graph = build_mask_workflow(runtime.comfy_upload(str(clip)), frames=end-start,
                                        fps=float(fps), prompt=p['accessoryPrompt'],
                                        prefix=f'accessory-mask-{job_id[:8]}-{start}')
            part = extract(execute(graph, 'Tracking accessory'), work / f'mask-{start}')
            if len(part) != end-start:
                raise ValueError('SAM3 mask frame count does not match input')
            masks.extend(part)
    runtime.api_preview(job_id, str(masks[0]), 'Accessory mask (white = edit); review tracking')
    runtime.api_progress(job_id, 0.3, 'Correcting all visible intervals')

    def render(clip, mask, ref, count, prefix):
        return execute(build_vace_workflow(runtime.comfy_upload(str(clip)),
                        runtime.comfy_upload(str(mask)), ref, frames=count, fps=float(fps), size=512,
                        prefix=f'{job_id[:8]}-{prefix}', seed=p['seed'], prompt=p['accessoryPrompt']),
                       'VACE regional correction')

    report.update(correct_frames(frames, masks, reference_name, work / 'composited', render,
                                 options=p, fps=fps, check_cancel=cancel))
    (work / 'report.json').write_text(json.dumps(report, indent=2))
    runtime.api_log(job_id, f'Accessory correction report: {work / "report.json"}; '
                    f'{len(report["chunks"])} chunks, {report["mask_coverage_frames"]} visible frames', 'info')
    cancel()
    runtime.api_progress(job_id, 0.95, 'Encoding corrected video with original audio')
    output = work / 'corrected.mp4'
    encode(work / 'composited', output, fps, audio=video)
    runtime.api_upload_output(job_id, str(output), content_type='video/mp4')
