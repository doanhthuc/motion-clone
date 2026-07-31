import worker, os
out = "/tmp/oldv.mp3"
for f in (out, out + ".vix.wav"):
    if os.path.exists(f):
        os.remove(f)
# Giả lập node FE gửi giọng CŨ 'vi_VN-vais1000-medium' (cái UI đang hiện 'Nữ')
worker._tts("Xin chao, day la giong doc thu nghiem cho video quang cao.", out, "vi_VN-vais1000-medium")
# .vix.wav chỉ được tạo bởi _vixtts_tts → tồn tại = đã đi đường viXTTS clone (KHONG phai Piper)
print("mp3_exists:", os.path.exists(out), "| viXTTS_clone_used:", os.path.exists(out + ".vix.wav"))
