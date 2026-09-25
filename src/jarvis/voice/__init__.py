"""Voice I/O for Jarvis: speech-to-text and text-to-speech, both on the CPU so
the GPU stays dedicated to the LLM.

- :mod:`jarvis.voice.stt` - microphone audio -> text (faster-whisper)
- :mod:`jarvis.voice.tts` - text -> spoken audio (Kokoro)
- :mod:`jarvis.voice.session` - the "start listening" conversation loop
"""
