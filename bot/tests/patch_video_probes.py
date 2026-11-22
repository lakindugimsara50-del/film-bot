with open('bot/services/video_service.py', 'r', encoding='utf-8') as f:
    s = f.read()

old_probe = '''    in_size = os.path.getsize(video_path) if os.path.exists(video_path) else 0
    duration = get_video_duration(video_path, ffmpeg_bin)
    in_w, in_h = get_video_resolution(video_path, ffmpeg_bin)'''

new_probe = '''    in_size = os.path.getsize(video_path) if os.path.exists(video_path) else 0
    duration = await asyncio.to_thread(get_video_duration, video_path, ffmpeg_bin)
    in_w, in_h = await asyncio.to_thread(get_video_resolution, video_path, ffmpeg_bin)'''

assert old_probe in s, "old_probe not found"
s = s.replace(old_probe, new_probe)

old_hw = '''    hw_enc = detect_hw_encoder(ffmpeg_bin)'''
new_hw = '''    hw_enc = await asyncio.to_thread(detect_hw_encoder, ffmpeg_bin)'''

assert old_hw in s, "old_hw not found"
s = s.replace(old_hw, new_hw)

old_intro = '''    intro_video = None
    if prepend_intro:
        intro_video = get_or_create_filmzilla_intro(in_w, in_h, fps=30, duration=5.0, ffmpeg_bin=ffmpeg_bin)
        if intro_video and not os.path.exists(intro_video):
            intro_video = None

    has_audio = bool(get_audio_codec(video_path, ffmpeg_bin))'''

new_intro = '''    intro_video = None
    if prepend_intro:
        intro_video = await asyncio.to_thread(get_or_create_filmzilla_intro, in_w, in_h, fps=30, duration=5.0, ffmpeg_bin=ffmpeg_bin)
        if intro_video and not os.path.exists(intro_video):
            intro_video = None

    has_audio = bool(await asyncio.to_thread(get_audio_codec, video_path, ffmpeg_bin))'''

assert old_intro in s, "old_intro not found"
s = s.replace(old_intro, new_intro)

with open('bot/services/video_service.py', 'w', encoding='utf-8') as f:
    f.write(s)
print('Successfully offloaded probe calls in cover_cinesubz_watermark')
